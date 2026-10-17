"""元信息 / 健康检查 / 前端引导数据。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from .. import __version__
from ..db.session import get_session
from .deps import ok, to_dict

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health() -> Dict[str, Any]:
    """健康检查：前端顶栏的连接状态指示灯轮询此接口。"""
    cfg = get_config()
    db_ok = False
    error = None

    try:
        from ..db.session import session_scope
        from sqlmodel import text
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:  # noqa: BLE001 - 健康检查需要吞掉所有异常
        error = f"{type(exc).__name__}: {exc}"

    payload = {
        "status": "ok" if db_ok else "degraded",
        "app": cfg.app.name,
        "version": __version__,
        "database": db_ok,
        "llm_ready": cfg.llm.is_ready,
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    if error:
        payload["error"] = error
    return ok(payload)


@router.get("/bootstrap")
async def bootstrap() -> Dict[str, Any]:
    """登录前就能拿到的**最小**引导信息。

    为什么需要这个接口：前端必须在**发出任何业务请求之前**知道
    ``app.debug``（演示模式）与主题等纯展示配置，否则会出现
    「配置里开着演示模式，登录页却先去打真实接口」的错位。
    而 ``/api/meta`` 是**受保护**的（它带当前用户名与业务配置），
    登录前拿不到。

    因此这里只暴露「不含任何业务数据、也不含任何身份信息」的字段：
    应用名、版本、主题、演示模式。刻意**不**包含用户、税率口径、
    推送目标等 —— 那些仍在 ``/api/meta`` 里，登录后才可见。
    """
    cfg = get_config()
    return ok(
        {
            "app_name": cfg.app.name,
            "subtitle": cfg.app.subtitle,
            "version": __version__,
            "debug": cfg.app.debug,
            "timezone": cfg.app.timezone,
            "currency_symbol": cfg.ledger.currency_symbol,
            "ui": {
                "theme": cfg.ui.theme,
                "accent_color": cfg.ui.accent_color,
                "particle_enabled": cfg.ui.particle_enabled,
                "report_animation": cfg.ui.report_animation,
                "counter_duration": cfg.ui.counter_duration,
                "page_size": cfg.ui.page_size,
            },
        }
    )


@router.get("/meta")
async def meta(session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """前端启动时拉取的全局元信息。"""
    cfg = get_config()

    from .deps import get_or_create_user

    user = await get_or_create_user(session)

    return ok(
        {
            "app_name": cfg.app.name,
            "subtitle": cfg.app.subtitle,
            "version": __version__,
            "port": cfg.server.port,
            "timezone": cfg.app.timezone,
            "debug": cfg.app.debug,
            "currency": cfg.ledger.currency,
            "currency_symbol": cfg.ledger.currency_symbol,
            "default_period": cfg.ledger.default_period,
            "custom_period_days": cfg.ledger.custom_period_days,
            "quick_amounts": cfg.collector.quick_amounts,
            "large_amount_threshold": cfg.ledger.large_amount_threshold,
            "llm": {
                "enabled": cfg.llm.enabled,
                "ready": cfg.llm.is_ready,
                "model": cfg.llm.model,
                "base_url": cfg.llm.base_url,
            },
            "ui": {
                "theme": cfg.ui.theme,
                "accent_color": cfg.ui.accent_color,
                "particle_enabled": cfg.ui.particle_enabled,
                "report_animation": cfg.ui.report_animation,
                "counter_duration": cfg.ui.counter_duration,
                "page_size": cfg.ui.page_size,
            },
            "tax": {
                "taxpayer_type": cfg.tax_check.taxpayer_type,
                "disclaimer": cfg.tax_check.disclaimer,
            },
            "push": {
                "enabled": cfg.push.enabled,
                "schedule_times": cfg.push.schedule_times,
                "report_period": cfg.push.report_period,
            },
            "user": to_dict(user),
        }
    )
