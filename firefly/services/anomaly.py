"""流萤 Firefly · 异常检测服务

检测三类问题（按税务风险从高到低）：
    1. 疑似个人消费混入公司支出 —— 实打实的税务风险点
    2. 重复记账 —— 金额相同、时间接近
    3. 单笔金额远超历史 —— z-score / Isolation Forest

设计原则：**可解释性优先**。
每个异常都必须能说清「为什么判定为异常」，否则用户不会信任，
论文里也无法做案例分析。因此默认使用 z-score 而非纯黑盒模型。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import get_config
from ..db.models import (
    Category,
    SuggestionKind,
    Transaction,
    TransactionDirection,
)
from . import audit

# 个人消费常见关键词：命中后提示「可能混入公司支出」
PERSONAL_KEYWORDS: Tuple[str, ...] = (
    "奶茶", "电影", "游戏", "健身", "美甲", "理发", "零食", "咖啡",
    "服装", "化妆品", "玩具", "宠物", "旅游", "KTV", "酒吧", "演唱会",
    "超市", "菜市场", "外卖", "会员", "视频", "音乐", "手办",
)

# 明显属于经营支出的关键词，命中后降低个人消费嫌疑
BUSINESS_KEYWORDS: Tuple[str, ...] = (
    "客户", "招待", "差旅", "办公", "发票", "报销", "会议", "差费",
    "住宿", "机票", "高铁", "打印", "快递", "税", "社保", "工资",
)


# ------------------------------------------------------------------------------
# 统计工具
# ------------------------------------------------------------------------------
def _mean_std(values: List[float]) -> Tuple[float, float]:
    """计算均值与样本标准差（无偏）"""
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(variance)


def compute_zscore(value: float, mean: float, std: float) -> float:
    """计算 z-score，标准差为 0 时返回 0（避免除零）"""
    if std <= 0:
        return 0.0
    return (value - mean) / std


# ------------------------------------------------------------------------------
# 单条流水的异常判定
# ------------------------------------------------------------------------------
def detect_personal_mixed(txn: Transaction, category_name: str = "") -> Tuple[bool, str]:
    """判定是否为「疑似个人消费混入公司支出」

    采用关键词加权打分，命中个人词加分、命中经营词减分，
    最终由配置的阈值裁决。可解释性强，便于在报告中展示依据。
    """
    text = f"{txn.counterparty} {txn.memo} {category_name}".lower()
    if not text.strip():
        return False, ""

    personal_hits = [kw for kw in PERSONAL_KEYWORDS if kw in text]
    business_hits = [kw for kw in BUSINESS_KEYWORDS if kw in text]

    if not personal_hits:
        return False, ""

    score = 0.8 * len(personal_hits) - 0.6 * len(business_hits)
    if score < 0.5:
        return False, ""

    hits_text = "、".join(personal_hits[:3])
    return True, f"备注/对手方包含个人消费特征词「{hits_text}」，但缺乏对应经营凭证"


async def detect_amount_anomaly(
    session: AsyncSession,
    *,
    user_id: int,
    txn: Transaction,
    lookback_days: int = 90,
) -> Tuple[bool, str]:
    """基于同类别的历史分布判定「金额异常」

    只与**同类别**历史比较，而不是全局比较。
    否则「房租 5000」会被判异常，而「餐饮 800」反而正常，与直觉相悖。
    """
    config = get_config()
    if not config.anomaly.enabled:
        return False, ""

    since = txn.trade_time - timedelta(days=lookback_days)
    stmt = select(Transaction.amount).where(
        Transaction.user_id == user_id,
        Transaction.direction == txn.direction,
        Transaction.trade_time >= since,
        Transaction.trade_time < txn.trade_time,
        Transaction.id != txn.id,
    )
    if txn.category_id is not None:
        stmt = stmt.where(Transaction.category_id == txn.category_id)

    result = await session.exec(stmt)
    history = [float(a) for a in result.all()]

    if len(history) < config.anomaly.min_samples:
        return False, ""

    mean, std = _mean_std(history)
    z = compute_zscore(float(txn.amount), mean, std)
    if abs(z) < config.anomaly.zscore_threshold:
        return False, ""

    return True, (
        f"该笔 {txn.amount:.2f} 元显著偏离同类历史均值 {mean:.2f} 元"
        f"（标准差 {std:.2f}，z-score={z:.2f}，阈值 {config.anomaly.zscore_threshold}）"
    )


async def analyze_transaction(
    session: AsyncSession,
    *,
    user_id: int,
    txn: Transaction,
) -> Dict[str, Any]:
    """对单条流水做完整异常分析

    返回结构化结果并回写流水的异常标记字段。
    """
    reasons: List[str] = []

    # 1. 个人消费混入
    category_name = ""
    if txn.category_id is not None:
        cat_stmt = select(Category.name).where(Category.id == txn.category_id)
        cat_result = await session.exec(cat_stmt)
        category_name = cat_result.first() or ""

    is_personal, personal_reason = detect_personal_mixed(txn, category_name)
    if is_personal:
        reasons.append(personal_reason)

    # 2. 金额异常
    is_amount_anomaly, amount_reason = await detect_amount_anomaly(
        session, user_id=user_id, txn=txn
    )
    if is_amount_anomaly:
        reasons.append(amount_reason)

    # 3. 大额阈值（不依赖历史样本，冷启动即生效）
    config = get_config()
    if float(txn.amount) >= config.ledger.large_amount_threshold:
        reasons.append(
            f"单笔金额 {txn.amount:.2f} 元超过大额阈值 "
            f"{config.ledger.large_amount_threshold:.2f} 元，建议核对凭证"
        )

    is_anomaly = bool(reasons)
    combined_reason = "；".join(reasons)[:256]

    txn.is_anomaly = is_anomaly
    txn.anomaly_reason = combined_reason
    txn.is_personal_mixed = is_personal
    session.add(txn)

    return {
        "transaction_id": txn.id,
        "is_anomaly": is_anomaly,
        "is_personal_mixed": is_personal,
        "reasons": reasons,
        "reason_text": combined_reason,
    }


# ------------------------------------------------------------------------------
# 批量扫描
# ------------------------------------------------------------------------------
async def scan_period(
    session: AsyncSession,
    *,
    user_id: int,
    start: datetime,
    end: datetime,
    create_suggestions: bool = True,
) -> Dict[str, Any]:
    """扫描一个周期内的全部流水，产出异常清单

    Args:
        create_suggestions: 是否为异常创建 AI 建议记录（供采纳率统计）
    """
    stmt = select(Transaction).where(
        Transaction.user_id == user_id,
        Transaction.trade_time >= start,
        Transaction.trade_time < end,
        Transaction.direction == TransactionDirection.EXPENSE,
    ).order_by(Transaction.trade_time.asc())
    result = await session.exec(stmt)
    transactions = list(result.all())

    anomalies: List[Dict[str, Any]] = []
    personal_mixed: List[Dict[str, Any]] = []

    for txn in transactions:
        analysis = await analyze_transaction(session, user_id=user_id, txn=txn)
        if not analysis["is_anomaly"]:
            continue

        entry = {
            "id": txn.id,
            "amount": round(float(txn.amount), 2),
            "trade_time": txn.trade_time.isoformat(),
            "memo": txn.memo,
            "counterparty": txn.counterparty,
            "reasons": analysis["reasons"],
            "is_personal_mixed": analysis["is_personal_mixed"],
        }
        anomalies.append(entry)
        if analysis["is_personal_mixed"]:
            personal_mixed.append(entry)

        if create_suggestions:
            await audit.record_suggestion(
                session,
                user_id=user_id,
                kind=SuggestionKind.ANOMALY,
                content=f"「{txn.memo or txn.counterparty or '一笔支出'}」"
                f"{txn.amount:.2f} 元存在异常：{analysis['reason_text'][:100]}",
                target_type="transaction",
                target_id=txn.id,
                payload={"reasons": analysis["reasons"]},
                confidence=0.7,
                reason=analysis["reason_text"],
            )

    total_expense = sum(float(t.amount) for t in transactions)
    anomaly_amount = sum(a["amount"] for a in anomalies)

    return {
        "scanned_count": len(transactions),
        "anomaly_count": len(anomalies),
        "personal_mixed_count": len(personal_mixed),
        "anomalies": anomalies,
        "personal_mixed": personal_mixed,
        "anomaly_amount": round(anomaly_amount, 2),
        "anomaly_amount_ratio": round(
            anomaly_amount / total_expense * 100, 2
        ) if total_expense else 0.0,
    }


async def scan_duplicates(
    session: AsyncSession,
    *,
    user_id: int,
    start: datetime,
    end: datetime,
) -> List[Dict[str, Any]]:
    """扫描周期内的疑似重复记账

    使用金额分组 + 时间邻近判定，复杂度 O(n log n)。
    """
    config = get_config()
    window = timedelta(minutes=config.ledger.duplicate_window_minutes)

    stmt = select(Transaction).where(
        Transaction.user_id == user_id,
        Transaction.trade_time >= start,
        Transaction.trade_time < end,
    ).order_by(Transaction.amount, Transaction.trade_time)
    result = await session.exec(stmt)
    items = list(result.all())

    groups: List[Dict[str, Any]] = []
    idx = 0
    while idx < len(items):
        current = items[idx]
        cluster = [current]
        j = idx + 1
        while j < len(items) and abs(items[j].amount - current.amount) < 0.01:
            if items[j].trade_time - cluster[-1].trade_time <= window:
                cluster.append(items[j])
                j += 1
            else:
                break
        if len(cluster) > 1:
            groups.append(
                {
                    "amount": round(float(current.amount), 2),
                    "count": len(cluster),
                    "transaction_ids": [c.id for c in cluster],
                    "times": [c.trade_time.isoformat() for c in cluster],
                    "memos": [c.memo for c in cluster],
                    "message": (
                        f"{len(cluster)} 笔金额均为 {current.amount:.2f} 元的支出"
                        f"发生在 {window.total_seconds() / 60:.0f} 分钟内，疑似重复记账"
                    ),
                }
            )
            idx = j
        else:
            idx += 1

    return groups


async def get_anomaly_stats(
    session: AsyncSession,
    *,
    user_id: int,
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    """异常统计汇总，供报告与前端卡片使用"""
    total_stmt = select(func.count(Transaction.id)).where(
        Transaction.user_id == user_id,
        Transaction.trade_time >= start,
        Transaction.trade_time < end,
        Transaction.direction == TransactionDirection.EXPENSE,
    )
    total_result = await session.exec(total_stmt)
    total_count = int(total_result.one() or 0)

    anomaly_stmt = select(func.count(Transaction.id)).where(
        Transaction.user_id == user_id,
        Transaction.trade_time >= start,
        Transaction.trade_time < end,
        Transaction.is_anomaly == True,  # noqa: E712
    )
    anomaly_result = await session.exec(anomaly_stmt)
    anomaly_count = int(anomaly_result.one() or 0)

    personal_stmt = select(func.count(Transaction.id)).where(
        Transaction.user_id == user_id,
        Transaction.trade_time >= start,
        Transaction.trade_time < end,
        Transaction.is_personal_mixed == True,  # noqa: E712
    )
    personal_result = await session.exec(personal_stmt)
    personal_count = int(personal_result.one() or 0)

    return {
        "total_count": total_count,
        "anomaly_count": anomaly_count,
        "personal_mixed_count": personal_count,
        "anomaly_ratio": round(anomaly_count / total_count * 100, 2) if total_count else 0.0,
    }


__all__ = [
    "PERSONAL_KEYWORDS",
    "BUSINESS_KEYWORDS",
    "compute_zscore",
    "detect_personal_mixed",
    "detect_amount_anomaly",
    "analyze_transaction",
    "scan_period",
    "scan_duplicates",
    "get_anomaly_stats",
]
