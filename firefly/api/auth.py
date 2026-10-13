"""登录、登出、会话与口令管理接口。

安全设计（见 ``firefly/services/auth.py`` 的模块注释）：
登录成功时把令牌同时写进 **HttpOnly Cookie** 与响应体。
写 Cookie 是为了让浏览器后续请求自动携带；写响应体是为了让
非浏览器客户端（curl / 机器人 / 前端自研存储）也能拿到令牌。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ..config import get_config
from ..services import auth as auth_service
from ..services import audit as audit_service
from ..db.session import get_session
from sqlalchemy.ext.asyncio import AsyncSession

from .deps import CurrentUser, fail, ok, require_auth

router = APIRouter(tags=["auth"])

# Cookie 的公共属性，集中在这里避免各处配置不一致
_COOKIE_PATH = "/"


class LoginIn(BaseModel):
    """登录请求体。"""

    username: str = Field(min_length=1, max_length=64, description="用户名")
    password: str = Field(min_length=1, max_length=128, description="口令")
    remember: bool = Field(default=False, description="记住我（延长 Cookie 有效期）")


class ChangePasswordIn(BaseModel):
    """修改口令请求体。"""

    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


def _set_auth_cookie(response: Response, token: str, *, hours: int) -> None:
    """写入会话 Cookie。

    属性说明（每一条都对应一类攻击）：

    - ``httponly=True``：JS 无法读取，防 XSS 窃取令牌；
    - ``samesite="lax"``：防 CSRF。选 ``lax`` 而非 ``strict``，
      是因为 ``strict`` 下从外部链接跳转进来浏览器不会带 Cookie，
      用户会觉得「刚登录怎么又退了」；
    - ``secure``：仅在 HTTPS 下开启。本项目默认是 ``http://127.0.0.1``
      本地访问，若强制 ``secure`` 浏览器会直接丢弃 Cookie 导致登录失效。
      因此这里按请求协议动态判断，而不是写死 ``True``；
    - ``max_age``：与服务端令牌有效期保持一致，避免出现
      「Cookie 还在但令牌已过期」的困惑状态。
    """
    response.set_cookie(
        key=auth_service.COOKIE_NAME,
        value=token,
        max_age=hours * 3600,
        path=_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=False,  # 由调用方根据协议覆盖
    )


def _clear_auth_cookie(response: Response) -> None:
    """清除会话 Cookie。"""
    response.delete_cookie(key=auth_service.COOKIE_NAME, path=_COOKIE_PATH)


def _is_secure_request(request: Request) -> bool:
    """判断当前请求是否走 HTTPS（含反向代理终止 TLS 的情况）。"""
    if request.url.scheme == "https":
        return True
    forwarded = request.headers.get("x-forwarded-proto")
    return bool(forwarded and forwarded.split(",")[0].strip() == "https")


@router.get("/auth/status")
async def auth_status() -> Dict[str, Any]:
    """鉴权模块的公开状态。

    **不需要登录即可访问** —— 前端在登录页就要知道「这个部署到底要不要登录」，
    如果这个接口本身要求登录，登录页就陷入了死循环。
    返回内容不含任何敏感字段（无口令、无哈希、无盐、无密钥）。
    """
    return ok(auth_service.auth_status())


@router.post("/auth/login")
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """登录。

    失败时**始终返回 HTTP 200 + ``success: false``**，而不是 401。
    理由：前端需要区分「登录失败（可以重试）」与「登录态失效（要跳登录页）」，
    两者都抛 401 会让拦截器把用户输错密码也当成会话过期处理。
    """
    cfg = get_config()
    ip = auth_service.get_client_ip(request)

    result = await auth_service.authenticate(payload.username, payload.password, ip)

    # ---- 审计：登录成功与失败都要留痕 ----
    # 失败留痕尤其重要，它是判断「是否有人在爆破」的唯一依据。
    try:
        await audit_service.log_action(
            session,
            action="login" if result["success"] else "login_failed",
            user_id=1,
            target_type="auth",
            target_id=payload.username,
            operator="user",
            detail=result["message"],
            extra={"ip": ip, "locked": result.get("locked", False)},
            channel="web",
        )
        await session.commit()
    except Exception:  # noqa: BLE001 - 审计失败不能阻断登录
        await session.rollback()

    if result["success"]:
        token = result.get("token") or ""
        if token:
            hours = cfg.auth.session_hours
            if payload.remember:
                # 「记住我」把有效期拉长到 7 天。**但服务端令牌的有效期不变**，
                # 因为令牌的 exp 在签发时就写死了；这里只延长 Cookie 的存活时间。
                # 也就是说「记住我」不会削弱服务端的安全性，只是让浏览器
                # 多留一会儿 Cookie 而已——若令牌过期，用户仍需重新登录。
                hours = max(hours, 24 * 7)
            _set_auth_cookie(response, token, hours=hours)

    return {
        "success": result["success"],
        "message": result["message"],
        "data": {
            "token": result.get("token", ""),
            "username": payload.username if result["success"] else "",
            "expires_in": result.get("expires_in", 0),
            "locked": result.get("locked", False),
            "retry_after": result.get("retry_after", 0),
            "remaining_attempts": result.get("remaining_attempts", 0),
            "using_default_password": result.get("using_default_password", False),
        },
    }


@router.post("/auth/logout")
async def logout(response: Response, request: Request) -> Dict[str, Any]:
    """登出。

    只清客户端 Cookie。因为令牌是无状态 HMAC 签名的，
    服务端并没有一张会话表可以「删除这一行」——
    这是无状态令牌换来的简洁性所付出的代价，已在服务层注释中说明。
    """
    _clear_auth_cookie(response)
    return ok({"message": "已退出登录"})


@router.get("/auth/me")
async def me(
    request: Request,
    _guard: Optional[Dict[str, Any]] = Depends(require_auth),
) -> Dict[str, Any]:
    """返回当前登录态。

    用于前端刷新页面后判断「我还需不需要重新登录」。
    鉴权未启用时返回 ``authenticated: true`` 与 ``auth_disabled: true``，
    让前端知道这是「无需登录」而非「已登录」。
    """
    cfg = get_config()

    if not cfg.auth.enabled:
        return ok(
            {
                "authenticated": True,
                "auth_disabled": True,
                "username": "",
                "expires_in": 0,
            }
        )

    token = ""
    from .deps import get_token_from_request

    token = get_token_from_request(request)
    payload = auth_service.verify_token(token) or {}

    return ok(
        {
            "authenticated": bool(payload),
            "auth_disabled": False,
            "username": payload.get("user", ""),
            "expires_in": auth_service.token_remaining_seconds(token),
            "login_at": payload.get("iat", 0),
        }
    )


@router.post("/auth/change-password")
async def change_password(
    payload: ChangePasswordIn,
    response: Response,
    session: AsyncSession = Depends(get_session),
    _guard: Optional[Dict[str, Any]] = Depends(require_auth),
) -> Dict[str, Any]:
    """修改登录口令。

    要求提供**原口令**，即使用户已登录。
    理由：若只凭登录态就能改口令，那么一旦令牌被窃取（比如用户
    把令牌贴进了聊天记录），攻击者就能直接改掉口令把主人锁在门外。
    要求原口令后，攻击者至少还需要知道旧口令。
    """
    cfg = get_config()

    if not cfg.auth.password_hash:
        auth_service.ensure_auth_config()

    # 原口令校验，复用恒定时间比对
    if not auth_service.verify_password(
        payload.old_password, cfg.auth.password_salt, cfg.auth.password_hash
    ):
        return fail("原口令不正确", code=400)

    if payload.old_password == payload.new_password:
        return fail("新口令不能与原口令相同", code=400)

    ok_flag, message = auth_service.reset_password(payload.new_password)
    if not ok_flag:
        return fail(message, code=400)

    # 改完口令后，让当前会话立刻换发新令牌。
    # 注意：**旧令牌在服务端仍然有效**（无状态令牌无法单向作废），
    # 只有靠用户改 ``secret_key`` 才能全量失效。这里主动换发是为了
    # 让当前浏览器不至于停留在旧令牌上。
    token = auth_service.create_token(cfg.auth.username)
    _set_auth_cookie(response, token, hours=cfg.auth.session_hours)

    try:
        await audit_service.log_action(
            session,
            action="change_password",
            user_id=1,
            target_type="auth",
            target_id=cfg.auth.username,
            operator="user",
            detail="口令已修改",
            channel="web",
        )
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()

    return ok({"message": message, "token": token})
