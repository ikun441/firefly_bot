"""审计日志与「建议采纳率」接口 —— 论文核心量化指标的数据出口。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..services import audit as audit_service
from .deps import fail, get_or_create_user, ok, parse_dt, to_dict

router = APIRouter(tags=["audit"])


class ResolveIn(BaseModel):
    adopted: bool
    channel: str = "web"


@router.get("/audit/logs")
async def list_logs(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    action: Optional[str] = Query(default=None),
    channel: Optional[str] = Query(default=None),
    target_type: Optional[str] = Query(default=None),
    target_id: Optional[int] = Query(default=None),
) -> Dict[str, Any]:
    """审计日志（谁在什么时候录了什么、AI 给了什么建议）。"""
    user = await get_or_create_user(session)

    logs = await audit_service.list_audit_logs(
        session, user_id=user.id, limit=limit, offset=offset, action=action, channel=channel, target_type=target_type, target_id=target_id
    )
    total = await audit_service.count_audit_logs(
        session, user_id=user.id, action=action, channel=channel, target_type=target_type, target_id=target_id
    )

    return ok([to_dict(log) for log in logs], total=total, limit=limit, offset=offset)


@router.get("/audit/stats")
async def adoption_stats(
    session: AsyncSession = Depends(get_session),
    since: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    """建议采纳率统计 —— 论文对照组实验的核心指标。

    ``adoption_rate`` 的分母只计入「用户明确采纳或拒绝」的样本；
    超时未处理的建议计入 ``ignored`` 单独披露，避免指标注水。
    """
    user = await get_or_create_user(session)
    stats_data = await audit_service.get_adoption_stats(
        session, user_id=user.id, since=parse_dt(since)
    )
    return ok(stats_data)


@router.get("/audit/suggestions")
async def list_suggestions(
    session: AsyncSession = Depends(get_session),
    state: Optional[str] = Query(default=None, description="pending/adopted/rejected/ignored"),
    kind: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> Dict[str, Any]:
    """建议列表（用于前端「AI 建议」面板与采纳/拒绝交互）。"""
    from sqlmodel import select

    from ..db.models import Suggestion

    user = await get_or_create_user(session)

    statement = select(Suggestion).where(Suggestion.user_id == user.id)
    if state:
        statement = statement.where(Suggestion.state == state)
    if kind:
        statement = statement.where(Suggestion.kind == kind)
    statement = statement.order_by(Suggestion.created_at.desc()).limit(limit)  # type: ignore[attr-defined]

    rows = (await session.execute(statement)).scalars().all()

    result = []
    for row in rows:
        item = to_dict(row)
        item["kind"] = row.kind.value if hasattr(row.kind, "value") else str(row.kind)
        item["state"] = row.state.value if hasattr(row.state, "value") else str(row.state)
        result.append(item)

    return ok(result, total=len(result))


@router.post("/audit/suggestions/{suggestion_id}/resolve")
async def resolve_suggestion(
    suggestion_id: str,
    payload: ResolveIn,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """用户采纳 / 拒绝一条 AI 建议 —— 采纳率指标的唯一样本写入点。"""
    user = await get_or_create_user(session)

    suggestion = await audit_service.resolve_suggestion(
        session, suggestion_id, adopted=payload.adopted, channel=payload.channel
    )
    if suggestion is None:
        return fail("建议不存在", code=404)

    return ok(to_dict(suggestion))
