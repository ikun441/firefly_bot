"""API 路由聚合。

所有子路由都以 ``/api`` 为前缀挂载，前端只需一个 base 路径。
把聚合集中在这里的好处：新增模块只改这一个文件，且能一眼看出接口全貌。
"""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    account,
    ai,
    audit,
    auth,
    backup,
    budget,
    category,
    importer,
    meta,
    push,
    report,
    settings,
    stats,
    subscription,
    taxcheck,
    transaction,
)

api_router = APIRouter()

for module in (
    # 登录鉴权放在最前面：它是唯一无需登录即可访问的业务模块
    auth,
    meta,
    transaction,
    category,
    # 资金账户（账本）：现金 / 银行卡 / 微信 / 支付宝等资金容器的增删改查
    account,
    stats,
    report,
    taxcheck,
    budget,
    ai,
    audit,
    push,
    subscription,
    settings,
    # 账单批量导入（P2 · 2-1）
    importer,
    # 数据备份与还原：导出全库 JSON、上传还原、还原前自动安全备份
    backup,
):
    api_router.include_router(module.router)

# ------------------------------------------------------------------------------
# 鉴权保护范围
# ------------------------------------------------------------------------------
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/api/health",
        "/api/bootstrap",
        "/api/auth/status",
        "/api/auth/login",
        # 前端登录页自身也要能加载（静态资源），但它不在 /api 下，
        # 由下面 _should_protect 的 /api 前缀判断天然放行
        "/api/openapi.json",
    }
)
"""**无需登录**即可访问的接口白名单。

这个清单要尽量短。每多一项就是一个未授权入口，答辩时应能解释
「为什么这一项必须公开」：

- ``/api/health`` —— 容器探活与前端连通性检测，登录前就要能调；
- ``/api/bootstrap`` —— **纯展示配置**（应用名、主题、演示模式开关）。
  前端必须在登录**之前**就知道 ``app.debug``，否则无法决定
  「这次启动该不该请求真实数据」。该接口刻意不含用户名、
  税率口径、推送目标等业务信息；
- ``/api/auth/status`` —— 登录页需要知道「本部署要不要登录」；
- ``/api/auth/login`` —— 登录本身；
- ``/api/openapi.json`` —— 文档描述，不含业务数据。

**故意不放进来的**：``/api/meta``（含当前用户名与业务配置）、
``/api/settings``（含配置全貌）、``/api/audit/*``（含操作痕迹）。
"""


def _should_protect(path: str) -> bool:
    """判断某路径是否受登录保护。"""
    if not path.startswith("/api"):
        return False
    if path in PUBLIC_PATHS:
        return False
    # 文档页面本身（/docs）不在 /api 下，但 Swagger 会去拉 openapi.json，
    # 已在白名单里放行；文档页不涉及业务数据，公开无风险。
    return True


__all__ = ["api_router", "PUBLIC_PATHS", "_should_protect"]
