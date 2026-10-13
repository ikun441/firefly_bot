"""报告推送与协议端状态接口。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..db.session import get_session
from ..services import report as report_service
from .deps import get_or_create_user, ok

router = APIRouter(tags=["push"])


class PushIn(BaseModel):
    report_id: str = ""
    text: str = ""
    target_type: str = ""
    target_id: str = ""


@router.get("/push/status")
async def push_status() -> Dict[str, Any]:
    """推送配置与协议端连接状态。"""
    cfg = get_config()

    from ..render.bot import get_bot_status, resolve_target

    # 同时给出「配置值」与「实际生效目标」：
    # 配置为空但 SUPERUSERS 有值时，实际仍能投递，
    # 只展示配置值会让用户以为推送不可用。
    resolved_type, resolved_id = resolve_target()

    return ok(
        {
            "enabled": cfg.push.enabled,
            "target_type": cfg.push.target_type,
            "target_id": cfg.push.target_id,
            "resolved_target_type": resolved_type,
            "resolved_target_id": resolved_id,
            "target_source": (
                "config" if cfg.push.target_id
                else ("superusers" if resolved_id else "none")
            ),
            "schedule_times": cfg.push.schedule_times,
            "send_image": cfg.push.send_image,
            "send_summary_text": cfg.push.send_summary_text,
            "report_period": cfg.push.report_period,
            "bot": get_bot_status(),
        }
    )


@router.post("/push/test")
async def push_test(payload: PushIn = PushIn()) -> Dict[str, Any]:
    """立即推送一条测试消息，验证协议端链路是否打通。"""
    from ..render.bot import get_bot_status, resolve_target, send_text

    # 统一走 resolve_target：它会按 push.target_id → SUPERUSERS 的顺序解析目标。
    # 之前这里自己写了一遍「只认 push.target_id」的判断，
    # 结果配了超级用户的用户点测试推送时永远收到「未配置推送目标」。
    target_type, target_id = resolve_target(
        target_type=payload.target_type or None,
        target_id=payload.target_id or None,
    )

    if not target_id:
        return ok(
            {
                "sent": False,
                "message": (
                    "未配置推送目标：请在 config.yaml 填 push.target_id，"
                    "或在 .env 配置 SUPERUSERS 作为兜底"
                ),
            }
        )

    text = payload.text or "✨ 流萤推送链路测试：如果你看到这条消息，说明协议端已连通。"
    sent = await send_text(text, target_type=target_type, target_id=target_id)

    if sent:
        message = "已发送"
    else:
        message = get_bot_status().get("last_error") or "发送失败，请检查协议端连接"

    return ok(
        {
            "sent": sent,
            "target_type": target_type,
            "target_id": target_id,
            "message": message,
        }
    )


@router.post("/push/latest")
async def push_latest(
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """把最近一份报告渲染成图片并推送（前端「立即推送」按钮）。"""
    user = await get_or_create_user(session)
    report = await report_service.get_latest_report(session, user_id=user.id)
    if report is None:
        return ok({"sent": False, "message": "尚无可用报告，请先生成"})

    from ..render.push import push_report

    result = await push_report(report=report, session=session)
    return ok(result)
