"""流萤 Firefly · 资金账户（账本）接口。

「账本」在数据模型里就是 ``Account`` —— 现金、银行卡、微信、支付宝
这些**资金容器**。记一笔流水时必须知道钱从哪个容器进出，
报告里的「账户余额」「净资产」也都由账户聚合而来。

这里提供完整的增删改查，删除规则刻意保守（见 ``delete_account``）：
账户不像类别，删错了会让历史流水的归属凭空消失、余额对不上。
"""
from typing import Any, Dict, Optional, Union

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..services import ledger as ledger_service
from ..services.audit import log_action
from .deps import MAX_AMOUNT, current_user, fail, ok, to_dict
from ..db.session import get_session
from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

router = APIRouter(tags=["account"])


# 账户类型：前端按这个值选图标与配色
ACCOUNT_KINDS = ("cash", "bank", "wechat", "alipay", "credit", "other")

# 与 ACCOUNT_KINDS 放在一起：这两个是同一个概念的机器名与展示名，
# 拆开放容易只改一处，出现「新增了类型但列表里显示成『其他』」。
_KIND_LABELS = {
    "cash": "现金",
    "bank": "银行卡",
    "wechat": "微信",
    "alipay": "支付宝",
    "credit": "信用卡",
    "other": "其他",
}


class AccountIn(BaseModel):
    """新建账户的请求体。"""

    name: str = Field(min_length=1, max_length=64)
    kind: str = "cash"
    # 期初余额可正可负（信用卡欠款即为负），但同样要有上界。
    initial_balance: float = Field(default=0.0, ge=-MAX_AMOUNT, le=MAX_AMOUNT)
    include_in_net: bool = True


class AccountPatch(BaseModel):
    """更新账户：所有字段可选，只改传进来的那几个。"""

    name: Optional[str] = Field(default=None, max_length=64)
    kind: Optional[str] = None
    initial_balance: Optional[float] = Field(default=None, ge=-MAX_AMOUNT, le=MAX_AMOUNT)
    include_in_net: Optional[bool] = None


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


@router.get("/accounts")
async def list_accounts(
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
) -> Dict[str, Any]:
    """账户列表，附带每个账户的当前余额与流水笔数。

    余额不是账户表上的一个列，而是「初始余额 + 收入 - 支出」实时算出来的
    （见 ``ledger.get_account_balances``）。这样做的原因是：
    余额一旦落库成冗余字段，任何一次流水增删改都要记得同步它，
    漏一处就会出现「流水改了、余额没变」的静默错误。
    """
    balances = await ledger_service.get_account_balances(session, user_id=user.id)
    accounts = await ledger_service.list_accounts(session, user_id=user.id)

    # 笔数单独查一次，避免为每个账户各发一条查询（N+1）
    counts: Dict[int, int] = {}
    for acc in accounts:
        counts[acc.id] = await ledger_service.count_account_transactions(
            session, user_id=user.id, account_id=acc.id
        )

    items = []
    for row in balances:
        item = dict(row)
        item["transaction_count"] = counts.get(row["id"], 0)
        item["kind_label"] = _KIND_LABELS.get(item.get("kind") or "cash", "其他")
        items.append(item)

    total_balance = round(sum(float(i.get("balance") or 0.0) for i in items), 2)
    net_total = round(
        sum(float(i.get("balance") or 0.0) for i in items if i.get("include_in_net")), 2
    )

    return ok(
        items,
        total=len(items),
        summary={
            "count": len(items),
            "total_balance": total_balance,
            "net_assets": net_total,
            "excluded_balance": round(total_balance - net_total, 2),
        },
        kinds=[{"value": k, "label": _KIND_LABELS[k]} for k in ACCOUNT_KINDS],
    )


@router.post("/accounts")
async def create_account(
    payload: AccountIn,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
) -> Dict[str, Any]:
    """新建账户。"""
    kind = (payload.kind or "cash").strip()
    if kind not in ACCOUNT_KINDS:
        # 未知类型不拒绝，落到 other —— 前端加新类型时后端不必同步发版
        kind = "other"

    account = await ledger_service.create_account(
        session,
        user_id=user.id,
        name=payload.name,
        kind=kind,
        initial_balance=payload.initial_balance,
        include_in_net=payload.include_in_net,
    )

    await log_action(
        session,
        "create_account",
        user_id=user.id,
        target_type="account",
        target_id=str(account.id),
        operator="user",
        detail=f"新建账户「{account.name}」",
        channel="web",
    )

    return ok(to_dict(account))


@router.get("/accounts/{account_id}")
async def get_account(
    account_id: int,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
) -> Dict[str, Any]:
    """单个账户详情（含余额与笔数）。"""
    account = await ledger_service.get_account(session, account_id, user_id=user.id)
    if account is None:
        return fail("账户不存在", code=404)

    balances = await ledger_service.get_account_balances(session, user_id=user.id)
    row = next((b for b in balances if b["id"] == account_id), None) or {}

    data = to_dict(account)
    data["balance"] = row.get("balance", account.initial_balance)
    data["kind_label"] = _KIND_LABELS.get(account.kind, "其他")
    data["transaction_count"] = await ledger_service.count_account_transactions(
        session, user_id=user.id, account_id=account_id
    )
    return ok(data)


@router.patch("/accounts/{account_id}")
async def update_account(
    account_id: int,
    payload: AccountPatch,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
) -> Dict[str, Any]:
    """更新账户（部分字段）。"""
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return fail("没有需要更新的字段", code=400)

    if "kind" in fields:
        kind = (fields["kind"] or "cash").strip()
        fields["kind"] = kind if kind in ACCOUNT_KINDS else "other"

    account = await ledger_service.update_account(
        session, account_id, user_id=user.id, **fields
    )
    if account is None:
        return fail("账户不存在", code=404)

    await log_action(
        session,
        "update_account",
        user_id=user.id,
        target_type="account",
        target_id=str(account_id),
        operator="user",
        detail=f"更新账户「{account.name}」：{'、'.join(fields.keys())}",
        channel="web",
    )

    balances = await ledger_service.get_account_balances(session, user_id=user.id)
    row = next((b for b in balances if b["id"] == account_id), None) or {}

    data = to_dict(account)
    data["balance"] = row.get("balance", account.initial_balance)
    data["kind_label"] = _KIND_LABELS.get(account.kind, "其他")
    return ok(data)


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: int,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
) -> Dict[str, Any]:
    """删除账户。

    拒绝的两种情形都返回 ``success: false`` + 可读原因，而不是 4xx：
    这是**业务规则**不满足，不是请求格式错误，前端要把原因原样弹给用户。
    """
    account = await ledger_service.get_account(session, account_id, user_id=user.id)
    if account is None:
        return fail("账户不存在", code=404)

    name = account.name
    success, reason = await ledger_service.delete_account(
        session, account_id, user_id=user.id
    )
    if not success:
        return fail(reason, code=400)

    await log_action(
        session,
        "delete_account",
        user_id=user.id,
        target_type="account",
        target_id=str(account_id),
        operator="user",
        detail=f"删除账户「{name}」",
        channel="web",
    )
    return ok({"id": account_id, "message": f"已删除账户「{name}」"})


@router.get("/accounts/{account_id}/transactions")
async def account_transactions(
    account_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
) -> Dict[str, Any]:
    """某账户下的流水（删除前的「先看看里面有什么」入口）。"""
    account = await ledger_service.get_account(session, account_id, user_id=user.id)
    if account is None:
        return fail("账户不存在", code=404)

    rows = await ledger_service.list_transactions(
        session, user_id=user.id, limit=limit
    )
    mine = [t for t in rows if t.account_id == account_id]

    return ok(
        [to_dict(t) for t in mine],
        total=len(mine),
        account=to_dict(account),
    )
