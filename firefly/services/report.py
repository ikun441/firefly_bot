"""流萤 Firefly · 报告编排服务（P0 核心）

产出两大核心模块：
    一、财务税务整体状况 —— 收支总览、结构、趋势、异常、预算执行
    二、财务税务优化方向 —— 税检结论、归因驱动、可执行建议

关键设计：本服务产出的 payload 是**唯一数据源**，
前端动画渲染与 htmlrender 静态出图都消费它，避免两套逻辑不一致。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import get_config
from ..db.models import (
    Report,
    ReportStatus,
    TransactionDirection,
)
from . import anomaly, audit, budget as budget_service, taxcheck
from .attribution import build_attribution, build_trend
from .ledger import (
    aggregate_by_category,
    aggregate_by_counterparty,
    aggregate_by_day,
    count_transactions,
    format_period_label,
    get_account_balances,
    get_net_assets,
    period_bounds,
    previous_period_bounds,
    sum_by_direction,
)


async def build_report_payload(
    session: AsyncSession,
    *,
    user_id: int,
    period: str,
    start: datetime,
    end: datetime,
    custom_days: Optional[int] = None,
    include_anomaly_scan: bool = True,
    include_tax_check: bool = True,
) -> Dict[str, Any]:
    """构建完整报告数据

    Args:
        period: daily / weekly / monthly / quarterly / yearly / custom
        start, end: 周期范围（左闭右开）
        include_anomaly_scan: 是否执行异常扫描（耗时操作）
        include_tax_check: 是否执行税务自检
    """
    config = get_config()

    # ---------------- 基础收支 ----------------
    totals = await sum_by_direction(session, user_id=user_id, start=start, end=end)
    prev_start, prev_end = previous_period_bounds(period, start, end)
    prev_totals = await sum_by_direction(
        session, user_id=user_id, start=prev_start, end=prev_end
    )

    # 环比
    def _delta(cur: float, prev: float) -> Dict[str, Any]:
        diff = cur - prev
        return {
            "value": round(diff, 2),
            "ratio": round(diff / prev * 100, 2) if prev else None,
            "direction": "up" if diff > 0 else ("down" if diff < 0 else "flat"),
        }

    # ---------------- 支出结构 ----------------
    category_expense = await aggregate_by_category(
        session, user_id=user_id, start=start, end=end,
        direction=TransactionDirection.EXPENSE,
    )
    category_income = await aggregate_by_category(
        session, user_id=user_id, start=start, end=end,
        direction=TransactionDirection.INCOME,
    )

    # ---------------- 趋势 ----------------
    daily_expense = await aggregate_by_day(
        session, user_id=user_id, start=start, end=end,
        direction=TransactionDirection.EXPENSE,
    )
    daily_income = await aggregate_by_day(
        session, user_id=user_id, start=start, end=end,
        direction=TransactionDirection.INCOME,
    )
    monthly_trend = await build_trend(session, user_id=user_id, months=6, reference=end)

    # ---------------- 归因分析 ----------------
    attribution = await build_attribution(
        session,
        user_id=user_id,
        current_start=start,
        current_end=end,
        previous_start=prev_start,
        previous_end=prev_end,
    )

    # ---------------- 对手方 ----------------
    top_counterparties = await aggregate_by_counterparty(
        session, user_id=user_id, start=start, end=end, limit=8
    )

    # ---------------- 异常检测 ----------------
    if include_anomaly_scan and config.anomaly.enabled:
        anomaly_scan = await anomaly.scan_period(
            session, user_id=user_id, start=start, end=end
        )
        duplicates = await anomaly.scan_duplicates(
            session, user_id=user_id, start=start, end=end
        )
    else:
        anomaly_scan = await _empty_anomaly_result(session, user_id=user_id, start=start, end=end)
        duplicates = []

    # ---------------- 预算执行 ----------------
    budget_status = await budget_service.get_budget_status(
        session, user_id=user_id, start=start, end=end, period=period
    )

    # ---------------- 税务自检 ----------------
    if include_tax_check and config.tax_check.enabled:
        tax_result = await taxcheck.run_tax_check(
            session, user_id=user_id, start=start, end=end
        )
    else:
        tax_result = {
            "checks": [], "counts": {}, "highest_level": "info",
            "optimizations": [], "risk_count": 0,
            "disclaimer": config.tax_check.disclaimer,
            "taxpayer_type": config.tax_check.taxpayer_type,
            "period_days": max((end - start).days, 1),
            "deductible_expense": 0.0,
        }

    # ---------------- 资产 ----------------
    net_assets = await get_net_assets(session, user_id=user_id)
    # 账户明细：净资产只有一个数，前端要展示「账户余额」以及各账户分布，
    # 所以把明细一并放进 payload，保证前端与出图消费的是同一份数据。
    accounts = await get_account_balances(session, user_id=user_id)
    txn_count = await count_transactions(
        session, user_id=user_id, start=start, end=end
    )

    # ---------------- 关键指标 ----------------
    savings_rate = (
        (totals["income"] - totals["expense"]) / totals["income"] * 100
        if totals["income"] > 0 else 0.0
    )
    avg_daily_expense = totals["expense"] / max((end - start).days, 1)
    top_category = category_expense[0] if category_expense else None

    # ---------------- 组装 ----------------
    payload: Dict[str, Any] = {
        "meta": {
            "period": period,
            "period_label": format_period_label(period, start, end),
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "currency_symbol": config.ledger.currency_symbol,
            "transaction_count": txn_count,
        },
        # ============ 模块一：财务税务整体状况 ============
        "overview": {
            "income": round(totals["income"], 2),
            "expense": round(totals["expense"], 2),
            "net": round(totals["net"], 2),
            "net_assets": net_assets,
            # 全部账户余额之和（与 net_assets 口径不同：后者只算 include_in_net 的账户）
            "balance": round(sum(float(a.get("balance") or 0.0) for a in accounts), 2),
            "savings_rate": round(savings_rate, 2),
            "avg_daily_expense": round(avg_daily_expense, 2),
            "transaction_count": txn_count,
            "compare": {
                "income": _delta(totals["income"], prev_totals["income"]),
                "expense": _delta(totals["expense"], prev_totals["expense"]),
                "net": _delta(totals["net"], prev_totals["net"]),
            },
        },
        "structure": {
            "expense_categories": category_expense,
            "income_categories": category_income,
            "top_category": top_category,
            "top_counterparties": top_counterparties,
        },
        # 账户明细（前端账户卡片与出图共用）
        "accounts": accounts,
        "trend": {
            "daily_expense": daily_expense,
            "daily_income": daily_income,
            "monthly": monthly_trend,
        },
        "anomaly": {
            "scanned_count": anomaly_scan["scanned_count"],
            "anomaly_count": anomaly_scan["anomaly_count"],
            "personal_mixed_count": anomaly_scan["personal_mixed_count"],
            "anomaly_amount": anomaly_scan["anomaly_amount"],
            "anomaly_amount_ratio": anomaly_scan["anomaly_amount_ratio"],
            "anomalies": anomaly_scan["anomalies"][:20],
            "personal_mixed": anomaly_scan["personal_mixed"][:20],
            "duplicates": duplicates[:10],
        },
        "budget": budget_status,
        "attribution": attribution,
        # ============ 模块二：财务税务优化方向 ============
        "tax": tax_result,
        "optimization": {
            "tax_optimizations": tax_result["optimizations"],
            "risk_count": tax_result["risk_count"],
            "highest_level": tax_result["highest_level"],
            "action_items": _build_action_items(
                totals=totals,
                budget_status=budget_status,
                anomaly_scan=anomaly_scan,
                tax_result=tax_result,
                attribution=attribution,
            ),
        },
        # 供前端做视觉标记
        "highlights": _build_highlights(
            totals=totals,
            prev_totals=prev_totals,
            top_category=top_category,
            anomaly_scan=anomaly_scan,
            budget_status=budget_status,
            tax_result=tax_result,
        ),
    }

    return payload


async def _empty_anomaly_result(
    session: AsyncSession, *, user_id: int, start: datetime, end: datetime
) -> Dict[str, Any]:
    """未启用异常扫描时返回统计口径一致的空结果。

    键名必须与 ``anomaly.scan_period`` 完全一致 —— 两条分支的输出会被同一套
    模板和前端消费，键名不一致会导致「扫描流水 0 笔」这种静默显示错误。
    """
    stats = await anomaly.get_anomaly_stats(
        session, user_id=user_id, start=start, end=end
    )
    return {
        "scanned_count": stats["total_count"],
        "anomaly_count": stats["anomaly_count"],
        "personal_mixed_count": stats["personal_mixed_count"],
        "anomalies": [],
        "personal_mixed": [],
        "anomaly_amount": 0.0,
        "anomaly_amount_ratio": stats.get("anomaly_ratio", 0.0),
    }


def _build_action_items(
    *,
    totals: Dict[str, float],
    budget_status: Dict[str, Any],
    anomaly_scan: Dict[str, Any],
    tax_result: Dict[str, Any],
    attribution: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """汇总可执行行动项，按优先级排序

    这是「优化方向」模块最实用的部分：用户看完就知道下一步做什么。
    """
    items: List[Dict[str, Any]] = []

    # 1. 超支类别
    for over in budget_status.get("over_items", [])[:3]:
        items.append(
            {
                "priority": "high",
                "category": "预算",
                "title": f"「{over['name']}」已超预算",
                "detail": (
                    f"预算 {over['budget']:,.2f} 元，实际支出 {over['actual']:,.2f} 元，"
                    f"超出 {abs(over['remaining']):,.2f} 元（使用率 {over['usage_ratio']:.1f}%）。"
                ),
                "action": "复核该类别的必要支出，考虑下调非必要开销或在下一周期上调预算。",
            }
        )

    # 2. 个人消费混入
    if anomaly_scan.get("personal_mixed_count", 0) > 0:
        items.append(
            {
                "priority": "high",
                "category": "税务风险",
                "title": "疑似个人消费混入经营支出",
                "detail": (
                    f"检出 {anomaly_scan['personal_mixed_count']} 笔，"
                    f"合计 {sum(a['amount'] for a in anomaly_scan.get('personal_mixed', [])):,.2f} 元。"
                ),
                "action": "逐笔核对用途，确属个人的应剔除或做纳税调整，并留存业务相关性证明。",
            }
        )

    # 3. 重复记账
    if anomaly_scan.get("duplicates"):
        items.append(
            {
                "priority": "medium",
                "category": "数据质量",
                "title": "存在疑似重复记账",
                "detail": f"发现 {len(anomaly_scan['duplicates'])} 组金额与时间高度接近的记录。",
                "action": "核对原始凭证，删除重复记录，避免虚增成本被税务机关质疑。",
            }
        )

    # 4. 亏损预警
    if totals["net"] < 0:
        items.append(
            {
                "priority": "high",
                "category": "经营",
                "title": "本期收支为负",
                "detail": (
                    f"收入 {totals['income']:,.2f} 元，支出 {totals['expense']:,.2f} 元，"
                    f"净流出 {abs(totals['net']):,.2f} 元。"
                ),
                "action": "检查是否存在未入账收入，或压缩非刚性支出；持续亏损需关注现金流安全线。",
            }
        )
    elif totals["income"] > 0 and totals["net"] / totals["income"] < 0.1:
        items.append(
            {
                "priority": "medium",
                "category": "经营",
                "title": "结余率偏低",
                "detail": (
                    f"本期结余率 "
                    f"{totals['net'] / totals['income'] * 100:.1f}%，低于 10% 的稳健线。"
                ),
                "action": "建立应急储备金，建议结余率保持在 20% 以上。",
            }
        )

    # 5. 支出增长归因
    if attribution.get("delta_total", 0) > 0 and attribution.get("significant"):
        top = attribution["significant"][0]
        if top["delta"] > 0:
            items.append(
                {
                    "priority": "medium",
                    "category": "成本控制",
                    "title": f"「{top['name']}」是支出增长主因",
                    "detail": (
                        f"较上期增加 {top['delta']:,.2f} 元"
                        f"（上期 {top['previous']:,.2f} → 本期 {top['current']:,.2f}）。"
                    ),
                    "action": f"分析「{top['name']}」增长原因，判断是业务扩张带来的合理增长还是成本失控。",
                }
            )

    # 6. 税检建议
    for opt in tax_result.get("optimizations", [])[:4]:
        items.append(
            {
                "priority": "high" if opt["level"] == "high" else "medium",
                "category": "税务优化",
                "title": opt["title"],
                "detail": opt["problem"],
                "action": opt["suggestion"],
            }
        )

    # 7. 预算缺失提示
    if not budget_status.get("has_budget"):
        items.append(
            {
                "priority": "low",
                "category": "管理建议",
                "title": "尚未设置预算",
                "detail": "系统未检测到本期预算配置，无法进行超支预警。",
                "action": "为主要支出类别设置月度预算，系统将在使用率达 80% 时提醒。",
            }
        )

    priority_order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda x: priority_order.get(x["priority"], 3))
    return items


def _build_highlights(
    *,
    totals: Dict[str, float],
    prev_totals: Dict[str, float],
    top_category: Optional[Dict[str, Any]],
    anomaly_scan: Dict[str, Any],
    budget_status: Dict[str, Any],
    tax_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """生成报告高亮卡片（前端顶部展示的核心结论）"""
    highlights: List[Dict[str, Any]] = []

    expense_delta = totals["expense"] - prev_totals["expense"]
    if prev_totals["expense"] > 0:
        ratio = expense_delta / prev_totals["expense"] * 100
        highlights.append(
            {
                "type": "expense_change",
                "tone": "warning" if expense_delta > 0 else "positive",
                "label": "支出环比",
                "value": round(ratio, 1),
                "unit": "%",
                "text": (
                    f"支出较上期{'增加' if expense_delta > 0 else '减少'} "
                    f"{abs(ratio):.1f}%（{abs(expense_delta):,.2f} 元）"
                ),
            }
        )

    if top_category:
        highlights.append(
            {
                "type": "top_category",
                "tone": "info",
                "label": "最大支出",
                "value": round(top_category["ratio"], 1),
                "unit": "%",
                "text": f"{top_category['icon']} {top_category['name']} 占比最高，"
                        f"金额 {top_category['amount']:,.2f} 元",
            }
        )

    if anomaly_scan.get("anomaly_count", 0) > 0:
        highlights.append(
            {
                "type": "anomaly",
                "tone": "warning",
                "label": "异常笔数",
                "value": anomaly_scan["anomaly_count"],
                "unit": "笔",
                "text": f"检出 {anomaly_scan['anomaly_count']} 笔异常支出，"
                        f"涉及金额 {anomaly_scan['anomaly_amount']:,.2f} 元",
            }
        )

    if budget_status.get("over_count", 0) > 0:
        highlights.append(
            {
                "type": "budget",
                "tone": "danger",
                "label": "超支类别",
                "value": budget_status["over_count"],
                "unit": "个",
                "text": f"{budget_status['over_count']} 个类别超出预算，建议优先处理",
            }
        )

    if tax_result.get("risk_count", 0) > 0:
        highlights.append(
            {
                "type": "tax",
                "tone": "warning" if tax_result["highest_level"] != "high" else "danger",
                "label": "税务风险",
                "value": tax_result["risk_count"],
                "unit": "项",
                "text": f"税务自检发现 {tax_result['risk_count']} 项待优化，"
                        f"最高风险等级：{_level_label(tax_result['highest_level'])}",
            }
        )

    return highlights


def _level_label(level: str) -> str:
    """风险等级的中文标签"""
    return {
        "info": "提示",
        "low": "低风险",
        "medium": "中风险",
        "high": "高风险",
    }.get(level, level)


async def generate_report(
    session: AsyncSession,
    *,
    user_id: int,
    period: str = "monthly",
    reference: Optional[datetime] = None,
    custom_days: Optional[int] = None,
    custom_start: Optional[datetime] = None,
    custom_end: Optional[datetime] = None,
    persist: bool = True,
    channel: str = "web",
) -> Report:
    """生成并（可选）落库一份报告

    自定义周期允许直接传起止时间，便于「近 45 天」这类需求。
    """
    started = time.perf_counter()

    if custom_start is not None and custom_end is not None:
        start, end = custom_start, custom_end
    else:
        start, end = period_bounds(period, reference=reference, custom_days=custom_days)

    report = Report(
        user_id=user_id,
        period=period,
        period_start=start,
        period_end=end,
        status=ReportStatus.GENERATING,
    )
    if persist:
        session.add(report)
        await session.flush()

    try:
        payload = await build_report_payload(
            session,
            user_id=user_id,
            period=period,
            start=start,
            end=end,
            custom_days=custom_days,
        )

        title = format_period_label(period, start, end)
        summary_text = _build_summary_text(payload)

        report.title = f"{title} 财税报告"
        report.payload = payload
        report.summary_text = summary_text
        report.status = ReportStatus.COMPLETED
        report.elapsed_ms = int((time.perf_counter() - started) * 1000)

        if persist:
            session.add(report)
            await session.flush()

            # 把报告内的税检结果关联到报告 ID
            await taxcheck.run_tax_check(
                session,
                user_id=user_id,
                start=start,
                end=end,
                report_id=report.id,
                persist=True,
                create_suggestions=False,
            )

            await audit.log_action(
                session,
                "generate_report",
                user_id=user_id,
                target_type="report",
                target_id=report.id,
                operator="system",
                detail=f"生成 {title} 报告，耗时 {report.elapsed_ms}ms",
                extra={"period": period, "elapsed_ms": report.elapsed_ms},
                channel=channel,
            )

    except Exception as exc:  # noqa: BLE001
        report.status = ReportStatus.FAILED
        report.error_message = str(exc)[:512]
        report.elapsed_ms = int((time.perf_counter() - started) * 1000)
        if persist:
            session.add(report)

    return report


def _build_summary_text(payload: Dict[str, Any]) -> str:
    """生成推送用的摘要文本（纯文本，便于协议端直接发送）。

    这里刻意写得比「三行收支」详细得多：协议端聊天窗口里，
    信息密度高的一条消息远好过让用户再点开图片放大看。
    但也做了截断控制 —— 类别只列前 5 个、优化方向只列前 5 条，
    否则超长消息在部分协议端会被折叠或发送失败。
    """
    overview = payload["overview"]
    symbol = payload["meta"]["currency_symbol"]
    label = payload["meta"]["period_label"]

    lines = [
        f"📊 【{label} 财税报告】",
        f"🕐 统计区间：{payload['meta']['period_start'][:10]} ~ {payload['meta']['period_end'][:10]}",
        f"📝 流水笔数：{overview.get('transaction_count', 0)} 笔",
        "",
        "━━━ 一、财务税务整体状况 ━━━",
        "",
        f"💰 收入：{symbol}{overview['income']:,.2f}",
        f"💸 支出：{symbol}{overview['expense']:,.2f}",
        f"📈 结余：{symbol}{overview['net']:,.2f}",
        f"🏦 账户余额：{symbol}{overview.get('balance', 0):,.2f}",
        f"📉 结余率：{overview.get('savings_rate', 0):.1f}%",
        f"📅 日均支出：{symbol}{overview.get('avg_daily_expense', 0):,.2f}",
    ]

    # 三项环比一起给，用户最关心「这个月比上个月怎么样」
    compare = overview.get("compare") or {}
    compare_parts = []
    for key, name in (("income", "收入"), ("expense", "支出"), ("net", "结余")):
        item = compare.get(key) or {}
        ratio = item.get("ratio")
        if ratio is None:
            continue
        # direction 为 up/down/flat，箭头比正负号更容易一眼看出
        direction = item.get("direction")
        arrow = "↑" if direction == "up" else ("↓" if direction == "down" else "→")
        compare_parts.append(f"{name}{arrow}{abs(ratio):.1f}%")
    if compare_parts:
        lines.append("🔄 环比：" + " · ".join(compare_parts))

    # ---- 支出结构 ----
    categories = (payload.get("structure") or {}).get("expense_categories") or []
    if categories:
        lines.extend(["", f"🔝 支出结构 TOP{min(len(categories), 5)}："])
        for idx, cat in enumerate(categories[:5], 1):
            icon = cat.get("icon") or "•"
            lines.append(
                f"  {idx}. {icon} {cat['name']}：{symbol}{cat['amount']:,.2f}"
                f"（{cat.get('ratio', 0):.1f}%，{cat.get('count', 0)} 笔）"
            )

    # ---- 对手方 ----
    counterparties = (payload.get("structure") or {}).get("top_counterparties") or []
    if counterparties:
        lines.extend(["", "🤝 主要往来对象："])
        for party in counterparties[:3]:
            lines.append(
                f"  · {party.get('counterparty') or '未知'}："
                f"{symbol}{(party.get('amount') or 0):,.2f}（{party.get('count', 0)} 笔）"
            )

    # ---- 异常 ----
    anomaly_data = payload.get("anomaly") or {}
    anomaly_count = anomaly_data.get("anomaly_count", 0)
    personal_count = anomaly_data.get("personal_mixed_count", 0)
    if anomaly_count or personal_count:
        lines.extend(["", "━━━ ⚠️ 异常提示 ━━━"])
        if anomaly_count:
            lines.append(
                f"  · 金额异常：{anomaly_count} 笔"
                f"（{symbol}{anomaly_data.get('anomaly_amount', 0):,.2f}）"
            )
        if personal_count:
            lines.append(f"  · 个人消费混入：{personal_count} 笔")
    else:
        lines.extend(["", "✅ 本期未发现异常流水"])

    # ---- 预算 ----
    budget_data = payload.get("budget") or {}
    if budget_data.get("has_budget"):
        lines.extend(["", "━━━ 🎯 预算执行 ━━━"])
        lines.append(
            f"  总预算 {symbol}{budget_data.get('total_budget', 0):,.2f} / "
            f"已用 {symbol}{budget_data.get('total_actual', 0):,.2f}"
            f"（{budget_data.get('total_usage_ratio', 0):.1f}%）"
        )
        for item in (budget_data.get("over_items") or [])[:3]:
            lines.append(
                f"  🔴 {item['name']} 已超支："
                f"{symbol}{item['actual']:,.2f} / {symbol}{item['budget']:,.2f}"
            )
        for item in (budget_data.get("warn_items") or [])[:2]:
            lines.append(
                f"  🟡 {item['name']} 接近预算：{item.get('usage_ratio', 0):.1f}%"
            )

    # ---- 归因 ----
    attribution = payload.get("attribution") or {}
    if attribution.get("summary"):
        lines.extend(["", "━━━ 📊 环比归因 ━━━", f"  {attribution['summary']}"])
        for item in (attribution.get("significant") or [])[:4]:
            delta = item.get("delta") or 0
            sign = "+" if delta >= 0 else "-"
            lines.append(
                f"  · {item['name']}：{sign}{symbol}{abs(delta):,.2f}"
                f"（本期 {symbol}{item.get('current', 0):,.2f}）"
            )

    # ---- 二、优化方向 ----
    lines.extend(["", "━━━ 二、财务税务优化方向 ━━━", ""])

    tax_data = payload.get("tax") or {}
    counts = tax_data.get("counts") or {}
    level_label = _level_label(tax_data.get("highest_level", "info"))
    lines.append(f"🧾 整体风险等级：{level_label}")
    lines.append(
        f"  风险项 {counts.get('high', 0)} 高 / {counts.get('medium', 0)} 中 / "
        f"{counts.get('low', 0)} 低 / {tax_data.get('risk_count', 0)} 需关注"
    )
    if tax_data.get("deductible_expense"):
        lines.append(
            f"  可税前扣除支出：{symbol}{tax_data['deductible_expense']:,.2f}"
            f"（共 {tax_data.get('period_days', 0)} 天）"
        )

    # 税检明细：只列非提示项，提示项没有行动价值
    tax_checks = [
        check for check in (tax_data.get("checks") or [])
        if check.get("level") not in (None, "info")
    ]
    if tax_checks:
        lines.append("")
        lines.append("📋 税务自检发现：")
        for check in tax_checks[:5]:
            badge = {"high": "🔴", "medium": "🟠", "low": "🟡"}.get(check.get("level"), "•")
            lines.append(f"  {badge} {check.get('title')}")
            if check.get("description"):
                lines.append(f"      {check['description']}")
            if check.get("suggestion"):
                lines.append(f"      👉 {check['suggestion']}")

    actions = (payload.get("optimization") or {}).get("action_items") or []
    if actions:
        lines.append("")
        lines.append(f"✨ 行动建议（共 {len(actions)} 条，列前 {min(len(actions), 5)} 条）：")
        priority_badge = {"high": "‼️", "medium": "❗", "low": "·"}
        for idx, item in enumerate(actions[:5], 1):
            badge = priority_badge.get(item.get("priority"), "·")
            lines.append(f"  {idx}. {badge} {item.get('title')}")
            if item.get("detail"):
                lines.append(f"      {item['detail']}")

    highlights = payload.get("highlights") or []
    if highlights:
        lines.extend(["", "🔍 本期亮点："])
        for item in highlights[:3]:
            lines.append(f"  · {item.get('text') or item.get('title') or ''}")

    lines.extend(["", f"— 流萤 Firefly · {payload['meta'].get('generated_at', '')[:19]} 生成"])

    return "\n".join(lines)


# ------------------------------------------------------------------------------
# 查询：报告归档与历史对比
# ------------------------------------------------------------------------------


async def get_report(
    session: AsyncSession,
    report_id: Any,
    *,
    user_id: Optional[int] = None,
) -> Optional[Report]:
    """按 ID 取报告。带 user_id 时顺带做归属校验。"""
    from sqlmodel import select

    statement = select(Report).where(Report.id == report_id)
    if user_id is not None:
        statement = statement.where(Report.user_id == user_id)

    result = await session.execute(statement)
    return result.scalars().first()


async def get_latest_report(
    session: AsyncSession,
    *,
    user_id: int,
    period: Optional[str] = None,
) -> Optional[Report]:
    """取最近一次**成功生成**的报告。

    只认 completed —— 前端首屏展示失败报告没有意义，
    而且失败的 payload 是空的，渲染出来只会是一片空白。
    """
    from sqlmodel import select

    statement = (
        select(Report)
        .where(Report.user_id == user_id)
        .where(Report.status == ReportStatus.COMPLETED)
    )
    if period:
        statement = statement.where(Report.period == period)

    statement = statement.order_by(Report.created_at.desc()).limit(1)
    result = await session.execute(statement)
    return result.scalars().first()


async def list_reports(
    session: AsyncSession,
    *,
    user_id: int,
    period: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Report]:
    """报告归档列表（按生成时间倒序），供历史对比使用。"""
    from sqlmodel import select

    statement = select(Report).where(Report.user_id == user_id)
    if period:
        statement = statement.where(Report.period == period)

    statement = (
        statement.order_by(Report.created_at.desc()).offset(offset).limit(limit)
    )
    result = await session.execute(statement)
    return list(result.scalars().all())


async def count_reports(
    session: AsyncSession,
    *,
    user_id: int,
    period: Optional[str] = None,
) -> int:
    """报告总数（分页用）。"""
    from sqlalchemy import func
    from sqlmodel import select

    statement = select(func.count(Report.id)).where(Report.user_id == user_id)
    if period:
        statement = statement.where(Report.period == period)

    result = await session.execute(statement)
    return int(result.scalar() or 0)


async def compare_reports(
    session: AsyncSession,
    *,
    user_id: int,
    current_id: Any,
    previous_id: Any,
) -> Dict[str, Any]:
    """两份报告的关键指标对比 —— 论文里「历史对比」章节的数据来源。

    直接从各自 payload 里取数，不重算，保证与当时前端展示的口径一致。
    """
    current = await get_report(session, current_id, user_id=user_id)
    previous = await get_report(session, previous_id, user_id=user_id)

    def _metrics(report: Optional[Report]) -> Dict[str, Any]:
        if report is None or not isinstance(report.payload, dict):
            return {}
        overview = report.payload.get("overview", {})
        tax = report.payload.get("tax", {})
        return {
            "report_id": report.id,
            "title": report.title,
            "period_start": report.period_start.isoformat() if report.period_start else None,
            "period_end": report.period_end.isoformat() if report.period_end else None,
            "income": overview.get("income"),
            "expense": overview.get("expense"),
            "net": overview.get("net"),
            "savings_rate": overview.get("savings_rate"),
            "transaction_count": overview.get("transaction_count"),
            "risk_count": tax.get("risk_count"),
            "highest_level": tax.get("highest_level"),
        }

    current_metrics = _metrics(current)
    previous_metrics = _metrics(previous)

    delta: Dict[str, Any] = {}
    for key in ("income", "expense", "net"):
        cur_value = current_metrics.get(key)
        prev_value = previous_metrics.get(key)
        if isinstance(cur_value, (int, float)) and isinstance(prev_value, (int, float)):
            delta[key] = {
                "value": round(cur_value - prev_value, 2),
                "ratio": round((cur_value - prev_value) / prev_value * 100, 2)
                if prev_value
                else None,
            }
        else:
            delta[key] = {"value": None, "ratio": None}

    return {
        "current": current_metrics,
        "previous": previous_metrics,
        "delta": delta,
        "available": bool(current_metrics and previous_metrics),
    }


__all__ = [
    "build_report_payload",
    "generate_report",
    "format_period_label",
    "get_report",
    "get_latest_report",
    "list_reports",
    "count_reports",
    "compare_reports",
]
