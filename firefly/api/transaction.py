"""流水（记账）相关接口。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..db.models import TransactionDirection, TransactionSource
from ..db.session import get_session
from ..services import anomaly as anomaly_service
from ..services import audit as audit_service
from ..services import ledger as ledger_service
from ..services.transaction_validation import validate_references
from .deps import MAX_AMOUNT, fail, get_or_create_user, ok, parse_dt, to_dict

router = APIRouter(tags=["transaction"])


class TransactionIn(BaseModel):
    # le=MAX_AMOUNT：必须同时卡上界，否则天文数字能一路污染统计与报告，
    # 详见 deps.MAX_AMOUNT 的说明。
    amount: float = Field(..., gt=0, le=MAX_AMOUNT, allow_inf_nan=False, description="金额，始终为正数")
    direction: str = Field(default="expense", description="expense 支出 / income 收入")
    # 前端传字符串、直接调 API 传数字都要能收，统一转成 int 再入库，
    # 否则 SQLite 里会出现 "1" 与 1 混存的脏数据。
    category_id: Optional[Union[int, str]] = None
    account_id: Optional[Union[int, str]] = None
    trade_time: Optional[str] = None
    counterparty: Optional[str] = None
    memo: Optional[str] = None
    has_invoice: bool = False
    is_confirmed: bool = True


class TransactionPatch(BaseModel):
    amount: Optional[float] = Field(default=None, gt=0, le=MAX_AMOUNT, allow_inf_nan=False)
    direction: Optional[str] = None
    category_id: Optional[Union[int, str]] = None
    account_id: Optional[Union[int, str]] = None
    trade_time: Optional[str] = None
    counterparty: Optional[str] = None
    memo: Optional[str] = None
    has_invoice: Optional[bool] = None
    is_confirmed: Optional[bool] = None


def _as_int(value: Union[int, str, None]) -> Optional[int]:
    """把可选的 id 归一化成 int；空串 / None / 非法值都返回 None。"""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


@router.get("/transactions")
async def list_transactions(
    session: AsyncSession = Depends(get_session),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    category_id: Optional[str] = Query(default=None),
    direction: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    only_pending: bool = Query(default=False),
    only_anomaly: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    account_id: Optional[int] = Query(default=None),
) -> Dict[str, Any]:
    """流水列表（按时间倒序）。"""
    user = await get_or_create_user(session)

    items = await ledger_service.list_transactions(
        session,
        user_id=user.id,
        start=parse_dt(start),
        end=parse_dt(end),
        category_id=category_id,
        direction=direction,
        keyword=keyword,
        only_pending=only_pending,
        only_anomaly=only_anomaly,
        limit=limit,
        offset=offset,
        account_id=account_id,
    )
    total = await ledger_service.count_transactions(
        session,
        user_id=user.id,
        start=parse_dt(start),
        end=parse_dt(end),
        category_id=category_id,
        direction=direction,
        keyword=keyword,
        only_pending=only_pending,
        only_anomaly=only_anomaly,
        account_id=account_id,
    )

    # 附带类别信息，避免前端二次查询
    categories = {c.id: c for c in await ledger_service.list_categories(session, user_id=user.id)}

    rows: List[Dict[str, Any]] = []
    for txn in items:
        row = to_dict(txn)
        category = categories.get(txn.category_id)
        if category is not None:
            row["category_name"] = category.name
            row["category_icon"] = category.icon
            row["category_color"] = category.color
            row["tax_account"] = category.tax_account
        else:
            row["category_name"] = "未分类"
            row["category_icon"] = "❓"
        rows.append(row)

    return ok(rows, total=total, limit=limit, offset=offset)


@router.post("/transactions")
async def create_transaction(
    payload: TransactionIn,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """新增一条流水。"""
    user = await get_or_create_user(session)

    try:
        direction = TransactionDirection(payload.direction)
    except ValueError:
        return fail(f"无效的收支方向: {payload.direction}")

    cfg = get_config()

    # 自动识别类别（未指定时）
    category_id = _as_int(payload.category_id)
    if not category_id and payload.memo:
        categories = await ledger_service.list_categories(session, user_id=user.id)
        for category in categories:
            if category.name and category.name in payload.memo and category.direction == direction:
                category_id = category.id
                break

    account_id = _as_int(payload.account_id)
    try:
        await validate_references(session, user_id=user.id, category_id=category_id, account_id=account_id, direction=direction)
    except ValueError as exc:
        return fail(str(exc))

    txn = await ledger_service.create_transaction(
        session,
        user_id=user.id,
        amount=payload.amount,
        direction=direction,
        category_id=category_id,
        account_id=account_id,
        trade_time=parse_dt(payload.trade_time),
        counterparty=payload.counterparty,
        memo=payload.memo,
        source=TransactionSource.MANUAL,
        is_confirmed=payload.is_confirmed,
        has_invoice=payload.has_invoice,
    )

    # 重复检测
    duplicate = None
    duplicate_hits = await ledger_service.find_duplicates(
        session,
        user_id=user.id,
        amount=payload.amount,
        trade_time=txn.trade_time,
        counterparty=payload.counterparty,
        # 方向必须参与比对：收入与支出金额相同也不可能是同一笔
        direction=txn.direction,
        window_minutes=cfg.ledger.duplicate_window_minutes,
        exclude_id=txn.id,
    )
    if duplicate_hits:
        # 注意：mark_duplicate 的第二个参数是**主键**，不是 ORM 对象。
        # 传 txn 会被 SQLModel 当成 SQL 表达式，抛
        # ArgumentError: SQL expression element or literal value expected。
        await ledger_service.mark_duplicate(session, txn.id, True)
        duplicate = {
            "count": len(duplicate_hits),
            "ids": [d.id for d in duplicate_hits],
        }

    # 异常检测
    analysis = await anomaly_service.analyze_transaction(session, user_id=user.id, txn=txn)

    await audit_service.log_action(
        session,
        "create_transaction",
        user_id=user.id,
        target_type="transaction",
        target_id=txn.id,
        operator="user",
        detail=f"新增{'支出' if direction == TransactionDirection.EXPENSE else '收入'} {payload.amount} 元",
        extra={"after": to_dict(txn)},
        channel="web",
    )

    return ok(
        {
            "transaction": to_dict(txn),
            "duplicate": duplicate,
            "analysis": analysis,
        }
    )


@router.get("/transactions/{txn_id}")
async def get_transaction(
    txn_id: str,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    user = await get_or_create_user(session)
    txn = await ledger_service.get_transaction(session, txn_id, user_id=user.id)
    if txn is None:
        return fail("流水不存在", code=404)
    return ok(to_dict(txn))


@router.patch("/transactions/{txn_id}")
async def update_transaction(
    txn_id: str,
    payload: TransactionPatch,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    user = await get_or_create_user(session)

    existing = await ledger_service.get_transaction(session, txn_id, user_id=user.id)
    if existing is None:
        return fail("流水不存在", code=404)
    before = to_dict(existing)
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None or k in ("category_id", "account_id")}
    if "trade_time" in fields:
        fields["trade_time"] = parse_dt(fields["trade_time"])
    if "direction" in fields:
        try:
            fields["direction"] = TransactionDirection(fields["direction"])
        except ValueError:
            return fail(f"无效的收支方向: {fields['direction']}")
    # id 类字段统一转 int，避免字符串 id 写进整型列
    for key in ("category_id", "account_id"):
        if key in fields:
            fields[key] = _as_int(fields[key])

    try:
        await validate_references(session, user_id=user.id, category_id=fields.get("category_id", existing.category_id), account_id=fields.get("account_id", existing.account_id), direction=fields.get("direction", existing.direction))
    except ValueError as exc:
        return fail(str(exc))

    txn = await ledger_service.update_transaction(session, txn_id, user_id=user.id, **fields)
    if txn is None:
        return fail("流水不存在", code=404)

    await audit_service.log_action(
        session,
        "update_transaction",
        user_id=user.id,
        target_type="transaction",
        target_id=txn.id,
        operator="user",
        detail="修改流水字段: " + ", ".join(fields.keys()),
        extra={"before": before, "after": to_dict(txn), "fields": list(fields)},
        channel="web",
    )
    return ok(to_dict(txn))


@router.delete("/transactions/{txn_id}")
async def delete_transaction(
    txn_id: str,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    user = await get_or_create_user(session)
    existing = await ledger_service.get_transaction(session, txn_id, user_id=user.id)
    if existing is None:
        return fail("流水不存在", code=404)
    before = to_dict(existing)
    deleted = await ledger_service.delete_transaction(session, txn_id, user_id=user.id)
    if not deleted:
        return fail("流水不存在", code=404)

    await audit_service.log_action(
        session,
        "delete_transaction",
        user_id=user.id,
        target_type="transaction",
        target_id=txn_id,
        operator="user",
        detail="删除流水",
        extra={"before": before},
        channel="web",
    )
    return ok({"id": txn_id, "deleted": True})


@router.post("/transactions/{txn_id}/confirm")
async def confirm_transaction(
    txn_id: str,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """确认待确认流水（自然语言记账产生的记录）。"""
    user = await get_or_create_user(session)
    txn = await ledger_service.confirm_transaction(session, txn_id, user_id=user.id)
    if txn is None:
        return fail("流水不存在", code=404)

    await audit_service.log_action(
        session,
        "confirm_transaction",
        user_id=user.id,
        target_type="transaction",
        target_id=txn.id,
        operator="user",
        detail="确认入账",
        channel="web",
    )
    return ok(to_dict(txn))
