"""公共依赖与响应工具。

这一层把「当前用户」抽象出来：毕设阶段默认单用户（个人版），
每次请求确保存在一个默认 User 记录，后续多用户版只需替换 `current_user` 的实现。
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..db.models import User
from ..db.session import get_session
from ..services.auth import COOKIE_NAME

DEFAULT_PLATFORM_ID = "local"

# 支持的统计周期。定义在此处作为**唯一来源**，供各路由复用。
#
# 之所以集中管理：这个集合原先在 report.py / stats.py 各写了一份，
# 而 taxcheck.py 想用时漏了 import，导致 POST /api/tax-check 直接 500。
# 常量散落多处时，「少写一个 import」的代价就是整条链路不可用。
VALID_PERIODS: frozenset = frozenset(
    {"daily", "weekly", "monthly", "quarterly", "yearly", "custom"}
)

# 单笔金额上限（元）。
#
# 为什么必须有上界：此前所有金额字段只写了 gt=0，于是
# POST /api/transactions 传 999999999999999 能直接入库。
# 危害不是「存了个大数」这么简单 ——
#   1. SQLite 存的是 REAL（IEEE754 双精度），超过 2^53 后整数不再精确，
#      实测回读值已从 999999999999999 漂移成 1000000000000087；
#   2. 这个值会被 /api/stats 的收支合计、报告 payload、预算进度一并吸收，
#      总览页直接显示天文数字，且用户很难定位到是哪一笔。
# 取 1 亿是基于业务判断：个人/小微记账场景下，单笔 1 亿元已远超合理范围，
# 同时仍在双精度可精确表示的区间内（远小于 2^53）。
MAX_AMOUNT: float = 100_000_000.0

__all__ = [
    "SessionDep",
    "CurrentUser",
    "DbSession",
    "current_user",
    "get_or_create_user",
    "ok",
    "fail",
    "to_dict",
    "parse_dt",
    "require_auth",
    "get_token_from_request",
]


# 让路由签名更短
SessionDep = AsyncSession


async def get_or_create_user(
    session: AsyncSession,
    platform_id: str = DEFAULT_PLATFORM_ID,
) -> User:
    """取默认用户，不存在则创建。

    单用户演示下 platform_id 固定为 ``local``；接入 OneBot 后可用 QQ 号作为
    platform_id 实现天然的多用户隔离。

    新用户创建时会一并落默认类别 —— 否则用户第一次打开前端是一片空白，
    记的第一笔账也没有类别可选，且导入的账单会全部落到「未分类」。
    """
    result = await session.execute(select(User).where(User.platform_id == platform_id))
    user = result.scalars().first()
    if user is not None:
        await _ensure_user_categories(session, user)
        return user

    user = User(
        nickname="本地用户",
        platform_id=platform_id,
        taxpayer_type=get_config().tax_check.taxpayer_type,
        is_business=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    await _ensure_user_categories(session, user)
    return user


async def _ensure_user_categories(session: AsyncSession, user: User) -> None:
    """确保用户已有默认类别（幂等）。

    延迟导入 ledger 以避免 ``deps`` 与 ``services`` 之间的循环依赖。
    """
    from ..services import ledger as ledger_service

    try:
        existing = await ledger_service.list_categories(session, user_id=user.id)
        if not existing:
            await ledger_service.ensure_default_categories(session, user_id=user.id)
        # 默认资金账户：没有它「账户余额」会恒为 0，流水也无处归属
        await ledger_service.ensure_default_account(session, user_id=user.id)
        await session.commit()
    except Exception:  # noqa: BLE001 - 建类别/账户失败不应阻断请求
        await session.rollback()


async def current_user(session: AsyncSession = Depends(get_session)) -> User:
    """FastAPI 依赖：返回当前用户。"""
    return await get_or_create_user(session)


def get_token_from_request(request: Request) -> str:
    """从请求中提取会话令牌。

    查找顺序（先 Cookie 后 Header）：

    1. **Cookie ``firefly_token``** —— 前端默认方式。因为它是 HttpOnly 的，
       JS 读不到，XSS 偷不走。
    2. **``Authorization: Bearer <token>``** —— 给 curl / Postman / 机器人用。

    先查 Cookie 是刻意的：浏览器场景下 Cookie 一定存在且更安全；
    只有在没有 Cookie（说明不是浏览器发起的请求）时才看 Header，
    避免浏览器里残留的旧 Header 覆盖掉刚登录的新 Cookie。
    """
    token = request.cookies.get(COOKIE_NAME) or ""
    if token:
        return token

    auth_header = request.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    return ""


async def require_auth(request: Request) -> Optional[Dict[str, Any]]:
    """FastAPI 依赖：校验登录态。

    - 鉴权**未启用**时直接返回 ``None`` 放行（单机演示的默认状态）；
    - 鉴权启用但令牌缺失/非法/过期时抛 ``401``。

    **为什么在依赖里抛 HTTPException 而不是返回 fail()**：
    401 是 HTTP 语义上「你没登录」的标准表达，前端拦截器可以据此
    统一跳转到登录页；而 ``success: false`` 的 200 响应无法与
    「业务逻辑失败」区分开，会让前端要么漏跳登录、要么把所有业务
    错误都当成登录失效。
    """
    cfg = get_config()

    if not cfg.auth.enabled:
        return None

    from ..services import auth as auth_service

    token = get_token_from_request(request)
    payload = auth_service.verify_token(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或登录已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


# 供路由直接使用的依赖别名
CurrentUser = Depends(current_user)
DbSession = Depends(get_session)
AuthGuard = Depends(require_auth)


def ok(data: Any = None, **extra: Any) -> Dict[str, Any]:
    """统一成功响应：``{"success": true, "data": ...}``。"""
    payload: Dict[str, Any] = {"success": True, "data": data}
    payload.update(extra)
    return payload


def fail(message: str, *, code: int = 400, detail: Any = None) -> Dict[str, Any]:
    """统一失败响应（业务失败仍返回 200，由前端判断 success 字段）。"""
    payload: Dict[str, Any] = {"success": False, "message": message, "code": code}
    if detail is not None:
        payload["detail"] = detail
    return payload


def to_dict(obj: Any, *, exclude: Optional[set] = None) -> Dict[str, Any]:
    """把 SQLModel 实例转为可 JSON 序列化的 dict。"""
    if obj is None:
        return {}
    exclude = exclude or set()
    data: Dict[str, Any] = {}
    for key, value in obj.__dict__.items():
        if key.startswith("_") or key in exclude:
            continue
        if hasattr(value, "isoformat"):
            from datetime import datetime, timezone
            if isinstance(value, datetime) and value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            data[key] = value.isoformat()
        else:
            data[key] = value
    return data


def parse_dt(value: Any):
    """宽松解析前端传入的时间字符串（ISO / ``YYYY-MM-DD HH:MM``）。"""
    if value is None or value == "":
        return None
    if hasattr(value, "year"):
        return value

    text = str(value).strip().replace("/", "-")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    from datetime import datetime

    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m", "%Y%m%d"):
        try:
            if fmt is None:
                return datetime.fromisoformat(text)
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"无法解析的时间格式: {value}",
    )
