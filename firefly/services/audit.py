"""流萤 Firefly · 审计日志服务

论文「建议采纳率」指标的唯一样本来源。
所有涉及 AI 建议的读写路径都必须经过本模块埋点。

设计原则：
    * 写入失败不得影响主流程（审计是旁路，不是关键路径）
    * 每条 AI 建议产生 Suggestion 记录，用户处理后更新其 state
    * 采纳率 = adopted / (adopted + rejected + ignored)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.models import (
    AdoptionState,
    AuditLog,
    Suggestion,
    SuggestionKind,
)


async def log_action(
    session: AsyncSession,
    action: str,
    *,
    user_id: Optional[int] = None,
    target_type: str = "",
    target_id: Optional[int] = None,
    operator: str = "user",
    detail: str = "",
    extra: Optional[Dict[str, Any]] = None,
    channel: str = "web",
) -> None:
    """记录一条审计日志

    调用方通常不关心返回值，且不应因审计失败而中断业务。
    """
    entry = AuditLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        operator=operator,
        detail=detail[:1024] if detail else "",
        extra=extra or {},
        channel=channel,
    )
    session.add(entry)


async def record_suggestion(
    session: AsyncSession,
    *,
    user_id: int,
    kind: SuggestionKind,
    content: str,
    target_type: str = "",
    target_id: Optional[int] = None,
    payload: Optional[Dict[str, Any]] = None,
    confidence: float = 0.0,
    reason: str = "",
) -> Suggestion:
    """登记一条 AI 建议（初始为待处理状态）

    注意：``content`` 与 ``reason`` 都要做空值保护。默认参数
    ``reason=""`` 只在该参数**未被传入**时生效；调用方显式传
    ``reason=None``（例如分类结果里没有理由时）会直接覆盖掉默认值，
    随后 ``None[:512]`` 抛 TypeError。这种错误只在特定数据上触发，
    很容易漏测。
    """
    item = Suggestion(
        user_id=user_id,
        kind=kind,
        target_type=target_type or "",
        target_id=target_id,
        content=(content or "")[:1024],
        payload=payload or {},
        confidence=confidence or 0.0,
        reason=(reason or "")[:512],
    )
    session.add(item)
    await session.flush()
    return item


async def resolve_suggestion(
    session: AsyncSession,
    suggestion_id: int,
    adopted: bool,
    *,
    channel: str = "web",
) -> Optional[Suggestion]:
    """标记建议为已采纳/已拒绝，并写入审计日志

    这是「建议采纳率」指标的唯一写入点。
    """
    stmt = select(Suggestion).where(Suggestion.id == suggestion_id)
    result = await session.exec(stmt)
    item = result.first()
    if item is None:
        return None

    item.state = AdoptionState.ADOPTED if adopted else AdoptionState.REJECTED
    item.resolved_at = datetime.now(timezone.utc)
    session.add(item)

    await log_action(
        session,
        "adopt_suggestion" if adopted else "reject_suggestion",
        user_id=item.user_id,
        target_type="suggestion",
        target_id=item.id,
        operator="user",
        detail=f"{item.kind.value}: {item.content[:80]}",
        extra={"confidence": item.confidence},
        channel=channel,
    )
    return item


async def expire_stale_suggestions(
    session: AsyncSession,
    *,
    hours: int = 72,
) -> int:
    """把超时未处理的建议标记为 ignored

    不这么做的话，「采纳率」的分母只包含用户主动处理的样本，
    会高估真实采纳率。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = select(Suggestion).where(
        Suggestion.state == AdoptionState.PENDING,
        Suggestion.created_at < cutoff,
    )
    result = await session.exec(stmt)
    items = result.all()
    for item in items:
        item.state = AdoptionState.IGNORED
        item.resolved_at = datetime.now(timezone.utc)
        session.add(item)
    return len(items)


async def get_adoption_stats(
    session: AsyncSession,
    *,
    user_id: Optional[int] = None,
    since: Optional[datetime] = None,
) -> Dict[str, Any]:
    """统计建议采纳率

    Returns:
        含总体采纳率与分类别采纳率的字典，直接作为论文实验数据。
    """
    conditions = []
    if user_id is not None:
        conditions.append(Suggestion.user_id == user_id)
    if since is not None:
        conditions.append(Suggestion.created_at >= since)

    stmt = select(Suggestion)
    for cond in conditions:
        stmt = stmt.where(cond)
    result = await session.exec(stmt)
    items = result.all()

    total = len(items)
    by_state: Dict[str, int] = {state.value: 0 for state in AdoptionState}
    by_kind: Dict[str, Dict[str, Any]] = {}

    for item in items:
        by_state[item.state.value] = by_state.get(item.state.value, 0) + 1

        kind_key = item.kind.value
        bucket = by_kind.setdefault(
            kind_key, {"total": 0, "adopted": 0, "rejected": 0, "pending": 0, "ignored": 0}
        )
        bucket["total"] += 1
        bucket[item.state.value] = bucket.get(item.state.value, 0) + 1

    # 采纳率分母只计入用户已明确处理（采纳/拒绝）的样本，
    # ignored 视为「未产生有效反馈」，单独披露避免指标注水
    decided = by_state.get(AdoptionState.ADOPTED.value, 0) + by_state.get(
        AdoptionState.REJECTED.value, 0
    )
    adopted = by_state.get(AdoptionState.ADOPTED.value, 0)

    for kind_key, bucket in by_kind.items():
        kind_decided = bucket["adopted"] + bucket["rejected"]
        bucket["adoption_rate"] = round(
            bucket["adopted"] / kind_decided * 100, 2
        ) if kind_decided else 0.0

    return {
        "total": total,
        "by_state": by_state,
        "by_kind": by_kind,
        "adoption_rate": round(adopted / decided * 100, 2) if decided else 0.0,
        "decided_count": decided,
        "pending_count": by_state.get(AdoptionState.PENDING.value, 0),
        "ignored_count": by_state.get(AdoptionState.IGNORED.value, 0),
    }


async def list_audit_logs(
    session: AsyncSession,
    *,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    channel: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[AuditLog]:
    """分页查询审计日志。

    ``channel`` 用于区分「网页操作」与「协议端操作」——
    答辩演示「谁录的账、从哪录的」时需要这个维度。
    """
    stmt = select(AuditLog)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if channel:
        stmt = stmt.where(AuditLog.channel == channel)
    stmt = stmt.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
    result = await session.exec(stmt)
    return list(result.all())


async def count_audit_logs(
    session: AsyncSession,
    *,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    channel: Optional[str] = None,
) -> int:
    """统计审计日志条数"""
    stmt = select(func.count(AuditLog.id))
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if channel:
        stmt = stmt.where(AuditLog.channel == channel)
    result = await session.exec(stmt)
    return int(result.one() or 0)


__all__ = [
    "log_action",
    "record_suggestion",
    "resolve_suggestion",
    "expire_stale_suggestions",
    "get_adoption_stats",
    "list_audit_logs",
    "count_audit_logs",
]
