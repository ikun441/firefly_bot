"""流萤 · 财报总结优化 —— 应用入口。

一个进程同时承担四件事：
1. NoneBot2 加载 OneBot v11 协议端（QQ 机器人）
2. FastAPI 提供 Web API
3. 静态挂载 ``index/``，前端与后端**同端口**（无 CORS、演示只有一个地址）
4. APScheduler 跑定时报告推送 / 订阅入账 / 阈值预警

启动方式：
    python -m firefly.app
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# --- NoneBot 初始化必须在导入任何 nonebot 子模块之前完成 -----------------------
import nonebot
from nonebot.adapters.console import Adapter as ConsoleAdapter
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.log import default_filter, default_format, logger as nb_logger

# 把当前进程的工作目录固定为项目根，保证相对路径（data/、index/）始终正确
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from firefly.config import CONFIG_PATH, INDEX_DIR, ensure_dirs, get_config  # noqa: E402

config = get_config()
ensure_dirs()

logging.basicConfig(level=logging.DEBUG if config.app.debug else logging.INFO)
logger = logging.getLogger("firefly")

nonebot.init(
    # 让 NoneBot 不要自己再读一遍 .env：firefly 已通过 config.load_config() 加载过，
    # 而 NoneBot 对 SUPERUSERS 等字段要求 JSON 格式（["123"]），
    # 与我们 .env 里更友好的「逗号分隔 / 留空」写法冲突，
    # 会导致 SettingsError: error parsing env var "superusers"。
    # firefly 自己解析完再通过下面的参数显式传入。
    _env_file=None,
    driver="~fastapi",
    host=config.server.host,
    port=config.server.port,
    log_level="DEBUG" if config.app.debug else "INFO",
    onebot_access_token=config.onebot.access_token or "",
    superusers=set(config.onebot.superusers or []),
)

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# Console 适配器是个交互式 TUI，会把整个终端刷成 ANSI 转义码，
# 导致启动横幅和访问地址被冲掉（答辩演示时看不到地址）。
# 因此仅在有真实 TTY 且显式开启时才注册；默认走 OneBot 协议端。
if sys.stdout.isatty() and os.getenv("FIREFLY_ENABLE_CONSOLE_BOT", "").strip().lower() in {"1", "true", "yes"}:
    driver.register_adapter(ConsoleAdapter)


# ------------------------------------------------------------------------------
# 生命周期
# ------------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app):
    """建表 → 注册定时任务 → 打印访问地址。"""
    from firefly.db.session import dispose_db, init_db
    from firefly.scheduler.tasks import register_jobs

    await init_db()
    logger.info("数据库已就绪：%s", config.database.url)

    try:
        register_jobs()
    except Exception as exc:  # noqa: BLE001 - 定时任务挂了不该拖垮 Web 服务
        logger.warning("定时任务注册失败：%s", exc)

    _print_banner()

    yield

    await dispose_db()
    logger.info("流萤已停止。")


def _print_banner() -> None:
    """打印启动横幅与前端访问地址（需求：后端启动时显示前端访问地址）。"""
    scheme_host = f"http://{config.server.host}:{config.server.port}"
    if config.server.host in ("0.0.0.0", "::"):
        scheme_host = f"http://127.0.0.1:{config.server.port}"

    if not config.server.show_welcome:
        return

    # 鉴权状态行
    if config.auth.enabled:
        auth_line = f"  🔐 登录鉴权     已启用 · 账号 {config.auth.username}"
    else:
        auth_line = "  🔐 登录鉴权     未启用（任何人可访问）"

    width = 62
    lines = [
        "",
        "╭" + "─" * width + "╮",
        "│" + _center("流 萤 · Firefly", width) + "│",
        "│" + _center("财报总结优化 · 智能财务财税助手", width) + "│",
        "╰" + "─" * width + "╯",
        "",
        f"  🖥  前端控制台   {scheme_host}/",
        f"  📡 API 文档     {scheme_host}/docs",
        f"  🔌 OneBot 反向WS {scheme_host}{config.onebot.ws_path}",
        f"     └─ 协议端请connect到这个地址（WebSocket 客户端模式）",
        "",
        f"  🗄  数据库       {config.database.url}",
        f"  🤖 AI 引擎      {'已启用 · ' + config.llm.model if config.llm.is_ready else '未配置（自然语言记账降级为规则解析）'}",
        auth_line,
        f"  ⏰ 定时推送      {', '.join(config.push.schedule_times or []) or '未设置'}"
        f"{'（已启用）' if config.push.enabled else '（未启用）'}",
        f"  🎯 推送目标      {config.push.target_id or '未配置'}",
        "",
        "  按 Ctrl+C 停止服务",
        "",
    ]

    # 安全提示：只在**真的有问题**时打印，避免常态噪音淹没横幅。
    # 正常单机演示（未开鉴权 + 仅监听 127.0.0.1）不打扰用户。
    warnings: list[str] = []
    if config.server.host in ("0.0.0.0", "::") and not config.auth.enabled:
        warnings.append(
            "服务已监听所有网卡但未启用登录鉴权，局域网内任何人可读写你的账本"
        )
    if config.server.host in ("0.0.0.0", "::") and config.auth.enabled:
        warnings.append(
            "服务对外暴露且已启用鉴权；若仍在使用默认口令，请尽快在设置页修改"
        )
    # 正向 WS 地址本应为空（本项目走反向 WS）。填了就会让人误以为
    # 流萤要去连协议端，实际排障时极易被带偏，所以启动时就点明。
    if config.onebot.ws_url:
        warnings.append(
            f"ONEBOT_WS_URL 已设置为 {config.onebot.ws_url}，但本项目采用反向 WebSocket，"
            "该值应当留空；协议端应主动连接 "
            f"{scheme_host}{config.onebot.ws_path}"
        )

    print("\n".join(lines), flush=True)

    for warning in warnings:
        logger.warning("⚠ 配置提示：%s", warning)


def _center(text: str, width: int) -> str:
    """按显示宽度居中（中文算 2 个字符）。"""
    display_width = sum(2 if ord(char) > 0x2E80 else 1 for char in text)
    padding = max(0, width - display_width)
    left = padding // 2
    return " " * left + text + " " * (padding - left)


# ------------------------------------------------------------------------------
# FastAPI 装配
# ------------------------------------------------------------------------------

app = nonebot.get_asgi()

# FastAPI 的 lifespan 由 NoneBot 自己管理，这里把初始化挂到 NoneBot 启动事件上
from nonebot import get_driver  # noqa: E402

_nb_driver = get_driver()


@_nb_driver.on_startup
async def _on_startup() -> None:
    from firefly.db.session import init_db
    from firefly.scheduler.tasks import register_jobs

    await init_db()

    # 鉴权配置自愈：首次启动时把明文口令派生为哈希、生成签名密钥并写回配置。
    # 放在 init_db 之后、banner 之前，这样横幅能显示真实的鉴权状态。
    try:
        from firefly.services.auth import ensure_auth_config

        changed = ensure_auth_config()
        if changed:
            logger.info("鉴权配置已初始化：%s", ", ".join(sorted(changed)))
    except Exception as exc:  # noqa: BLE001 - 鉴权初始化失败不能阻断启动
        logger.warning("鉴权配置初始化失败：%s", exc)

    try:
        register_jobs()
    except Exception as exc:  # noqa: BLE001
        logger.warning("定时任务注册失败：%s", exc)
    _print_banner()


@_nb_driver.on_shutdown
async def _on_shutdown() -> None:
    from firefly.db.session import dispose_db

    await dispose_db()
    logger.info("流萤已停止。")


# 1) API 路由
from firefly.api.router import api_router  # noqa: E402

app.include_router(api_router, prefix="/api")


# 1.2) 登录鉴权中间件
# 为什么不逐个路由加 Depends(require_auth)：本项目 53 个接口，
# 手工加会漏（漏掉的那一个就是未授权入口），而且新增接口时极易忘记。
# 用中间件按路径前缀统一拦截，白名单集中在 firefly/api/router.py 的 PUBLIC_PATHS，
# **默认拒绝**而非默认放行——新接口自动受保护，漏加白名单只会「多要一次登录」，
# 不会「把数据裸奔出去」。这个失败方向是刻意选的。
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402


class AuthMiddleware(BaseHTTPMiddleware):
    """登录态拦截中间件。

    只拦截 ``/api`` 前缀且不在白名单中的请求。静态资源（``/``、
    ``/css/*``、``/js/*``）一律放行，否则登录页自己都加载不出来。
    实际的数据保护靠「接口全部要令牌」+「登录页本身不含任何数据」实现。
    """

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        from firefly.api.router import _should_protect
        from firefly.config import get_config as _get_config

        cfg = _get_config()

        # 鉴权未启用：直接放行，保持单机演示的零门槛体验
        if not cfg.auth.enabled or not _should_protect(request.url.path):
            return await call_next(request)

        from firefly.api.deps import get_token_from_request
        from firefly.services import auth as auth_service

        token = get_token_from_request(request)
        if not auth_service.verify_token(token):
            # 返回 401 而非 200+success:false：前端拦截器据此统一跳登录页。
            # 响应体仍是统一信封格式，便于前端复用同一套解析逻辑。
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "message": "未登录或登录已过期，请重新登录",
                    "code": 401,
                },
            )

        return await call_next(request)


app.add_middleware(AuthMiddleware)

# 1.5) 交互式 API 文档
# NoneBot 的 FastAPI driver 在构建 ASGI app 时把 docs_url / redoc_url / openapi_url
# 全部置为 None（上游是刻意关掉的），所以 /docs 默认并不存在。
# 这里显式补上，方便答辩时直接展示接口；同时因为下面会把 index/ 挂到 "/"，
# 必须**先注册文档路由再挂载静态目录**，否则 "/" 这个 Mount 会把 /docs 一并吞掉。
from fastapi.openapi.docs import get_swagger_ui_html  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

app.openapi_url = "/api/openapi.json"
app.docs_url = "/docs"
app.redoc_url = None


@app.get(app.openapi_url, include_in_schema=False)
async def _openapi_schema() -> JSONResponse:
    """提供 OpenAPI 描述，供 Swagger UI 与第三方工具消费。"""
    return JSONResponse(app.openapi())


@app.get(app.docs_url, include_in_schema=False)
async def _swagger_ui():  # type: ignore[no-untyped-def]
    """Swagger UI 页面。"""
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{config.app.name} · 接口文档",
    )


# 2) 前端静态资源：index/ 目录挂到根路径
from fastapi.staticfiles import StaticFiles  # noqa: E402


class NoCacheStaticFiles(StaticFiles):
    """给前端资源加「禁止强缓存」响应头。

    为什么要专门做这件事：``StaticFiles`` 默认只按 ``Last-Modified`` 协商，
    浏览器会**启发式缓存** ``app.js`` / ``*.css`` 这类资源。开发期改了前端
    （比如新增一个页面）后刷新，浏览器仍拿旧文件，表现是「代码明明写了、
    界面上就是没有」——排查成本极高，容易被误判成后端没注册路由。

    本项目不做前端构建，文件名不含内容哈希，无法靠改文件名失效，
    因此统一要求浏览器每次校验（``no-cache`` 是「可缓存但必须复验」，
    比 ``no-store`` 更合适：文件没变时仍能走 304，不会反复重传）。
    """

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        response = await super().get_response(path, scope)
        # API 与文档路由不走这里，仅前端静态资源受影响
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


if INDEX_DIR.exists():
    app.mount("/", NoCacheStaticFiles(directory=str(INDEX_DIR), html=True), name="index")
    logger.info("前端已挂载：%s -> %s", "/", INDEX_DIR)
else:  # pragma: no cover - 目录缺失只在部署异常时出现
    logger.warning("前端目录不存在：%s", INDEX_DIR)


# ------------------------------------------------------------------------------
# 直接运行
# ------------------------------------------------------------------------------


def main() -> None:
    """``python -m firefly.app`` 入口。"""
    import uvicorn

    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="debug" if config.app.debug else "info",
        access_log=config.app.debug,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.error("启动失败：%s", exc)
        raise
