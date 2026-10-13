"""流萤 Firefly · API 包。

路由的**唯一装配入口**是 ``firefly.api.router.api_router``，
``firefly/app.py`` 直接 include 它。

这里保留 ``build_router()`` 只是为了兼容可能存在的旧调用方 ——
它直接复用同一个 ``api_router``，不再自己拼一套。

（曾经踩过的坑：这里和 ``router.py`` 各维护了一份 include 列表，
新模块只加到了一边，导致接口注册成功却 404。）
"""

from __future__ import annotations

from fastapi import APIRouter

from .router import api_router


def build_router() -> APIRouter:
    """返回全局唯一的 API 路由器。"""
    return api_router


__all__ = ["build_router", "api_router"]
