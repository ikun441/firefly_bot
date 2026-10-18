"""配置读写接口 —— 前端「设置」页通过这里修改 config.yaml。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import get_config, get_editable_schema, update_config
from .deps import fail, ok

router = APIRouter(tags=["settings"])


class SettingsPatch(BaseModel):
    """前端提交的配置补丁：``{"server.port": 2525, "ui.particle_enabled": false}``。"""

    model_config = {"extra": "allow"}


@router.get("/settings")
async def read_settings() -> Dict[str, Any]:
    """读取当前配置（敏感字段已脱敏）。"""
    cfg = get_config()
    return ok(cfg.model_dump_safe())


@router.get("/settings/schema")
async def read_schema() -> Dict[str, Any]:
    """返回可编辑字段白名单与只读字段说明。"""
    return ok(get_editable_schema())


@router.put("/settings")
async def write_settings(payload: SettingsPatch) -> Dict[str, Any]:
    """把允许修改的字段写回 config.yaml。

    不在白名单内的字段（如 ``llm.api_key``）会被拒绝并返回 \`\`rejected\`\` 列表，
    密钥请在本地 config.yaml 或环境变量配置，不通过通用网页接口写入。
    """
    patch: Dict[str, Any] = dict(payload.model_dump(exclude_none=True))

    if not patch:
        return fail("没有需要更新的字段")

    result = update_config(patch)

    # 端口变更需要重启，单独提示
    restart_required = any(key == "server.port" or key == "server.host" for key in result.get("updated", []))

    return ok(
        {
            "updated": result.get("updated", []),
            "rejected": result.get("rejected", []),
            "restart_required": restart_required,
            "config": get_config().model_dump_safe(),
        }
    )
