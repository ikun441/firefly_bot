"""协议端（OneBot v11）发送能力。

把「怎么拿到 bot 实例」这件事集中在这里，其余模块只管调用 ``send_text`` /
``send_image``。这样 Web 端和定时任务都不需要知道 NoneBot 的内部结构，
也让「后端没接协议端」时能优雅降级而不是抛异常。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from ..config import get_config

_STATUS: Dict[str, Any] = {
    "connected": False,
    "self_id": None,
    "adapters": [],
    "last_error": None,
    "last_sent_at": None,
}


def get_bot_status() -> Dict[str, Any]:
    """返回协议端连接状态（前端「推送」面板展示）。"""
    try:
        from nonebot import get_bots

        bots = get_bots()
        _STATUS["connected"] = bool(bots)
        if bots:
            bot = next(iter(bots.values()))
            _STATUS["self_id"] = getattr(bot, "self_id", None)
            _STATUS["adapters"] = list(
                {type(getattr(bot, "adapter", None)).__name__ for bot in bots.values()}
            )
        else:
            _STATUS["self_id"] = None
            _STATUS["adapters"] = []
    except Exception as exc:  # noqa: BLE001 - NoneBot 未初始化属正常情况
        _STATUS["connected"] = False
        _STATUS["last_error"] = f"{type(exc).__name__}: {exc}"

    return dict(_STATUS)


def _pick_bot():
    """挑选一个可用的 Bot 实例。"""
    try:
        from nonebot import get_bots

        bots = get_bots()
        if not bots:
            return None
        return next(iter(bots.values()))
    except Exception:  # noqa: BLE001
        return None


def _to_data_uri(path: Path) -> str:
    """把本地图片转成 ``base64://`` 数据 URI。

    为什么不用 ``file://``：
        NapCat 跑在 macOS 的 QQ.app 沙箱内，读不到 ``~/Desktop`` 下的文件。
        用本地路径时协议端会返回

            ActionFailed(status='failed', retcode=1200,
                message="EPERM: operation not permitted, open '.../report_79_push.png'")

        而这个失败**只发生在协议端一侧**：接口仍返回 200、服务端日志也没有异常。
        最终表现是「文字发出来了、图片没有，且没有任何报错」。

        ``base64://`` 由协议端自行解码，不经过文件系统，因此不受沙箱限制。
        代价是体积增大约 1/3 —— 报告图已先经 ``shrink_for_qq`` 压缩，
        实测 368 KB 的图编码后约 491 KB，完全可接受。

    失败时回退到 ``file://``：本地测试环境（无沙箱）下这个方式仍然可用，
    总比因为一次读取失败把整条消息丢掉强。
    """
    import base64

    try:
        raw = path.read_bytes()
        return "base64://" + base64.b64encode(raw).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        logger.warning("图片转 base64 失败，回退 file:// —— {}: {}", type(exc).__name__, exc)
        return path.resolve().as_uri()


def resolve_target(
    *,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
) -> "tuple[str, str]":
    """决定这条消息该发到哪里。

    优先级：
        1. 调用方显式传入的 target_id
        2. ``config.yaml`` 的 ``push.target_id``
        3. ``.env`` 的 ``SUPERUSERS`` 中的第一个（**兜底**）

    为什么要有第 3 条：
        用户配置了超级用户，直觉上「推送」就应该发到自己那里。
        如果只认 push.target_id，那么没填它的用户点推送时
        会静默地什么都不发 —— 而界面还提示成功，最难排查。
        把 SUPERUSERS 作为兜底，既符合直觉，也保证了
        「只要协议端连上了、且配了超级用户，推送就一定能到人」。

    :return: ``(target_type, target_id)``，target_id 为空表示无法投递
    """
    config = get_config()

    kind = (target_type or config.push.target_type or "private").strip()
    resolved = str(target_id or config.push.target_id or "").strip()

    if not resolved:
        superusers = [str(u).strip() for u in (config.onebot.superusers or [])]
        superusers = [u for u in superusers if u]
        if superusers:
            # 超级用户一定是私聊对象，群号不可能出现在这里
            kind = "private"
            resolved = superusers[0]

    # 群消息必须能转成整数；私聊同理。合法性在发送处再校验一次。
    return kind, resolved


async def send_text(
    text: str,
    *,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
) -> bool:
    """发送纯文本消息。返回是否真的发出去了。"""
    if not text:
        return False

    bot = _pick_bot()
    if bot is None:
        _STATUS["last_error"] = "没有可用的协议端连接（bot 未连接）"
        return False

    kind, target = resolve_target(target_type=target_type, target_id=target_id)
    if not target:
        _STATUS["last_error"] = (
            "未配置推送目标：请在 config.yaml 填 push.target_id，"
            "或在 .env 配置 SUPERUSERS 作为兜底"
        )
        return False

    try:
        from nonebot.adapters.onebot.v11 import Message

        from nonebot.adapters.onebot.v11 import MessageSegment
        message = Message(MessageSegment.text(text))
        if kind == "group":
            await bot.send_group_msg(group_id=int(target), message=message)
        else:
            await bot.send_private_msg(user_id=int(target), message=message)

        from datetime import datetime

        _STATUS["last_sent_at"] = datetime.now().isoformat(timespec="seconds")
        _STATUS["last_error"] = None
        return True
    except Exception as exc:  # noqa: BLE001 - 发送失败不应让调用方崩溃
        _STATUS["last_error"] = f"{type(exc).__name__}: {exc}"
        return False


async def send_image(
    image_path: Path | str,
    *,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
) -> bool:
    """发送图片消息。"""
    bot = _pick_bot()
    if bot is None:
        _STATUS["last_error"] = "没有可用的协议端连接（bot 未连接）"
        return False

    path = Path(image_path)
    if not path.exists():
        _STATUS["last_error"] = f"图片文件不存在: {path}"
        return False

    kind, target = resolve_target(target_type=target_type, target_id=target_id)
    if not target:
        _STATUS["last_error"] = (
            "未配置推送目标：请在 config.yaml 填 push.target_id，"
            "或在 .env 配置 SUPERUSERS 作为兜底"
        )
        return False

    try:
        from nonebot.adapters.onebot.v11 import MessageSegment

        # base64:// 而非 file:// —— QQ.app 沙箱读不到 ~/Desktop，详见 _to_data_uri
        message = MessageSegment.image(_to_data_uri(path))
        if kind == "group":
            await bot.send_group_msg(group_id=int(target), message=message)
        else:
            await bot.send_private_msg(user_id=int(target), message=message)

        from datetime import datetime

        _STATUS["last_sent_at"] = datetime.now().isoformat(timespec="seconds")
        _STATUS["last_error"] = None
        return True
    except Exception as exc:  # noqa: BLE001
        # 必须打日志：这个错误随后会被 send_text 成功时清空 _STATUS["last_error"]，
        # 只在状态里留痕的话，会出现「图片没发出去却查不到原因」。
        logger.warning("图片发送失败：{}: {}", type(exc).__name__, exc)
        _STATUS["last_error"] = f"{type(exc).__name__}: {exc}"
        return False


async def broadcast_summary(text: str, image_path: Optional[Path | str] = None, *, target_type: Optional[str] = None, target_id: Optional[str] = None) -> Dict[str, Any]:
    """发送一条报告消息（文字摘要 + 可选图片）。

    做法是分两条发出：先图后文。OneBot 的图文混排在不同协议端表现不一致，
    分成两条最稳。
    """
    config = get_config()

    # 目标解析交给 resolve_target，未配置 push.target_id 时会兜底到 SUPERUSERS，
    # 避免「用户配了超级用户、界面提示已推送、实际却什么都没发」这种静默失败。
    target_type, target_id = resolve_target(target_type=target_type, target_id=target_id)

    result = {
        "text_sent": False,
        "image_sent": False,
        "target_type": target_type,
        "target_id": target_id,
    }

    if not target_id:
        _STATUS["last_error"] = (
            "未配置推送目标：请在 config.yaml 填 push.target_id，"
            "或在 .env 配置 SUPERUSERS 作为兜底"
        )
        return result

    if image_path and config.push.send_image:
        result["image_sent"] = await send_image(image_path, target_type=target_type, target_id=target_id)

    if text and config.push.send_summary_text:
        result["text_sent"] = await send_text(text, target_type=target_type, target_id=target_id)

    return result


__all__ = [
    "send_text",
    "send_image",
    "broadcast_summary",
    "get_bot_status",
    "resolve_target",
]
