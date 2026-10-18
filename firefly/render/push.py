"""报告推送编排：HTML → 图片 → 协议端。

上游（Web 按钮 / 定时任务）只需要调 ``push_report``，
不需要关心渲染失败要不要退回纯文字这类细节。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import DATA_DIR, get_config
from . import bot as bot_module
from .html import render_report_html
from .image import render_html_to_image, shrink_for_qq


def _now_utc() -> datetime:
    """当前时间（带 UTC 时区）。

    SQLModel 的 ``DateTime`` 拒绝 naive datetime，写入时会抛
    ``ValueError: Datetime values must have timezone information.``。
    集中在这个助手函数里，避免各处又写回 ``datetime.now()``。
    """
    return datetime.now(timezone.utc)


async def push_report(
    report: Any,
    *,
    session: Any = None,
    content_type: Optional[str] = None,
    ai_summary: Optional[bool] = None,
    ai_temperature: Optional[float] = None,
    ai_max_tokens: Optional[int] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
) -> Dict[str, Any]:
    """把一份报告渲染并推送出去。

    流程：payload → HTML 文件 → PNG 图片 → 协议端。

    任何一步失败都不会中断后面的步骤：图片发不出去时，
    文字摘要仍然要尽力送达 —— 宁可收到半份信息，也不要静默失败。

    :param content_type: ``text`` / ``image`` / ``both``。
        为 ``None`` 时取 ``config.push.content_type``。
        让用户在点按钮那一刻就能选择形态，而不是改完配置再回来。
    :param ai_summary: 是否用大模型生成一段解读附在消息里。
        为 ``None`` 时取 ``config.push.ai_summary``。
        生成的文本是**追加**在规则摘要之后的，不是替代 ——
        规则摘要保证数字准确，AI 负责解释「为什么」。
    :param ai_temperature: AI 总结的采样温度，``None`` 时取配置值。
    :param ai_max_tokens: AI 总结的字数上限，``None`` 时取配置值。
    :param target_type: 覆盖推送目标类型（private / group）
    :param target_id: 覆盖推送目标 ID
    """
    config = get_config()

    kind = (content_type or config.push.content_type or "both").strip().lower()
    if kind not in ("text", "image", "both"):
        kind = "both"

    want_image = kind in ("image", "both")
    want_text = kind in ("text", "both")

    use_ai = config.push.ai_summary if ai_summary is None else bool(ai_summary)

    result: Dict[str, Any] = {
        "report_id": getattr(report, "id", None),
        "title": getattr(report, "title", None),
        "enabled": config.push.enabled,
        "content_type": kind,
        "ai_summary": use_ai,
        "image_path": None,
        "text_sent": False,
        "image_sent": False,
        "errors": [],
        "pushed_at": None,
    }

    # 用 resolve_target 判定，它会兜底到 .env 的 SUPERUSERS。
    # 直接读 config.push.target_id 会漏掉「用户只配了超级用户」这种情况，
    # 导致明明能发却提前返回。
    resolved_type, resolved_target = bot_module.resolve_target(
        target_type=target_type, target_id=target_id
    )
    if not resolved_target:
        result["errors"].append(
            "未配置推送目标：请在 config.yaml 填 push.target_id，"
            "或在 .env 配置 SUPERUSERS 作为兜底"
        )
        return result

    result["target_type"] = resolved_type
    result["target_id"] = resolved_target

    # Generate AI text before rendering so text/image/both carry the same interpretation.
    summary_text = (getattr(report, "summary_text", "") or "") if config.push.send_summary_text else ""
    ai_text = ""
    if use_ai:
        try:
            from ..ai.summarizer import summarize_report
            ai_result = await summarize_report(getattr(report, "payload", None) or {},
                temperature=config.push.ai_temperature if ai_temperature is None else ai_temperature,
                max_tokens=config.push.ai_max_tokens if ai_max_tokens is None else ai_max_tokens)
            result["ai_engine"] = ai_result.get("engine")
            result["ai_elapsed_ms"] = ai_result.get("elapsed_ms")
            if ai_result.get("error"):
                result["errors"].append("AI 总结未生成：" + ai_result["error"])
            if ai_result.get("engine") == "llm":
                ai_text = (ai_result.get("text") or "").strip()
        except Exception as exc:
            from ..ai.llm import safe_error
            result["errors"].append("AI 总结未生成：" + safe_error(exc))
    result["ai_generated"] = bool(ai_text)
    if ai_text:
        summary_text = (summary_text + "\n\n🤖 AI 解读：\n" + ai_text).strip()

    html_path, image_path = None, None
    if want_image:
        try:
            from uuid import uuid4
            html_path = await render_report_html(report, ai_summary=ai_text, artifact_id=uuid4().hex)
            result["html_path"] = str(html_path)
            rendered = await render_html_to_image(html_path.read_text(encoding="utf-8"),
                output_path=html_path.with_suffix(".png"), width=config.push.image_width, scale=config.push.image_scale)
            image_path = shrink_for_qq(rendered)
            result["image_path"] = str(image_path)
            if session is not None:
                report.html_path, report.image_path = str(html_path), str(rendered)
                session.add(report)
                await session.commit()
        except Exception as exc:
            result["errors"].append("报告图片渲染失败（" + type(exc).__name__ + "）")
            # Preserve the generated interpretation when an image-only delivery cannot render.
            if summary_text:
                want_text = True
                result["text_fallback"] = True

    # 4) 推送
    push_text = summary_text if want_text else ""
    push_image = image_path if want_image else None

    try:
        send_result = await bot_module.broadcast_summary(push_text, push_image, target_type=resolved_type, target_id=resolved_target, text_enabled=want_text)
        result["text_sent"] = send_result.get("text_sent", False)
        result["image_sent"] = send_result.get("image_sent", False)

        # send_image / send_text 内部把失败原因写在 _STATUS 里，
        # broadcast 只回传布尔值，所以这里主动捞一次，
        # 否则「图片没发出去」会是个没有原因的悬案。
        if (want_image and not result["image_sent"]) or (want_text and not result["text_sent"]):
            from .bot import get_bot_status

            detail = get_bot_status().get("last_error")
            if detail:
                result["errors"].append(f"协议端返回：{detail}")
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"协议端发送失败: {type(exc).__name__}: {exc}")

    # 5) 回写推送状态
    if session is not None and (result["text_sent"] or result["image_sent"]):
        report.is_pushed = True
        # 必须用带时区的 datetime：SQLModel 的 DateTime 在写入 naive 值时会抛
        # ValueError: Datetime values must have timezone information.
        # 曾因为这里写 datetime.now() 导致「消息已发出、接口却返回 500」——
        # 比静默失败更难排查，因为用户会以为没发出去而重复点击。
        report.pushed_at = _now_utc()
        session.add(report)
        await session.commit()
        result["pushed_at"] = report.pushed_at.isoformat()

    result["ai_delivered"] = bool(ai_text and (result["text_sent"] or result["image_sent"]))
    result["success"] = bool(result["text_sent"] or result["image_sent"])
    if not result["success"] and not result["errors"]:
        result["errors"].append("协议端未连接，消息未发出（图片与 HTML 已生成到 data/reports/）")

    return result


async def push_text_only(text: str) -> Dict[str, Any]:
    """仅推送文字（阈值预警这类轻量消息）。"""
    sent = await bot_module.send_text(text)
    return {"success": sent, "text_sent": sent}


__all__ = ["push_report", "push_text_only"]
