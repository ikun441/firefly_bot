"""流萤 Firefly · 数据模型定义

使用 SQLModel（SQLAlchemy + Pydantic）统一描述表结构与数据校验。

设计要点：
    * AuditLog.suggestion_adopted 是论文「建议采纳率」指标的唯一样本来源，
      必须在业务写入路径中稳定埋点，不可遗漏。
    * Transaction.confidence / is_confirmed 支撑「AI 抽取 → 人工确认」流程。
    * Category.tax_account 把记账类别与税务口径挂钩，是税检与税优建议的基础。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Index, String, Text, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, Relationship, SQLModel


# ------------------------------------------------------------------------------
# 枚举定义
# ------------------------------------------------------------------------------
def _now_utc() -> datetime:
    """统一使用带时区的 UTC 时间，避免周期统计时区错乱"""
    return datetime.now(timezone.utc)


class TransactionDirection(str, Enum):
    """收支方向"""

    EXPENSE = "expense"  # 支出
    INCOME = "income"  # 收入


class TransactionSource(str, Enum):
    """流水来源，用于统计各录入口径的使用率"""

    MANUAL = "manual"  # 手动录入（收集器）
    NATURAL_LANGUAGE = "natural_language"  # 自然语言记账
    OCR = "ocr"  # 截图/账单导入
    CSV = "csv"  # CSV 批量导入
    SUBSCRIPTION = "subscription"  # 周期订阅自动生成
    API = "api"  # 外部接口写入


class TaxpayerType(str, Enum):
    """纳税人身份，直接决定增值税测算口径"""

    SMALL_SCALE = "small_scale"  # 小规模纳税人
    GENERAL = "general"  # 一般纳税人


class RiskLevel(str, Enum):
    """税检风险等级"""

    INFO = "info"  # 提示
    LOW = "low"  # 低风险
    MEDIUM = "medium"  # 中风险
    HIGH = "high"  # 高风险


class ReportStatus(str, Enum):
    """报告生成状态"""

    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class SuggestionKind(str, Enum):
    """AI 建议类型，用于按类别统计采纳率"""

    CATEGORY = "category"  # 分类建议
    ANOMALY = "anomaly"  # 异常提示
    TAX_OPTIMIZE = "tax_optimize"  # 税务优化建议
    BUDGET = "budget"  # 预算建议


class AdoptionState(str, Enum):
    """建议采纳状态"""

    PENDING = "pending"  # 待用户处理
    ADOPTED = "adopted"  # 已采纳
    REJECTED = "rejected"  # 已拒绝
    IGNORED = "ignored"  # 超时未处理


# ------------------------------------------------------------------------------
# 用户
# ------------------------------------------------------------------------------
class User(SQLModel, table=True):
    """用户/租户

    毕设阶段以单用户为主，保留 tenant 概念以便将来扩展到小微企业多员工场景。
    """

    __tablename__ = "ff_user"

    id: Optional[int] = Field(default=None, primary_key=True)
    nickname: str = Field(default="默认用户", max_length=64)
    # 平台唯一标识（如 QQ 号），用于把协议端消息映射到本地账本
    platform_id: str = Field(default="", max_length=64, index=True)
    # 纳税人身份，影响税检口径
    taxpayer_type: TaxpayerType = Field(default=TaxpayerType.SMALL_SCALE)
    # 是否为小微企业版（预留多员工/审批流）
    is_business: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_now_utc)
    updated_at: datetime = Field(default_factory=_now_utc)


# ------------------------------------------------------------------------------
# 账户
# ------------------------------------------------------------------------------
class Account(SQLModel, table=True):
    """资金账户：现金 / 银行卡 / 微信 / 支付宝等"""

    __tablename__ = "ff_account"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="ff_user.id", index=True)
    name: str = Field(max_length=64)
    # 账户类型，前端按类型显示不同图标
    kind: str = Field(default="cash", max_length=32)
    # 初始余额
    initial_balance: float = Field(default=0.0)
    # 是否计入净资产统计
    include_in_net: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_now_utc)


# ------------------------------------------------------------------------------
# 类别
# ------------------------------------------------------------------------------
class Category(SQLModel, table=True):
    """记账类别

    * parent_id 支持二级分类（如 餐饮 > 外卖）
    * tax_account 挂接税务口径，是税检与税优建议的基础
    * is_system 标记内置类别，用户不可删除
    """

    __tablename__ = "ff_category"
    __table_args__ = (
        UniqueConstraint("user_id", "name", "parent_id", name="uq_category_user_name_parent"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="ff_user.id", index=True)
    name: str = Field(max_length=64, index=True)
    # 二级分类的父级
    parent_id: Optional[int] = Field(default=None, foreign_key="ff_category.id")
    # 收支归属
    direction: TransactionDirection = Field(default=TransactionDirection.EXPENSE)
    # 图标 emoji 或 CSS 类名
    icon: str = Field(default="📦", max_length=32)
    # 颜色标记（前端图表配色）
    color: str = Field(default="#5eead4", max_length=32)
    # 税务口径科目，如「业务招待费」「差旅费」
    tax_account: str = Field(default="", max_length=64)
    # 该类别支出是否可税前扣除
    deductible: bool = Field(default=False)
    # 是否内置类别
    is_system: bool = Field(default=False)
    # 是否为 AI 建议生成（类别自演化功能）
    is_ai_suggested: bool = Field(default=False)
    # 排序权重，越小越靠前
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=_now_utc)


# ------------------------------------------------------------------------------
# 流水（核心表）
# ------------------------------------------------------------------------------
class Transaction(SQLModel, table=True):
    """记账流水

    时间字段使用 trade_time（交易发生时间）而非创建时间，
    周期报告依据 trade_time 划分区间。
    """

    __tablename__ = "ff_transaction"
    __table_args__ = (
        # 周期统计按时间过滤 + 按类别聚合，联合索引显著提速
        Index("ix_txn_user_time", "user_id", "trade_time"),
        Index("ix_txn_user_category", "user_id", "category_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="ff_user.id", index=True)
    account_id: Optional[int] = Field(default=None, foreign_key="ff_account.id")
    category_id: Optional[int] = Field(default=None, foreign_key="ff_category.id")

    # 金额：统一存正数，方向由 direction 决定，避免正负号混乱
    amount: float = Field(default=0.0)
    direction: TransactionDirection = Field(default=TransactionDirection.EXPENSE)
    currency: str = Field(default="CNY", max_length=8)

    # 交易发生时间
    trade_time: datetime = Field(default_factory=_now_utc, index=True)
    # 对手方（商户/个人）
    counterparty: str = Field(default="", max_length=128, index=True)
    # 备注
    memo: str = Field(default="", max_length=512)
    # 原始输入文本（自然语言记账时保留，便于回溯与准确率评测）
    raw_input: str = Field(default="", max_length=1024)

    source: TransactionSource = Field(default=TransactionSource.MANUAL)

    # AI 抽取置信度 0~1，低于阈值进入「待确认」
    confidence: float = Field(default=1.0)
    # 是否已人工确认
    is_confirmed: bool = Field(default=True, index=True)
    # 疑似重复（金额+时间窗口命中）
    is_duplicate: bool = Field(default=False)
    # 异常标记
    is_anomaly: bool = Field(default=False)
    # 异常原因说明（可解释性）
    anomaly_reason: str = Field(default="", max_length=256)
    # 个人消费混入公司支出的风险标记（税务风险点）
    is_personal_mixed: bool = Field(default=False)

    # 是否已生成发票/凭证（预留）
    has_invoice: bool = Field(default=False)

    created_at: datetime = Field(default_factory=_now_utc)
    updated_at: datetime = Field(default_factory=_now_utc)


# ------------------------------------------------------------------------------
# 周期订阅
# ------------------------------------------------------------------------------
class Subscription(SQLModel, table=True):
    """周期订阅项：房租、宽带、会员服务等，定义一次自动按月入账"""

    __tablename__ = "ff_subscription"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="ff_user.id", index=True)
    name: str = Field(max_length=64)
    amount: float = Field(default=0.0)
    direction: TransactionDirection = Field(default=TransactionDirection.EXPENSE)
    category_id: Optional[int] = Field(default=None, foreign_key="ff_category.id")
    account_id: Optional[int] = Field(default=None, foreign_key="ff_account.id")

    # 周期类型：monthly / weekly / yearly
    period: str = Field(default="monthly", max_length=16)
    # 每月/每周的第几天执行
    day_of_period: int = Field(default=1)
    # 生效区间
    start_date: date = Field(default_factory=date.today)
    end_date: Optional[date] = Field(default=None)
    # 下次入账日期，调度器据此判断
    next_run_date: date = Field(default_factory=date.today)
    is_active: bool = Field(default=True)
    memo: str = Field(default="", max_length=256)
    created_at: datetime = Field(default_factory=_now_utc)


# ------------------------------------------------------------------------------
# 预算
# ------------------------------------------------------------------------------
class Budget(SQLModel, table=True):
    """预算：支持按类别或全局设置，按周期生效"""

    __tablename__ = "ff_budget"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "category_id", "period", "start_date", name="uq_budget_scope"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="ff_user.id", index=True)
    # 为空表示「总预算」，否则为类别预算
    category_id: Optional[int] = Field(default=None, foreign_key="ff_category.id")
    amount: float = Field(default=0.0)
    period: str = Field(default="monthly", max_length=16)
    start_date: date = Field(default_factory=date.today)
    end_date: Optional[date] = Field(default=None)
    # 是否启用超支提醒
    alert_enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_now_utc)


# ------------------------------------------------------------------------------
# 报告
# ------------------------------------------------------------------------------
class Report(SQLModel, table=True):
    """周期报告归档

    payload 保存完整报告 JSON，使前端动画渲染与 htmlrender 出图共用同一数据源，
    避免「网页好看、推送难看」的不一致。
    """

    __tablename__ = "ff_report"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="ff_user.id", index=True)
    # 周期标识：daily / weekly / monthly / quarterly / yearly / custom
    period: str = Field(default="monthly", max_length=16, index=True)
    # 周期起止（含起，含止）
    period_start: datetime = Field(index=True)
    period_end: datetime = Field(index=True)
    # 展示用标题，如「2026年3月财务报告」
    title: str = Field(default="", max_length=128)

    # 完整报告数据（两大核心模块）
    payload: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # 摘要文本，用于推送
    summary_text: str = Field(default="", sa_column=Column(Text))
    # 渲染后的图片路径
    image_path: str = Field(default="", max_length=512)
    # HTML 快照路径（归档备查）
    html_path: str = Field(default="", max_length=512)

    status: ReportStatus = Field(default=ReportStatus.PENDING, index=True)
    # 生成耗时（毫秒），论文性能指标
    elapsed_ms: int = Field(default=0)
    error_message: str = Field(default="", max_length=512)

    # 是否已推送到协议端
    is_pushed: bool = Field(default=False, index=True)
    pushed_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=_now_utc, index=True)


# ------------------------------------------------------------------------------
# 税务风险自检结果
# ------------------------------------------------------------------------------
class TaxCheckResult(SQLModel, table=True):
    """税检明细项，每次体检生成多条"""

    __tablename__ = "ff_tax_check"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="ff_user.id", index=True)
    # 所属报告（可为空，表示独立体检）
    report_id: Optional[int] = Field(default=None, foreign_key="ff_report.id", index=True)

    # 检查项标识，如 vat_burden / invoice_mismatch / personal_mixed
    check_key: str = Field(max_length=64, index=True)
    # 检查项名称
    title: str = Field(max_length=128)
    # 风险等级
    level: RiskLevel = Field(default=RiskLevel.INFO, index=True)
    # 实测值（如税负率）
    actual_value: float = Field(default=0.0)
    # 预警阈值
    threshold_value: float = Field(default=0.0)
    # 说明
    description: str = Field(default="", max_length=512)
    # 优化建议
    suggestion: str = Field(default="", max_length=512)
    # 政策依据（RAG 检索来源，可解释性）
    policy_basis: str = Field(default="", max_length=1024)
    # 建议引用的政策文档 ID
    policy_doc_id: Optional[int] = Field(default=None)

    created_at: datetime = Field(default_factory=_now_utc, index=True)


# ------------------------------------------------------------------------------
# AI 建议与采纳记录（论文核心指标来源）
# ------------------------------------------------------------------------------
class Suggestion(SQLModel, table=True):
    """AI 生成的建议及其采纳情况

    「建议采纳率」= adopted 数量 / 已处理总数，是论文实验章节的量化指标。
    对照组实验（有 AI 建议 vs 无）也依赖此表。
    """

    __tablename__ = "ff_suggestion"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="ff_user.id", index=True)
    kind: SuggestionKind = Field(default=SuggestionKind.CATEGORY, index=True)

    # 关联对象：流水/报告/税检项
    target_type: str = Field(default="", max_length=32)
    target_id: Optional[int] = Field(default=None)

    # 建议内容
    content: str = Field(default="", max_length=1024)
    # 建议的结构化载荷（如推荐的 category_id）
    payload: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # AI 置信度
    confidence: float = Field(default=0.0)
    # 依据说明
    reason: str = Field(default="", max_length=512)

    # 采纳状态
    state: AdoptionState = Field(default=AdoptionState.PENDING, index=True)
    # 处理时间
    resolved_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=_now_utc, index=True)


# ------------------------------------------------------------------------------
# 审计日志
# ------------------------------------------------------------------------------
class AuditLog(SQLModel, table=True):
    """审计日志：谁在何时做了什么、AI 给了什么、用户是否采纳

    论文「建议采纳率」与全部行为统计的数据底座。
    """

    __tablename__ = "ff_audit_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="ff_user.id", index=True)

    # 动作类型：create_transaction / update_transaction / delete_transaction /
    #          generate_report / adopt_suggestion / reject_suggestion /
    #          update_config / import_bill / push_report
    action: str = Field(max_length=64, index=True)
    # 操作对象类型
    target_type: str = Field(default="", max_length=32)
    target_id: Optional[int] = Field(default=None)

    # 操作者：user / ai / system / scheduler
    operator: str = Field(default="user", max_length=32)
    # 详情
    detail: str = Field(default="", max_length=1024)
    extra: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # 客户端的请求来源（web / bot）
    channel: str = Field(default="web", max_length=16)
    created_at: datetime = Field(default_factory=_now_utc, index=True)


# ------------------------------------------------------------------------------
# 政策知识库
# ------------------------------------------------------------------------------
class PolicyDoc(SQLModel, table=True):
    """政策文档，供 RAG 检索

    policy 更新时只需增删本表数据，无需改动代码逻辑。
    """

    __tablename__ = "ff_policy_doc"

    id: Optional[int] = Field(default=None, primary_key=True)
    # 政策标题
    title: str = Field(max_length=256, index=True)
    # 发文机关
    issuer: str = Field(default="", max_length=128)
    # 文号
    doc_number: str = Field(default="", max_length=128)
    # 分类标签：vat / income_tax / deduction / threshold ...
    tags: str = Field(default="", max_length=256, index=True)
    # 生效日期
    effective_date: Optional[date] = Field(default=None)
    # 正文内容（检索主体）
    content: str = Field(default="", sa_column=Column(Text))
    # 原文链接
    source_url: str = Field(default="", max_length=512)
    # 是否已向量化
    is_embedded: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_now_utc)


# ------------------------------------------------------------------------------
# 导入批次（账单批量导入去重与追溯）
# ------------------------------------------------------------------------------
class ImportBatch(SQLModel, table=True):
    """账单导入批次，记录一次 OCR/CSV 导入的结果，支持回滚"""

    __tablename__ = "ff_import_batch"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="ff_user.id", index=True)
    # 来源类型：ocr / csv
    source_type: str = Field(default="csv", max_length=16)
    # 原始文件名
    filename: str = Field(default="", max_length=256)
    # 总解析条数
    total_count: int = Field(default=0)
    # 成功入库条数
    imported_count: int = Field(default=0)
    # 因重复被跳过条数
    duplicate_count: int = Field(default=0)
    # 需人工确认条数
    pending_count: int = Field(default=0)
    # 解析明细（含每条的处理结果）
    detail: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now_utc, index=True)


# ------------------------------------------------------------------------------
# 系统状态（键值对，存调度器/初始化标记等运行时状态）
# ------------------------------------------------------------------------------
class SystemState(SQLModel, table=True):
    """系统运行时状态键值表"""

    __tablename__ = "ff_system_state"

    key: str = Field(primary_key=True, max_length=64)
    value: str = Field(default="", max_length=1024)
    updated_at: datetime = Field(default_factory=_now_utc)


__all__ = [
    "TransactionDirection",
    "TransactionSource",
    "TaxpayerType",
    "RiskLevel",
    "ReportStatus",
    "SuggestionKind",
    "AdoptionState",
    "User",
    "Account",
    "Category",
    "Transaction",
    "Subscription",
    "Budget",
    "Report",
    "TaxCheckResult",
    "Suggestion",
    "AuditLog",
    "PolicyDoc",
    "ImportBatch",
    "SystemState",
]
