"""流萤 Firefly · 税务风险自检引擎

这是「金融科技应用」专业的正题模块：
把记账数据翻译成税务口径的风险信号，并给出可执行的优化方向。

检查项：
    1. 增值税起征点临近     —— vat_threshold
    2. 税负率偏离合理区间   —— tax_burden
    3. 支出结构失衡         —— category_concentration
    4. 个人消费混入经营支出 —— personal_mixed
    5. 可扣除支出未取得凭证 —— missing_invoice
    6. 进销项倒挂           —— income_expense_inversion
    7. 纳税人身份临界点     —— taxpayer_critical
    8. 专项附加扣除提醒     —— deduction_hint

**免责声明**：结果依据公开政策规则自动生成，仅供参考，不构成专业税务意见。
每条结论都附带 policy_basis 字段说明依据来源，便于答辩时解释口径。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import get_config
from ..db.models import (
    Category,
    RiskLevel,
    SuggestionKind,
    TaxCheckResult,
    Transaction,
    TransactionDirection,
)
from . import audit
from .ledger import aggregate_by_category, sum_by_direction

# 政策依据文本（P2 接入 RAG 后可由知识库检索替换，这里作为离线兜底）
POLICY_BASIS: Dict[str, str] = {
    "vat_threshold": (
        "《中华人民共和国增值税暂行条例》及财政部、税务总局关于小规模纳税人"
        "月销售额 10 万元以下免征增值税的相关规定。"
    ),
    "tax_burden": (
        "增值税税负率 = 当期应纳增值税 / 当期应税销售额。"
        "各行业税负率参考区间由主管税务机关发布，偏离过大可能触发评估。"
    ),
    "category_concentration": "《企业所得税法》第八条关于支出真实性与合理性的要求。",
    "personal_mixed": (
        "《企业所得税法》第八条、《企业所得税法实施条例》第二十七条："
        "与取得收入无关的支出不得在税前扣除。"
    ),
    "missing_invoice": (
        "《企业所得税税前扣除凭证管理办法》（国家税务总局公告 2018 年第 28 号）："
        "企业发生支出应取得税前扣除凭证。"
    ),
    "income_expense_inversion": "增值税进销项配比原则，长期倒挂易触发风险核查。",
    "taxpayer_critical": (
        "《关于统一增值税小规模纳税人标准的通知》（财税〔2018〕33 号）："
        "年应征增值税销售额 500 万元为一般纳税人与小规模纳税人的分界。"
    ),
    "deduction_hint": "《个人所得税专项附加扣除暂行办法》相关规定。",
}

# 一般纳税人年销售额临界点（元）
GENERAL_TAXPAYER_THRESHOLD = 5_000_000.0


# ------------------------------------------------------------------------------
# 单项检查
# ------------------------------------------------------------------------------
def _make_result(
    *,
    check_key: str,
    title: str,
    level: RiskLevel,
    actual: float,
    threshold: float,
    description: str,
    suggestion: str,
) -> Dict[str, Any]:
    """构造一条税检结果（统一填充政策依据）"""
    return {
        "check_key": check_key,
        "title": title,
        "level": level.value,
        "actual_value": round(actual, 4),
        "threshold_value": round(threshold, 4),
        "description": description,
        "suggestion": suggestion,
        "policy_basis": POLICY_BASIS.get(check_key, ""),
    }


def check_vat_threshold(
    *,
    period_income: float,
    period_days: int,
    config,
) -> Dict[str, Any]:
    """检查增值税起征点临近情况

    把周期销售额折算为月度口径再与起征点比较，
    否则「季报 28 万」这类数据无法直接判断。
    """
    monthly_income = period_income / max(period_days, 1) * 30
    threshold = config.tax_check.vat_threshold_monthly
    ratio = monthly_income / threshold if threshold else 0.0

    if config.tax_check.taxpayer_type == "general":
        return _make_result(
            check_key="vat_threshold",
            title="增值税起征点",
            level=RiskLevel.INFO,
            actual=monthly_income,
            threshold=threshold,
            description=(
                f"当前为一般纳税人，按月折算销售额 {monthly_income:,.2f} 元，"
                f"不适用小规模纳税人起征点优惠。"
            ),
            suggestion="一般纳税人按销项减进项计算应纳税额，请确保进项发票及时认证抵扣。",
        )

    if ratio >= 1.0:
        level = RiskLevel.HIGH
        desc = (
            f"按月折算销售额 {monthly_income:,.2f} 元，已超过小规模纳税人月销售额 "
            f"{threshold:,.0f} 元的免税标准，需就全部销售额缴纳增值税。"
        )
        suggestion = (
            "1) 确认是否可适用其他减免政策；"
            "2) 若长期超过标准，建议评估登记为一般纳税人后的进项抵扣收益；"
            "3) 注意开票节奏，避免集中开票导致单月超标。"
        )
    elif ratio >= 0.8:
        level = RiskLevel.MEDIUM
        desc = (
            f"按月折算销售额 {monthly_income:,.2f} 元，已达免税标准 "
            f"{threshold:,.0f} 元的 {ratio * 100:.1f}%，接近临界点。"
        )
        suggestion = (
            "临近起征点，建议合理规划开票时间，避免单月突破标准而丧失免税优惠。"
        )
    else:
        level = RiskLevel.INFO
        desc = (
            f"按月折算销售额 {monthly_income:,.2f} 元，处于免税标准 "
            f"{threshold:,.0f} 元以内（占比 {ratio * 100:.1f}%），当前可享受免税政策。"
        )
        suggestion = "保持当前规模即可继续享受小规模纳税人免税政策。"

    return _make_result(
        check_key="vat_threshold",
        title="增值税起征点",
        level=level,
        actual=monthly_income,
        threshold=threshold,
        description=desc,
        suggestion=suggestion,
    )


def check_tax_burden(
    *,
    period_income: float,
    period_expense: float,
    period_days: int,
    config,
) -> Dict[str, Any]:
    """检查税负率是否偏离合理区间

    简化测算：以销售额为基数，按配置税率估算应纳增值税，得到估算税负率。
    真实申报还需考虑进项抵扣，此处为**教学演示口径**，报告中会注明。
    """
    if period_income <= 0:
        return _make_result(
            check_key="tax_burden",
            title="增值税税负率",
            level=RiskLevel.INFO,
            actual=0.0,
            threshold=config.tax_check.tax_burden_low,
            description="本期无销售收入，无需测算税负率。",
            suggestion="无收入期间仍需按期进行零申报，避免产生逾期记录。",
        )

    if config.tax_check.taxpayer_type == "general":
        rate = config.tax_check.vat_rate_general / 100
        # 一般纳税人：销项 - 进项（此处以支出中可抵扣部分近似进项）
        estimated_vat = max(period_income * rate - period_expense * rate * 0.6, 0.0)
    else:
        rate = config.tax_check.vat_rate_small_scale / 100
        monthly_income = period_income / max(period_days, 1) * 30
        estimated_vat = (
            0.0
            if monthly_income <= config.tax_check.vat_threshold_monthly
            else period_income * rate
        )

    burden = estimated_vat / period_income * 100
    low = config.tax_check.tax_burden_low
    high = config.tax_check.tax_burden_high

    if burden < low:
        level = RiskLevel.LOW
        desc = (
            f"本期估算增值税税负率 {burden:.2f}%，低于参考区间下限 {low:.2f}%。"
            f"（测算基数：销售额 {period_income:,.2f} 元）"
        )
        suggestion = (
            "税负率偏低可能被关注。若确有免税政策适用，请留存政策依据备查；"
            "若因进项留抵导致，建议关注留抵退税政策的适用条件。"
        )
    elif burden > high:
        level = RiskLevel.MEDIUM
        desc = (
            f"本期估算增值税税负率 {burden:.2f}%，高于参考区间上限 {high:.2f}%。"
        )
        suggestion = (
            "税负偏高，建议核查：1) 进项发票是否及时取得并认证；"
            "2) 是否存在可适用的税收优惠未申请；3) 业务是否可拆分适用不同税率。"
        )
    else:
        level = RiskLevel.INFO
        desc = f"本期估算增值税税负率 {burden:.2f}%，处于参考区间 {low:.2f}%~{high:.2f}% 内。"
        suggestion = "税负率处于合理区间，保持现有进销项管理方式即可。"

    return _make_result(
        check_key="tax_burden",
        title="增值税税负率",
        level=level,
        actual=burden,
        threshold=high,
        description=desc,
        suggestion=suggestion,
    )


def check_category_concentration(
    *, categories: List[Dict[str, Any]], config
) -> Optional[Dict[str, Any]]:
    """检查支出结构是否过度集中

    单一类别占比过高，既可能是经营特征，也可能是科目归集错误，
    值得提示用户复核。
    """
    if not categories:
        return None

    top = categories[0]
    warn_line = config.tax_check.category_concentration_warn

    if top["ratio"] < warn_line:
        return _make_result(
            check_key="category_concentration",
            title="支出结构",
            level=RiskLevel.INFO,
            actual=top["ratio"],
            threshold=warn_line,
            description=(
                f"最大支出类别「{top['name']}」占比 {top['ratio']:.2f}%，"
                f"未超过警戒线 {warn_line:.0f}%，支出结构较为分散。"
            ),
            suggestion="支出结构健康，无需特别调整。",
        )

    level = RiskLevel.MEDIUM if top["ratio"] >= 60 else RiskLevel.LOW
    return _make_result(
        check_key="category_concentration",
        title="支出结构",
        level=level,
        actual=top["ratio"],
        threshold=warn_line,
        description=(
            f"最大支出类别「{top['name']}」占比高达 {top['ratio']:.2f}%"
            f"（金额 {top['amount']:,.2f} 元），超过警戒线 {warn_line:.0f}%。"
        ),
        suggestion=(
            f"1) 复核「{top['name']}」科目归集是否准确，避免与其他类别混淆；"
            "2) 若确为业务特征，评估该支出的真实性凭证是否齐备；"
            "3) 关注该类别的成本控制空间。"
        ),
    )


def check_personal_mixed(
    *, personal_count: int, personal_amount: float, total_expense: float
) -> Optional[Dict[str, Any]]:
    """检查个人消费混入经营支出

    这是税务稽查的高频风险点，也是本系统最有业务价值的检查项之一。
    """
    if personal_count <= 0:
        return None

    ratio = personal_amount / total_expense * 100 if total_expense else 0.0
    level = RiskLevel.HIGH if ratio >= 10 or personal_amount >= 5000 else RiskLevel.MEDIUM

    return _make_result(
        check_key="personal_mixed",
        title="个人消费混入风险",
        level=level,
        actual=personal_amount,
        threshold=0.0,
        description=(
            f"检出 {personal_count} 笔疑似个人消费，合计 {personal_amount:,.2f} 元，"
            f"占本期支出 {ratio:.2f}%。此类支出若无法证明与经营相关，"
            f"不得在企业所得税前扣除。"
        ),
        suggestion=(
            "1) 逐笔核对，确属个人消费的应从公司支出中剔除或做纳税调整；"
            "2) 保留消费小票、用途说明等证明业务相关性的材料；"
            "3) 建立费用报销审批流程，从源头减少混同。"
        ),
    )


def check_missing_invoice(
    *, deductible_expense: float, total_expense: float
) -> Optional[Dict[str, Any]]:
    """检查可税前扣除支出是否取得凭证

    以「可扣除类别支出」为基数，提示凭证管理要求。
    """
    if deductible_expense <= 0:
        return None

    ratio = deductible_expense / total_expense * 100 if total_expense else 0.0
    return _make_result(
        check_key="missing_invoice",
        title="税前扣除凭证",
        level=RiskLevel.LOW,
        actual=deductible_expense,
        threshold=total_expense,
        description=(
            f"本期可税前扣除类别的支出合计 {deductible_expense:,.2f} 元，"
            f"占总支出的 {ratio:.2f}%。上述支出需取得合规扣除凭证方可在企业所得税前扣除。"
        ),
        suggestion=(
            "1) 逐笔确认是否已取得发票或合规凭证；"
            "2) 小额零星支出可用收款凭证及内部凭证替代；"
            "3) 及时在发票查验平台核验真伪。"
        ),
    )


def check_income_expense_inversion(
    *, period_income: float, period_expense: float
) -> Optional[Dict[str, Any]]:
    """检查进销项倒挂（支出长期大于收入）"""
    if period_income <= 0 and period_expense <= 0:
        return None

    if period_expense <= period_income:
        return _make_result(
            check_key="income_expense_inversion",
            title="进销项配比",
            level=RiskLevel.INFO,
            actual=period_expense,
            threshold=period_income,
            description=(
                f"本期收入 {period_income:,.2f} 元，支出 {period_expense:,.2f} 元，"
                f"收入覆盖支出，进销项配比正常。"
            ),
            suggestion="保持正常经营节奏。",
        )

    ratio = period_expense / period_income if period_income else float("inf")
    level = RiskLevel.HIGH if ratio >= 2 else RiskLevel.MEDIUM
    return _make_result(
        check_key="income_expense_inversion",
        title="进销项配比",
        level=level,
        actual=period_expense,
        threshold=period_income,
        description=(
            f"本期支出 {period_expense:,.2f} 元超过收入 {period_income:,.2f} 元"
            f"{'（亏损）' if period_income else '（无收入但有大额支出）'}，"
            f"支出为收入的 {ratio:.2f} 倍，存在进销项倒挂。"
        ),
        suggestion=(
            "1) 核实是否存在未开票收入；"
            "2) 若为正常亏损，保留完整成本费用凭证备查；"
            "3) 检查是否有股东借款、个人垫付等未入账事项。"
        ),
    )


def check_taxpayer_critical(
    *, period_income: float, period_days: int, config
) -> Optional[Dict[str, Any]]:
    """检查是否临近一般纳税人登记标准"""
    annualized = period_income / max(period_days, 1) * 365
    ratio = annualized / GENERAL_TAXPAYER_THRESHOLD

    if config.tax_check.taxpayer_type == "general":
        return None

    if ratio < 0.6:
        return _make_result(
            check_key="taxpayer_critical",
            title="纳税人身份临界点",
            level=RiskLevel.INFO,
            actual=annualized,
            threshold=GENERAL_TAXPAYER_THRESHOLD,
            description=(
                f"按本期数据年化销售额约 {annualized:,.2f} 元，"
                f"为一般纳税人标准（500 万元）的 {ratio * 100:.1f}%，暂无需转换。"
            ),
            suggestion="当前维持小规模纳税人身份较为有利。",
        )

    level = RiskLevel.HIGH if ratio >= 1.0 else RiskLevel.MEDIUM
    desc = (
        f"按本期数据年化销售额约 {annualized:,.2f} 元，已达一般纳税人标准"
        f"（500 万元）的 {ratio * 100:.1f}%。"
    )
    suggestion = (
        "1) 测算一般纳税人身份下的实际税负（销项减进项）与小规模身份对比；"
        "2) 若客户多需专票抵扣，转为一般纳税人可能更有利；"
        "3) 注意连续 12 个月销售额累计口径，避免被动超标。"
    )

    return _make_result(
        check_key="taxpayer_critical",
        title="纳税人身份临界点",
        level=level,
        actual=annualized,
        threshold=GENERAL_TAXPAYER_THRESHOLD,
        description=desc,
        suggestion=suggestion,
    )


def check_deduction_hint(
    *, period_income: float, config
) -> Optional[Dict[str, Any]]:
    """个税专项附加扣除提醒（个人版适用）"""
    if config.tax_check.taxpayer_type != "small_scale" or period_income <= 0:
        return None

    return _make_result(
        check_key="deduction_hint",
        title="个税专项附加扣除",
        level=RiskLevel.INFO,
        actual=0.0,
        threshold=0.0,
        description=(
            "若您有子女教育、继续教育、住房贷款利息、住房租金、赡养老人、"
            "大病医疗等支出，可申报个人所得税专项附加扣除。"
        ),
        suggestion=(
            "1) 在个人所得税 APP 中确认专项附加扣除信息是否完整；"
            "2) 每年 12 月确认次年扣除信息，避免中断；"
            "3) 留存相关证明材料备查（如租赁合同、学籍证明）。"
        ),
    )


# ------------------------------------------------------------------------------
# 汇总执行
# ------------------------------------------------------------------------------
async def run_tax_check(
    session: AsyncSession,
    *,
    user_id: int,
    start: datetime,
    end: datetime,
    report_id: Optional[int] = None,
    persist: bool = True,
    create_suggestions: bool = True,
) -> Dict[str, Any]:
    """执行完整的税务风险自检

    Returns:
        含全部检查项、风险汇总与免责声明的字典
    """
    config = get_config()
    period_days = max((end - start).days, 1)

    # 基础数据
    totals = await sum_by_direction(session, user_id=user_id, start=start, end=end)
    categories = await aggregate_by_category(
        session, user_id=user_id, start=start, end=end,
        direction=TransactionDirection.EXPENSE,
    )

    # 可税前扣除支出：按类别的 deductible 标记汇总
    deductible_stmt = (
        select(func.sum(Transaction.amount))
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.user_id == user_id,
            Transaction.trade_time >= start,
            Transaction.trade_time < end,
            Transaction.direction == TransactionDirection.EXPENSE,
            Category.deductible == True,  # noqa: E712
        )
    )
    deductible_result = await session.exec(deductible_stmt)
    deductible_expense = float(deductible_result.one() or 0.0)

    # 个人消费混入统计
    personal_stmt = select(
        func.count(Transaction.id), func.sum(Transaction.amount)
    ).where(
        Transaction.user_id == user_id,
        Transaction.trade_time >= start,
        Transaction.trade_time < end,
        Transaction.is_personal_mixed == True,  # noqa: E712
    )
    personal_result = await session.exec(personal_stmt)
    personal_row = personal_result.one()
    personal_count = int(personal_row[0] or 0)
    personal_amount = float(personal_row[1] or 0.0)

    # 逐项检查
    checks: List[Dict[str, Any]] = []
    checks.append(
        check_vat_threshold(
            period_income=totals["income"], period_days=period_days, config=config
        )
    )
    checks.append(
        check_tax_burden(
            period_income=totals["income"],
            period_expense=totals["expense"],
            period_days=period_days,
            config=config,
        )
    )

    for optional in (
        check_category_concentration(categories=categories, config=config),
        check_personal_mixed(
            personal_count=personal_count,
            personal_amount=personal_amount,
            total_expense=totals["expense"],
        ),
        check_missing_invoice(
            deductible_expense=deductible_expense, total_expense=totals["expense"]
        ),
        check_income_expense_inversion(
            period_income=totals["income"], period_expense=totals["expense"]
        ),
        check_taxpayer_critical(
            period_income=totals["income"], period_days=period_days, config=config
        ),
        check_deduction_hint(period_income=totals["income"], config=config),
    ):
        if optional is not None:
            checks.append(optional)

    # 风险等级汇总
    level_order = {RiskLevel.INFO.value: 0, RiskLevel.LOW.value: 1,
                   RiskLevel.MEDIUM.value: 2, RiskLevel.HIGH.value: 3}
    counts = {level.value: 0 for level in RiskLevel}
    for item in checks:
        counts[item["level"]] = counts.get(item["level"], 0) + 1

    highest = max(checks, key=lambda c: level_order.get(c["level"], 0)) if checks else None

    # 优化方向：抽取所有非 INFO 项的建议
    optimizations = [
        {
            "title": c["title"],
            "level": c["level"],
            "problem": c["description"],
            "suggestion": c["suggestion"],
            "policy_basis": c["policy_basis"],
        }
        for c in checks
        if c["level"] != RiskLevel.INFO.value
    ]
    optimizations.sort(key=lambda x: level_order.get(x["level"], 0), reverse=True)

    # 落库
    if persist:
        for item in checks:
            record = TaxCheckResult(
                user_id=user_id,
                report_id=report_id,
                check_key=item["check_key"],
                title=item["title"],
                level=RiskLevel(item["level"]),
                actual_value=item["actual_value"],
                threshold_value=item["threshold_value"],
                description=item["description"],
                suggestion=item["suggestion"],
                policy_basis=item["policy_basis"],
            )
            session.add(record)

    # 为优化方向生成建议记录（采纳率统计样本）
    if create_suggestions and config.audit.track_adoption:
        for opt in optimizations[:5]:
            await audit.record_suggestion(
                session,
                user_id=user_id,
                kind=SuggestionKind.TAX_OPTIMIZE,
                content=f"{opt['title']}：{opt['suggestion'][:120]}",
                target_type="tax_check",
                target_id=report_id,
                payload={"level": opt["level"], "policy_basis": opt["policy_basis"]},
                confidence=0.8,
                reason=opt["problem"][:200],
            )

    return {
        "checks": checks,
        "counts": counts,
        "highest_level": highest["level"] if highest else RiskLevel.INFO.value,
        "optimizations": optimizations,
        "risk_count": len(optimizations),
        "disclaimer": config.tax_check.disclaimer,
        "taxpayer_type": config.tax_check.taxpayer_type,
        "period_days": period_days,
        "deductible_expense": round(deductible_expense, 2),
    }


__all__ = [
    "POLICY_BASIS",
    "GENERAL_TAXPAYER_THRESHOLD",
    "check_vat_threshold",
    "check_tax_burden",
    "check_category_concentration",
    "check_personal_mixed",
    "check_missing_invoice",
    "check_income_expense_inversion",
    "check_taxpayer_critical",
    "check_deduction_hint",
    "run_tax_check",
]
