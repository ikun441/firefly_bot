"""流萤 Firefly · 记账领域服务

职责：
    * 流水增删改查（含待确认状态流）
    * 重复检测（金额 + 时间窗口 + 对手方模糊匹配，LLM 兜底在 ai 层）
    * 账户余额计算
    * 各类聚合查询（按类别/时间/对手方）

本模块是 Web API 与 Bot 插件的共同底座，不依赖 FastAPI。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rapidfuzz import fuzz
from sqlalchemy import case, func, or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import get_config
from ..db.models import (
    Account,
    Category,
    Transaction,
    TransactionDirection,
    TransactionSource,
)


# ------------------------------------------------------------------------------
# 时间区间工具
# ------------------------------------------------------------------------------
def period_bounds(
    period: str,
    *,
    reference: Optional[datetime] = None,
    custom_days: Optional[int] = None,
) -> Tuple[datetime, datetime]:
    """计算周期的起止时间（左闭右开）

    Args:
        period: daily / weekly / monthly / quarterly / yearly / custom
        reference: 参考时间点，默认当前时间
        custom_days: period="custom" 时必须提供

    Returns:
        (start, end) 均为带 UTC 时区的 datetime，end 为开区间
    """
    ref = reference or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)

    if period == "daily":
        start = ref.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif period == "weekly":
        # 周一为一周的开始
        start = (ref - timedelta(days=ref.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = start + timedelta(days=7)
    elif period == "monthly":
        start = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # 下月一号
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
    elif period == "quarterly":
        quarter_start_month = ((ref.month - 1) // 3) * 3 + 1
        start = ref.replace(
            month=quarter_start_month, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        month_after = quarter_start_month + 3
        if month_after > 12:
            end = start.replace(year=start.year + 1, month=month_after - 12)
        else:
            end = start.replace(month=month_after)
    elif period == "yearly":
        start = ref.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1)
    elif period == "custom":
        days = custom_days or 30
        end = ref
        start = end - timedelta(days=days)
    else:
        raise ValueError(f"未知的周期类型: {period}")

    return start, end


def previous_period_bounds(period: str, start: datetime, end: datetime) -> Tuple[datetime, datetime]:
    """计算上一个等长周期，用于环比对比"""
    span = end - start
    return start - span, start


def format_period_label(period: str, start: datetime, end: datetime) -> str:
    """生成人类可读的周期标题"""
    # 周期边界按 UTC 存储，展示时按配置时区呈现
    local_start = start
    local_end = end - timedelta(seconds=1)

    if period == "daily":
        return local_start.strftime("%Y年%m月%d日")
    if period == "weekly":
        return f"{local_start.strftime('%Y年%m月%d日')} ~ {local_end.strftime('%m月%d日')} 周报"
    if period == "monthly":
        return local_start.strftime("%Y年%m月")
    if period == "quarterly":
        quarter = (local_start.month - 1) // 3 + 1
        return f"{local_start.year}年 第{quarter}季度"
    if period == "yearly":
        return f"{local_start.year}年"
    days = (end - start).days
    return f"近{days}天"


# ------------------------------------------------------------------------------
# 流水写入
# ------------------------------------------------------------------------------

TZ_LOCAL = timezone(timedelta(hours=8))  # Asia/Shanghai，报告周期按本地日历切分


def ensure_aware(value: Optional[datetime]) -> datetime:
    """把可能为 naive 的时间统一成带时区的 UTC。

    SQLModel/SQLAlchemy 在写库时强制要求带时区（否则抛
    ``Datetime values must have timezone information``）。
    前端表单传来的 ``2026-10-12 12:30`` 是本地时间，必须按本地时区补全，
    否则「10 月 12 日中午」会被当成 UTC 而整体偏移 8 小时，
    跨月时会导致月度报告把一笔账算到上一个月。
    """
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ_LOCAL).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


async def create_transaction(
    session: AsyncSession,
    *,
    user_id: int,
    amount: float,
    direction: TransactionDirection = TransactionDirection.EXPENSE,
    category_id: Optional[int] = None,
    account_id: Optional[int] = None,
    trade_time: Optional[datetime] = None,
    counterparty: str = "",
    memo: str = "",
    raw_input: str = "",
    source: TransactionSource = TransactionSource.MANUAL,
    confidence: float = 1.0,
    is_confirmed: bool = True,
) -> Transaction:
    """创建一条流水

    金额统一取绝对值存储，方向由 direction 表达，避免正负号在聚合时出错。
    """
    txn = Transaction(
        user_id=user_id,
        amount=abs(float(amount)),
        direction=direction,
        category_id=category_id,
        account_id=account_id,
        trade_time=ensure_aware(trade_time),
        counterparty=(counterparty or "")[:128],
        memo=(memo or "")[:512],
        raw_input=(raw_input or "")[:1024],
        source=source,
        confidence=confidence,
        is_confirmed=is_confirmed,
    )
    session.add(txn)
    await session.flush()
    return txn


async def get_transaction(
    session: AsyncSession, txn_id: int, user_id: Optional[int] = None
) -> Optional[Transaction]:
    """按 ID 取流水"""
    stmt = select(Transaction).where(Transaction.id == txn_id)
    if user_id is not None:
        stmt = stmt.where(Transaction.user_id == user_id)
    result = await session.exec(stmt)
    return result.first()


async def update_transaction(
    session: AsyncSession,
    txn_id: int,
    *,
    user_id: Optional[int] = None,
    **fields: Any,
) -> Optional[Transaction]:
    """更新流水字段（白名单）"""
    txn = await get_transaction(session, txn_id, user_id)
    if txn is None:
        return None

    allowed = {
        "amount",
        "direction",
        "category_id",
        "account_id",
        "trade_time",
        "counterparty",
        "memo",
        "is_confirmed",
        "is_anomaly",
        "anomaly_reason",
        "is_personal_mixed",
        "has_invoice",
    }
    for key, value in fields.items():
        if key in allowed and value is not None:
            if key == "amount":
                value = abs(float(value))
            setattr(txn, key, value)

    txn.updated_at = datetime.now(timezone.utc)
    session.add(txn)
    await session.flush()
    return txn


async def delete_transaction(
    session: AsyncSession, txn_id: int, user_id: Optional[int] = None
) -> bool:
    """删除流水"""
    txn = await get_transaction(session, txn_id, user_id)
    if txn is None:
        return False
    await session.delete(txn)
    return True


async def confirm_transaction(
    session: AsyncSession, txn_id: int, user_id: Optional[int] = None
) -> Optional[Transaction]:
    """把「待确认」流水标记为已确认"""
    txn = await get_transaction(session, txn_id, user_id)
    if txn is None:
        return None
    txn.is_confirmed = True
    txn.updated_at = datetime.now(timezone.utc)
    session.add(txn)
    await session.flush()
    return txn


# ------------------------------------------------------------------------------
# 查询与筛选
# ------------------------------------------------------------------------------
async def list_transactions(
    session: AsyncSession,
    *,
    user_id: int,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    category_id: Optional[int] = None,
    direction: Optional[TransactionDirection] = None,
    keyword: Optional[str] = None,
    only_pending: bool = False,
    only_anomaly: bool = False,
    limit: int = 100,
    offset: int = 0,
    order_desc: bool = True,
) -> List[Transaction]:
    """条件查询流水列表"""
    stmt = select(Transaction).where(Transaction.user_id == user_id)

    if start is not None:
        stmt = stmt.where(Transaction.trade_time >= start)
    if end is not None:
        stmt = stmt.where(Transaction.trade_time < end)
    if category_id is not None:
        stmt = stmt.where(Transaction.category_id == category_id)
    if direction is not None:
        stmt = stmt.where(Transaction.direction == direction)
    if only_pending:
        stmt = stmt.where(Transaction.is_confirmed == False)  # noqa: E712
    if only_anomaly:
        stmt = stmt.where(Transaction.is_anomaly == True)  # noqa: E712
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            (Transaction.memo.like(like)) | (Transaction.counterparty.like(like))  # type: ignore[attr-defined]
        )

    order_column = (
        Transaction.trade_time.desc() if order_desc else Transaction.trade_time.asc()
    )
    stmt = stmt.order_by(order_column).offset(offset).limit(limit)
    result = await session.exec(stmt)
    return list(result.all())


async def count_transactions(
    session: AsyncSession,
    *,
    user_id: int,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    category_id: Optional[int] = None,
    direction: Optional[str] = None,
    keyword: Optional[str] = None,
    only_pending: bool = False,
    only_anomaly: bool = False,
) -> int:
    """统计流水条数。

    过滤条件与 ``list_transactions`` 保持一一对应 ——
    否则分页组件会算出与实际列表不符的页数。
    """
    stmt = select(func.count(Transaction.id)).where(Transaction.user_id == user_id)
    if start is not None:
        stmt = stmt.where(Transaction.trade_time >= start)
    if end is not None:
        stmt = stmt.where(Transaction.trade_time < end)
    if category_id is not None:
        stmt = stmt.where(Transaction.category_id == category_id)
    if direction:
        stmt = stmt.where(Transaction.direction == direction)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                Transaction.memo.like(like),
                Transaction.counterparty.like(like),
            )
        )
    if only_pending:
        stmt = stmt.where(Transaction.is_confirmed == False)  # noqa: E712
    if only_anomaly:
        stmt = stmt.where(Transaction.is_anomaly == True)  # noqa: E712
    result = await session.exec(stmt)
    return int(result.one() or 0)


# ------------------------------------------------------------------------------
# 聚合统计
# ------------------------------------------------------------------------------
async def sum_by_direction(
    session: AsyncSession,
    *,
    user_id: int,
    start: datetime,
    end: datetime,
    include_pending: bool = True,
) -> Dict[str, float]:
    """统计区间内的收入与支出总额

    默认包含待确认流水，否则报告数字会与用户实际感知不符。
    """
    stmt = (
        select(Transaction.direction, func.sum(Transaction.amount))
        .where(
            Transaction.user_id == user_id,
            Transaction.trade_time >= start,
            Transaction.trade_time < end,
        )
        .group_by(Transaction.direction)
    )
    if not include_pending:
        stmt = stmt.where(Transaction.is_confirmed == True)  # noqa: E712

    result = await session.exec(stmt)
    totals = {"income": 0.0, "expense": 0.0}
    for direction, total in result.all():
        key = direction.value if hasattr(direction, "value") else str(direction)
        if key in totals:
            totals[key] = float(total or 0.0)

    totals["net"] = totals["income"] - totals["expense"]
    return totals


async def aggregate_by_category(
    session: AsyncSession,
    *,
    user_id: int,
    start: datetime,
    end: datetime,
    direction: TransactionDirection = TransactionDirection.EXPENSE,
    top_n: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """按类别聚合，返回金额降序列表（含占比）

    占比是「结构失衡」税检项与饼图的直接数据源。
    """
    join_cond = Transaction.category_id == Category.id
    stmt = (
        select(
            Transaction.category_id,
            Category.name,
            Category.icon,
            Category.color,
            func.sum(Transaction.amount).label("total"),
            func.count(Transaction.id).label("cnt"),
        )
        .join(Category, join_cond, isouter=True)
        .where(
            Transaction.user_id == user_id,
            Transaction.trade_time >= start,
            Transaction.trade_time < end,
            Transaction.direction == direction,
        )
        .group_by(Transaction.category_id, Category.name, Category.icon, Category.color)
        .order_by(func.sum(Transaction.amount).desc())
    )

    result = await session.exec(stmt)
    rows = result.all()

    grand_total = sum(float(row.total or 0.0) for row in rows)
    items: List[Dict[str, Any]] = []
    for row in rows:
        total = float(row.total or 0.0)
        items.append(
            {
                "category_id": row.category_id,
                "name": row.name or "未分类",
                "icon": row.icon or "📦",
                "color": row.color or "#94a3b8",
                "amount": round(total, 2),
                "count": int(row.cnt or 0),
                "ratio": round(total / grand_total * 100, 2) if grand_total else 0.0,
            }
        )

    if top_n is not None:
        items = items[:top_n]
    return items


async def aggregate_by_day(
    session: AsyncSession,
    *,
    user_id: int,
    start: datetime,
    end: datetime,
    direction: TransactionDirection = TransactionDirection.EXPENSE,
) -> List[Dict[str, Any]]:
    """按天聚合，用于趋势折线图"""
    day_expr = func.date(Transaction.trade_time)
    stmt = (
        select(day_expr.label("day"), func.sum(Transaction.amount).label("total"))
        .where(
            Transaction.user_id == user_id,
            Transaction.trade_time >= start,
            Transaction.trade_time < end,
            Transaction.direction == direction,
        )
        .group_by(day_expr)
        .order_by(day_expr)
    )
    result = await session.exec(stmt)
    return [
        {"date": str(row.day), "amount": round(float(row.total or 0.0), 2)}
        for row in result.all()
    ]


async def aggregate_by_counterparty(
    session: AsyncSession,
    *,
    user_id: int,
    start: datetime,
    end: datetime,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """按对手方聚合，识别大额供应商"""
    stmt = (
        select(
            Transaction.counterparty,
            func.sum(Transaction.amount).label("total"),
            func.count(Transaction.id).label("cnt"),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.trade_time >= start,
            Transaction.trade_time < end,
            Transaction.direction == TransactionDirection.EXPENSE,
            Transaction.counterparty != "",
        )
        .group_by(Transaction.counterparty)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(limit)
    )
    result = await session.exec(stmt)
    return [
        {
            "counterparty": row.counterparty,
            "amount": round(float(row.total or 0.0), 2),
            "count": int(row.cnt or 0),
        }
        for row in result.all()
    ]


# ------------------------------------------------------------------------------
# 账户余额
# ------------------------------------------------------------------------------
async def list_accounts(session: AsyncSession, *, user_id: int) -> List[Account]:
    """列出用户的全部资金账户。"""
    result = await session.exec(
        select(Account).where(Account.user_id == user_id).order_by(Account.id)
    )
    return list(result.all())


async def ensure_default_account(session: AsyncSession, *, user_id: int) -> int:
    """为新用户初始化一个默认资金账户。

    没有账户时 ``get_account_balances`` 返回空列表，前端的「账户余额」会恒为 ¥0.00，
    流水也无处归属。因此与默认类别一样，首次访问时自动播种一个「默认账户」。
    返回新建数量（已存在则返回 0）。
    """
    existing = await list_accounts(session, user_id=user_id)
    if existing:
        return 0

    session.add(
        Account(
            user_id=user_id,
            name="默认账户",
            kind="cash",
            initial_balance=0.0,
            include_in_net=True,
        )
    )
    await session.flush()
    return 1


async def get_account_balances(
    session: AsyncSession, *, user_id: int
) -> List[Dict[str, Any]]:
    """计算各账户当前余额 = 初始余额 + 收入 - 支出"""
    acc_stmt = select(Account).where(Account.user_id == user_id)
    acc_result = await session.exec(acc_stmt)
    accounts = list(acc_result.all())

    income_sum = func.sum(
        case(
            (Transaction.direction == TransactionDirection.INCOME, Transaction.amount),
            else_=0.0,
        )
    )
    expense_sum = func.sum(
        case(
            (Transaction.direction == TransactionDirection.EXPENSE, Transaction.amount),
            else_=0.0,
        )
    )
    bal_stmt = select(
        Transaction.account_id, income_sum.label("income"), expense_sum.label("expense")
    ).where(Transaction.user_id == user_id).group_by(Transaction.account_id)
    bal_result = await session.exec(bal_stmt)
    delta: Dict[Optional[int], Tuple[float, float]] = {
        row.account_id: (float(row.income or 0.0), float(row.expense or 0.0))
        for row in bal_result.all()
    }

    output: List[Dict[str, Any]] = []
    for acc in accounts:
        inc, exp = delta.get(acc.id, (0.0, 0.0))
        output.append(
            {
                "id": acc.id,
                "name": acc.name,
                "kind": acc.kind,
                "balance": round(acc.initial_balance + inc - exp, 2),
                # 初始余额与流水净额也要带出来：账本页要展示「初始余额 ¥X · 流入/流出」，
                # 只给最终 balance 的话前端只能拿它当初始余额显示（并会显示成 0），
                # 用户看到「初始余额 ¥0.00 · 当前余额 ¥11,800.00」会以为数据错了。
                "initial_balance": round(float(acc.initial_balance or 0.0), 2),
                "income": round(inc, 2),
                "expense": round(exp, 2),
                "include_in_net": acc.include_in_net,
            }
        )
    return output


async def get_net_assets(session: AsyncSession, *, user_id: int) -> float:
    """净资产 = 计入统计的账户余额之和"""
    balances = await get_account_balances(session, user_id=user_id)
    return round(sum(b["balance"] for b in balances if b["include_in_net"]), 2)


async def get_account(
    session: AsyncSession, account_id: int, *, user_id: Optional[int] = None
) -> Optional[Account]:
    """按主键取账户；传 ``user_id`` 时顺带做归属校验。"""
    account = await session.get(Account, account_id)
    if account is None:
        return None
    if user_id is not None and account.user_id != user_id:
        return None
    return account


async def create_account(
    session: AsyncSession,
    *,
    user_id: int,
    name: str,
    kind: str = "cash",
    initial_balance: float = 0.0,
    include_in_net: bool = True,
) -> Account:
    """新建资金账户。

    ``name`` 做去空白与长度截断：账户名会被前端当作下拉选项文本渲染，
    长度失控会把整个列表撑变形。
    """
    account = Account(
        user_id=user_id,
        name=(name or "").strip()[:64] or "未命名账户",
        kind=(kind or "cash").strip()[:32] or "cash",
        initial_balance=float(initial_balance or 0.0),
        include_in_net=bool(include_in_net),
    )
    session.add(account)
    await session.flush()
    return account


async def update_account(
    session: AsyncSession,
    account_id: int,
    *,
    user_id: int,
    **fields: Any,
) -> Optional[Account]:
    """更新账户。字段走白名单，避免调用方顺手改掉 ``user_id`` 造成越权。"""
    account = await get_account(session, account_id, user_id=user_id)
    if account is None:
        return None

    allowed = {"name", "kind", "initial_balance", "include_in_net"}
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key == "name":
            account.name = str(value).strip()[:64] or account.name
        elif key == "kind":
            account.kind = str(value).strip()[:32] or account.kind
        elif key == "initial_balance":
            account.initial_balance = float(value)
        elif key == "include_in_net":
            account.include_in_net = bool(value)

    session.add(account)
    await session.flush()
    return account


async def count_account_transactions(
    session: AsyncSession, *, user_id: int, account_id: int
) -> int:
    """统计挂在某账户下的流水笔数。

    删除账户前必须查这个：SQLite 默认不强制外键，
    直接删掉账户会让那些流水变成 ``account_id`` 指向不存在的行，
    余额统计里它们就凭空消失了 —— 钱没少算，但账对不上。
    """
    stmt = select(func.count(Transaction.id)).where(
        Transaction.user_id == user_id,
        Transaction.account_id == account_id,
    )
    result = await session.exec(stmt)
    return int(result.one() or 0)


async def delete_account(
    session: AsyncSession, account_id: int, *, user_id: int
) -> Tuple[bool, str]:
    """删除账户；返回 ``(是否成功, 原因)``。

    规则（与类别删除保持一致的安全取向）：
      - 账户不存在或不属于该用户 → 拒绝；
      - 是最后一个账户 → 拒绝（全删光之后新流水无处归属，
        「账户余额」会重新变回恒为 0 的老问题）；
      - 名下仍有流水 → 拒绝并提示笔数，让用户先迁移或删除流水。
    """
    account = await get_account(session, account_id, user_id=user_id)
    if account is None:
        return False, "账户不存在"

    total = len(await list_accounts(session, user_id=user_id))
    if total <= 1:
        return False, "至少保留一个账户，无法删除最后一个"

    used = await count_account_transactions(
        session, user_id=user_id, account_id=account_id
    )
    if used:
        return False, f"该账户下还有 {used} 笔流水，请先迁移或删除后再删账户"

    await session.delete(account)
    await session.flush()
    return True, "已删除"


# ------------------------------------------------------------------------------
# 去重检测
# ------------------------------------------------------------------------------
async def find_duplicates(
    session: AsyncSession,
    *,
    user_id: int,
    amount: float,
    trade_time: datetime,
    counterparty: str = "",
    direction: Optional[TransactionDirection] = None,
    window_minutes: Optional[int] = None,
    exclude_id: Optional[int] = None,
    account_id: Optional[int] = None,
) -> List[Transaction]:
    """查找疑似重复流水

    规则（保守，宁可漏报不可误报）：
        方向相同 且 金额完全一致 且 时间差在窗口内
        且 （对手方为空 或 模糊相似度 >= 80）

    为什么要带 ``direction``：
        一笔 26000 的收入和一笔 26000 的支出，若时间相近，
        只比金额会把它们判成重复 —— 而重复检测的标记会直接影响
        报告里的「重复记账」风险项，误报等于给用户一个假警报。
        方向不同必然不是同一笔，这个条件零成本且能挡掉大部分误判。

    关于对手方为空的分支：
        对手方为空时仍返回候选，是因为手工记账常常不填对手方；
        此时「金额一致 + 时间接近」已经是足够强的信号。
        调用方（导入服务）会把结果放进待确认区，而不是静默丢弃。
    """
    config = get_config()
    window = window_minutes if window_minutes is not None else config.ledger.duplicate_window_minutes
    delta = timedelta(minutes=window)

    stmt = select(Transaction).where(
        Transaction.user_id == user_id,
        Transaction.amount == abs(float(amount)),
        Transaction.trade_time >= trade_time - delta,
        Transaction.trade_time <= trade_time + delta,
    )
    if direction is not None:
        stmt = stmt.where(Transaction.direction == direction)
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    if exclude_id is not None:
        stmt = stmt.where(Transaction.id != exclude_id)

    result = await session.exec(stmt)
    candidates = list(result.all())

    # counterparty 允许为 None（前端不填对手方时就是 None），
    # 这里统一收敛成字符串，否则下面的 .strip() 会抛 AttributeError。
    subject = (counterparty or "").strip()
    if not subject:
        return candidates

    # 两边都要有对手方才做相似度判断：候选对手方为空时无法比较，
    # 但这不代表「一定是重复」——那只是信息缺失。
    # 只在双方都有可比对的值时才用相似度决策。
    return [
        c
        for c in candidates
        if (c.counterparty or "").strip()
        and fuzz.token_set_ratio(subject, c.counterparty) >= 80
    ]


async def mark_duplicate(
    session: AsyncSession, txn_id: int, is_duplicate: bool = True
) -> Optional[Transaction]:
    """标记重复流水"""
    txn = await get_transaction(session, txn_id)
    if txn is None:
        return None
    txn.is_duplicate = is_duplicate
    session.add(txn)
    await session.flush()
    return txn


# ------------------------------------------------------------------------------
# 类别
# ------------------------------------------------------------------------------
async def list_categories(
    session: AsyncSession,
    *,
    user_id: int,
    direction: Optional[TransactionDirection] = None,
) -> List[Category]:
    """列出类别（按排序权重）"""
    stmt = select(Category).where(Category.user_id == user_id)
    if direction is not None:
        stmt = stmt.where(Category.direction == direction)
    stmt = stmt.order_by(col(Category.sort_order).asc(), col(Category.id).asc())
    result = await session.exec(stmt)
    return list(result.all())


async def get_category_by_name(
    session: AsyncSession, *, user_id: int, name: str
) -> Optional[Category]:
    """按名称查找类别（自然语言记账时用于匹配）"""
    stmt = select(Category).where(
        Category.user_id == user_id, Category.name == name.strip()
    )
    result = await session.exec(stmt)
    return result.first()


async def create_category(
    session: AsyncSession,
    *,
    user_id: int,
    name: str,
    direction: TransactionDirection = TransactionDirection.EXPENSE,
    icon: str = "📦",
    color: str = "#5eead4",
    tax_account: Optional[str] = "",
    deductible: bool = False,
    parent_id: Optional[int] = None,
    is_ai_suggested: bool = False,
    is_system: bool = False,
) -> Category:
    """创建类别

    ``is_system`` 只有内置类别的播种流程会传 True；
    用户自己新建的类别一律为 False（否则用户就删不掉自己建错的类别了）。
    """
    category = Category(
        user_id=user_id,
        name=(name or "").strip()[:64],
        direction=direction,
        icon=icon,
        color=color,
        tax_account=(tax_account or "")[:64],
        deductible=deductible,
        parent_id=parent_id,
        is_ai_suggested=is_ai_suggested,
        is_system=is_system,
    )
    session.add(category)
    await session.flush()
    return category


async def ensure_default_categories(session: AsyncSession, *, user_id: int) -> int:
    """为新用户初始化内置类别

    tax_account / deductible 字段把记账类别与税务口径挂钩，
    使税检与税优建议可以直接基于流水计算，无需二次映射。

    这些类别一律标记 ``is_system=True``：它们是系统播种的，
    误删会连带破坏税检口径（税检靠 ``Category.tax_account`` 聚合），
    所以从源头就应当被保护，而不是等用户删掉后再补救。
    """
    existing = await list_categories(session, user_id=user_id)
    if existing:
        return 0

    defaults: Sequence[Dict[str, Any]] = [
        # 支出类
        {"name": "餐饮", "icon": "🍜", "color": "#f97316", "tax_account": "业务招待费", "deductible": True},
        {"name": "交通", "icon": "🚕", "color": "#38bdf8", "tax_account": "差旅费", "deductible": True},
        {"name": "办公", "icon": "💼", "color": "#a78bfa", "tax_account": "办公费", "deductible": True},
        {"name": "房租", "icon": "🏠", "color": "#f472b6", "tax_account": "租赁费", "deductible": True},
        {"name": "水电", "icon": "💡", "color": "#fbbf24", "tax_account": "水电费", "deductible": True},
        {"name": "通信", "icon": "📱", "color": "#34d399", "tax_account": "通信费", "deductible": True},
        {"name": "采购", "icon": "📦", "color": "#22d3ee", "tax_account": "存货采购", "deductible": True},
        {"name": "宣传推广", "icon": "📣", "color": "#fb7185", "tax_account": "广告宣传费", "deductible": True},
        {"name": "培训学习", "icon": "📚", "color": "#818cf8", "tax_account": "职工教育经费", "deductible": True},
        {"name": "医疗", "icon": "🏥", "color": "#f87171", "tax_account": "", "deductible": False},
        {"name": "娱乐", "icon": "🎮", "color": "#c084fc", "tax_account": "", "deductible": False},
        {"name": "购物", "icon": "🛍️", "color": "#fda4af", "tax_account": "", "deductible": False},
        {"name": "其他支出", "icon": "💸", "color": "#94a3b8", "tax_account": "", "deductible": False},
    ]
    order = 0
    for item in defaults:
        order += 10
        await create_category(
            session,
            user_id=user_id,
            name=item["name"],
            direction=TransactionDirection.EXPENSE,
            icon=item["icon"],
            color=item["color"],
            tax_account=item["tax_account"],
            deductible=item["deductible"],
            is_system=True,
        )

    income_defaults: Sequence[Dict[str, Any]] = [
        {"name": "工资", "icon": "💰", "color": "#4ade80"},
        {"name": "服务收入", "icon": "🧾", "color": "#2dd4bf"},
        {"name": "经营收入", "icon": "🏪", "color": "#60a5fa"},
        {"name": "投资收益", "icon": "📈", "color": "#facc15"},
        {"name": "其他收入", "icon": "🎁", "color": "#a3e635"},
    ]
    for item in income_defaults:
        order += 10
        await create_category(
            session,
            user_id=user_id,
            name=item["name"],
            direction=TransactionDirection.INCOME,
            icon=item["icon"],
            color=item["color"],
            is_system=True,
        )

    return len(defaults) + len(income_defaults)


__all__ = [
    "period_bounds",
    "previous_period_bounds",
    "format_period_label",
    "create_transaction",
    "get_transaction",
    "update_transaction",
    "delete_transaction",
    "confirm_transaction",
    "list_transactions",
    "count_transactions",
    "sum_by_direction",
    "aggregate_by_category",
    "aggregate_by_day",
    "aggregate_by_counterparty",
    "get_account_balances",
    "get_net_assets",
    "list_accounts",
    "ensure_default_account",
    "get_account",
    "create_account",
    "update_account",
    "delete_account",
    "count_account_transactions",
    "find_duplicates",
    "mark_duplicate",
    "list_categories",
    "get_category_by_name",
    "create_category",
    "ensure_default_categories",
]
