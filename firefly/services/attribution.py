"""流萤 Firefly · 归因分析服务

把「这个月涨了」翻译成「因为哪几个类别各涨了多少」。
核心产出是瀑布图数据：从期初基数出发，逐项累加各类别的变动，
最终得到期末金额。这是财务分析里最有说服力的表达方式。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.models import TransactionDirection
from .ledger import aggregate_by_category

# 归因贡献的显著性阈值：占比低于此值的类别合并为「其他」
NEGLIGIBLE_RATIO = 2.0


async def build_attribution(
    session: AsyncSession,
    *,
    user_id: int,
    current_start: datetime,
    current_end: datetime,
    previous_start: datetime,
    previous_end: datetime,
) -> Dict[str, Any]:
    """构建支出变动的归因分析

    Returns:
        含瀑布图数据、主要驱动因子与文字结论的字典
    """
    current = await aggregate_by_category(
        session, user_id=user_id, start=current_start, end=current_end,
        direction=TransactionDirection.EXPENSE,
    )
    previous = await aggregate_by_category(
        session, user_id=user_id, start=previous_start, end=previous_end,
        direction=TransactionDirection.EXPENSE,
    )

    current_map = {item["name"]: item for item in current}
    previous_map = {item["name"]: item for item in previous}
    all_names = set(current_map) | set(previous_map)

    current_total = sum(item["amount"] for item in current)
    previous_total = sum(item["amount"] for item in previous)
    delta_total = current_total - previous_total

    # 计算每个类别的变动贡献
    contributions: List[Dict[str, Any]] = []
    for name in all_names:
        cur = current_map.get(name, {})
        prev = previous_map.get(name, {})
        cur_amount = float(cur.get("amount", 0.0))
        prev_amount = float(prev.get("amount", 0.0))
        delta = cur_amount - prev_amount

        contributions.append(
            {
                "name": name,
                "icon": cur.get("icon") or prev.get("icon") or "📦",
                "color": cur.get("color") or prev.get("color") or "#94a3b8",
                "current": round(cur_amount, 2),
                "previous": round(prev_amount, 2),
                "delta": round(delta, 2),
                # 变动幅度（百分比）：上期为 0 时无意义，标记为新增
                "delta_ratio": round(delta / prev_amount * 100, 2) if prev_amount else None,
                "is_new": prev_amount == 0 and cur_amount > 0,
                "is_gone": cur_amount == 0 and prev_amount > 0,
                "contribution_ratio": round(
                    abs(delta) / abs(delta_total) * 100, 2
                ) if delta_total else 0.0,
            }
        )

    # 按变动绝对值排序，抓住主要矛盾
    contributions.sort(key=lambda x: abs(x["delta"]), reverse=True)

    # 构建瀑布图数据：期初 -> 各因子 -> 期末
    waterfall: List[Dict[str, Any]] = [
        {
            "name": "上期支出",
            "value": round(previous_total, 2),
            "type": "total",
            "color": "#64748b",
        }
    ]

    significant: List[Dict[str, Any]] = []
    other_delta = 0.0
    for item in contributions:
        if abs(item["delta"]) < 0.01:
            continue
        if item["contribution_ratio"] < NEGLIGIBLE_RATIO:
            other_delta += item["delta"]
            continue
        significant.append(item)

    for item in significant:
        waterfall.append(
            {
                "name": item["name"],
                "value": item["delta"],
                "type": "increase" if item["delta"] > 0 else "decrease",
                # 涨用暖色、跌用冷色，符合"支出增加要警惕"的直觉
                "color": "#f87171" if item["delta"] > 0 else "#4ade80",
                "current": item["current"],
                "previous": item["previous"],
            }
        )

    if abs(other_delta) >= 0.01:
        waterfall.append(
            {
                "name": "其他类别合计",
                "value": round(other_delta, 2),
                "type": "increase" if other_delta > 0 else "decrease",
                "color": "#f87171" if other_delta > 0 else "#4ade80",
            }
        )

    waterfall.append(
        {
            "name": "本期支出",
            "value": round(current_total, 2),
            "type": "total",
            "color": "#38bdf8",
        }
    )

    # 生成文字结论
    change_ratio = (delta_total / previous_total * 100) if previous_total else None
    # 上期为 0 时环比无意义，单独措辞，避免把 None 塞进 format 里
    ratio_text = f"（{change_ratio:+.1f}%）" if change_ratio is not None else "（上期无支出记录）"
    if delta_total > 0:
        top = significant[0] if significant else None
        if top:
            summary = (
                f"本期支出较上期增加 {delta_total:,.2f} 元"
                f"{ratio_text}，主要来自「{top['name']}」"
                f"增加 {top['delta']:,.2f} 元。"
            )
        else:
            summary = f"本期支出较上期增加 {delta_total:,.2f} 元，各主要类别变动均不显著。"
    elif delta_total < 0:
        top = significant[0] if significant else None
        if top and top["delta"] < 0:
            summary = (
                f"本期支出较上期减少 {abs(delta_total):,.2f} 元"
                f"{ratio_text}，主要得益于「{top['name']}」"
                f"减少 {abs(top['delta']):,.2f} 元。"
            )
        else:
            summary = f"本期支出较上期减少 {abs(delta_total):,.2f} 元。"
    else:
        summary = "本期支出与上期基本持平。"

    return {
        "waterfall": waterfall,
        "contributions": contributions,
        "significant": significant,
        "current_total": round(current_total, 2),
        "previous_total": round(previous_total, 2),
        "delta_total": round(delta_total, 2),
        "change_ratio": round(change_ratio, 2) if change_ratio is not None else None,
        "summary": summary,
    }


async def build_trend(
    session: AsyncSession,
    *,
    user_id: int,
    months: int = 6,
    reference: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """构建近 N 个月的收支趋势，用于长图与季度对比"""
    from .ledger import period_bounds, sum_by_direction

    ref = reference or datetime.now()
    points: List[Dict[str, Any]] = []

    for offset in range(months - 1, -1, -1):
        # 从当前月往前推 offset 个月
        year = ref.year
        month = ref.month - offset
        while month <= 0:
            month += 12
            year -= 1

        anchor = ref.replace(year=year, month=month, day=15)
        start, end = period_bounds("monthly", reference=anchor)
        totals = await sum_by_direction(session, user_id=user_id, start=start, end=end)

        points.append(
            {
                "label": f"{month}月",
                "year": year,
                "month": month,
                "income": round(totals["income"], 2),
                "expense": round(totals["expense"], 2),
                "net": round(totals["net"], 2),
            }
        )

    return points


__all__ = ["build_attribution", "build_trend", "NEGLIGIBLE_RATIO"]
