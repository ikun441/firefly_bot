"""记账类别接口。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import TransactionDirection
from ..db.session import get_session
from ..services import audit as audit_service
from ..services import ledger as ledger_service
from .deps import fail, get_or_create_user, ok, to_dict

router = APIRouter(tags=["category"])


class CategoryIn(BaseModel):
    name: str
    direction: str = "expense"
    parent_id: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    tax_account: Optional[str] = None
    deductible: bool = False


@router.get("/categories")
async def list_categories(
    session: AsyncSession = Depends(get_session),
    direction: Optional[str] = Query(default=None, description="expense / income"),
) -> Dict[str, Any]:
    """类别列表（含系统预设与用户自定义）。"""
    user = await get_or_create_user(session)
    categories = await ledger_service.list_categories(session, user_id=user.id)

    rows: List[Dict[str, Any]] = []
    for category in categories:
        if direction and category.direction != direction:
            continue
        row = to_dict(category)
        row["direction"] = category.direction.value if hasattr(category.direction, "value") else str(category.direction)
        rows.append(row)

    return ok(rows, total=len(rows))


@router.post("/categories")
async def create_category(
    payload: CategoryIn,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    user = await get_or_create_user(session)

    existing = await ledger_service.get_category_by_name(session, user_id=user.id, name=payload.name)
    if existing is not None:
        return fail(f"类别「{payload.name}」已存在")

    try:
        direction = TransactionDirection(payload.direction)
    except ValueError:
        return fail(f"无效的收支方向: {payload.direction}")

    category = await ledger_service.create_category(
        session,
        user_id=user.id,
        name=payload.name,
        direction=direction,
        parent_id=payload.parent_id,
        icon=payload.icon or "🏷️",
        color=payload.color,
        tax_account=payload.tax_account,
        deductible=payload.deductible,
        is_ai_suggested=False,
    )

    await audit_service.log_action(
        session,
        "create_category",
        user_id=user.id,
        target_type="category",
        target_id=category.id,
        operator="user",
        detail=f"新增类别「{payload.name}」",
        channel="web",
    )

    row = to_dict(category)
    row["direction"] = direction.value
    return ok(row)


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: str,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    user = await get_or_create_user(session)

    from sqlmodel import select

    from ..db.models import Category, Transaction

    category = (
        await session.execute(
            select(Category).where(Category.id == category_id, Category.user_id == user.id)
        )
    ).scalars().first()

    if category is None:
        return fail("类别不存在", code=404)
    if category.is_system:
        return fail("系统预设类别不可删除")

    used = (
        await session.execute(
            select(Transaction.id).where(Transaction.category_id == category_id).limit(1)
        )
    ).scalars().first()
    if used is not None:
        return fail("该类别下已有流水记录，无法删除")

    await session.delete(category)
    await session.commit()

    await audit_service.log_action(
        session,
        "delete_category",
        user_id=user.id,
        target_type="category",
        target_id=category_id,
        operator="user",
        detail=f"删除类别「{category.name}」",
        channel="web",
    )
    return ok({"id": category_id, "deleted": True})
