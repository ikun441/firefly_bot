"""Markdown AI replies → self-contained HTML → paginated PNG → original QQ session.

Raw HTML, remote images, scripts and network resources are disabled. Files live only
inside a per-reply temporary directory; protocol messages use base64, not file URLs.
"""
from __future__ import annotations

import asyncio
import base64
from html import escape
from pathlib import Path
import tempfile
from typing import Awaitable, Callable

from loguru import logger

from .. import __version__
from ..config import INDEX_DIR, get_config
from .image import render_html_to_image

MAX_CHARS = 20_000
MAX_LINES = 400
MAX_PAGES = 12


def build_ai_reply_html(markdown: str, *, title: str = "流萤 · AI 回复") -> str:
    from markdown_it import MarkdownIt

    text = str(markdown or "")
    lines = text[:MAX_CHARS].splitlines()
    truncated = len(text) > MAX_CHARS or len(lines) > MAX_LINES
    text = "\n".join(lines[:MAX_LINES])
    parser = MarkdownIt("commonmark", {"html": False, "breaks": True, "linkify": False}).enable("table").enable("strikethrough")
    parser.renderer.rules["image"] = lambda tokens, idx, options, env: '<span class="image-placeholder">[图片：' + escape(tokens[idx].content or "外部图片") + ']</span>'
    parser.renderer.rules["link_open"] = lambda *args: '<span class="md-link">'
    parser.renderer.rules["link_close"] = lambda *args: '</span>'
    body = parser.render(text)
    logo = INDEX_DIR / "assets/brand/firefly-logo.jpg"
    logo_html = ('<img class="logo" alt="流萤 Logo" src="data:image/jpeg;base64,' + base64.b64encode(logo.read_bytes()).decode("ascii") + '" />') if logo.is_file() else ""
    notice = '<p class="notice">内容较长，本图展示前 400 行 / 20,000 字符。可继续提问查看后续内容。</p>' if truncated else ""
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'" />
<meta name="viewport" content="width=device-width, initial-scale=1" /><title>{escape(title)}</title>
<style>
*{{box-sizing:border-box}} html,body{{margin:0;background:#f1f5f9;color:#172536;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
.page{{padding:26px}} .sheet{{background:#fff;border:1px solid #dbe5ed;border-radius:20px;padding:30px 34px}}
header{{display:flex;align-items:center;gap:14px;padding-bottom:20px;margin-bottom:24px;border-bottom:1px solid #e2e8f0}} .logo{{width:52px;height:52px;border-radius:14px;object-fit:cover}} header h1{{font-size:22px;line-height:1.4;margin:0}} .subtitle,footer{{font-size:12px;color:#617388}} .subtitle{{margin-top:5px}}
.markdown{{font-size:17px;line-height:1.85;overflow-wrap:anywhere}} .markdown>:first-child{{margin-top:0}}
h1,h2,h3,h4,h5,h6{{color:#123a45;line-height:1.45;margin:25px 0 12px;font-weight:700}} h1{{font-size:28px}} h2{{font-size:23px;padding-bottom:8px;border-bottom:1px solid #e2e8f0}} h3{{font-size:20px}} h4,h5,h6{{font-size:18px}}
p{{margin:10px 0 16px}} strong{{color:#0c6570}} ul,ol{{padding-left:27px;margin:12px 0}} li{{margin:7px 0}} li p{{margin:5px 0}}
blockquote{{margin:18px 0;padding:10px 18px;border-left:4px solid #35aaad;background:#edf8f7;color:#31595f;border-radius:0 10px 10px 0}}
code{{font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace;background:#edf2f6;padding:2px 5px;border-radius:4px;font-size:.9em}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;background:#142b3b;color:#edf6ff;padding:18px;border-radius:12px;font-size:14px;line-height:1.7;margin:18px 0}}
pre code{{color:inherit;background:none;padding:0;border:0;font-size:inherit}} table{{width:100%;table-layout:fixed;border-collapse:collapse;margin:20px 0;font-size:15px}}
th,td{{padding:11px 12px;border:1px solid #d9e3eb;text-align:left;vertical-align:top;overflow-wrap:anywhere}} th{{background:#e9f5f3;color:#164b51}} tr:nth-child(even) td{{background:#f7fafc}}
hr{{height:1px;border:0;background:#dce6ed;margin:24px 0}} .md-link{{color:#117e8a;text-decoration:underline}} .image-placeholder{{color:#64748b;font-size:14px}} .notice{{border:1px solid #e8ce91;background:#fff9e8;padding:12px;border-radius:8px;font-size:14px}}
footer{{margin-top:24px;padding-top:15px;border-top:1px solid #e2e8f0}}
</style></head><body><div class="page"><article class="sheet"><header>{logo_html}<div><h1>{escape(title)}</h1><div class="subtitle">Markdown 排版 · 标题 / 表格 / 引用 / 代码</div></div></header>
<main class="markdown">{body}</main>{notice}<footer>流萤 Firefly · v{escape(__version__)}</footer></article></div></body></html>'''


def _split_png(path: Path, max_height: int) -> list[bytes]:
    import io
    from PIL import Image

    pages = []
    with Image.open(path) as source:
        if source.width * source.height > 50_000_000:
            raise ValueError("AI reply image exceeds pixel limit")
        image = source.convert("RGB")
        top = 0
        while top < image.height:
            if len(pages) >= MAX_PAGES:
                raise ValueError("AI reply exceeds page limit")
            end = min(top + max_height, image.height)
            if end < image.height:
                # Prefer whitespace/code-background rows, avoiding cuts through a text line.
                for row in range(end - 1, max(top + max_height // 2, end - 180), -1):
                    samples = [image.getpixel((x, row)) for x in range(50, image.width - 50, 12)]
                    if samples and max(samples.count(color) for color in set(samples)) / len(samples) >= .98:
                        end = row + 1
                        break
            output = io.BytesIO()
            image.crop((0, top, image.width, end)).save(output, format="PNG", optimize=True)
            pages.append(output.getvalue())
            top = end
    return pages


async def render_ai_reply(markdown: str) -> list[bytes]:
    document = build_ai_reply_html(markdown)
    limit = min(6000, max(1000, get_config().push.image_max_height or 6000))
    # No financial reply files are retained after rendering (including error paths).
    with tempfile.TemporaryDirectory(prefix="firefly-ai-reply-") as directory:
        path = Path(directory) / "reply.png"
        rendered = await render_html_to_image(document, output_path=path, width=880, scale=1.5)
        return await asyncio.to_thread(_split_png, Path(rendered), limit)


def _text_chunks(text: str, limit: int = 3500):
    while text:
        cut = min(len(text), limit)
        if cut < len(text):
            newline = text.rfind("\n", cut // 2, cut)
            if newline >= 0:
                cut = newline + 1
        yield text[:cut]
        text = text[cut:]


async def send_ai_reply(send: Callable[..., Awaitable], markdown: str) -> dict:
    """Use the matcher's sender so the response returns to the exact original session."""
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    result = {"images_sent": 0, "text_fallback": False}
    try:
        pages = await render_ai_reply(markdown)
        if not pages:
            raise ValueError("No AI reply pages rendered")
        for page in pages:
            await send(Message(MessageSegment.image("base64://" + base64.b64encode(page).decode("ascii"))))
            result["images_sent"] += 1
        return result
    except Exception as exc:
        # Never log generated financial content or protocol response bodies.
        logger.warning("AI 回复图片渲染/发送失败，降级文字：{}", type(exc).__name__)
    result["text_fallback"] = True
    prefix = "图片渲染或发送未完成，以下为完整文字内容：\n\n"
    for chunk in _text_chunks(prefix + markdown):
        await send(Message(MessageSegment.text(chunk)))
    return result
