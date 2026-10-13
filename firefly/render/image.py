"""HTML → 图片渲染。

优先走 ``nonebot-plugin-htmlrender``（答辩时协议端推送用的就是它）；
若插件不可用（例如还没初始化 NoneBot），直接退回 playwright 原生 API。
两条路径都指向同一份 HTML，所以出图结果一致。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from ..config import DATA_DIR, get_config

RENDER_TIMEOUT_MS = 30000


async def render_html_to_image(
    html: str,
    *,
    output_path: Optional[Path] = None,
    width: Optional[int] = None,
    scale: Optional[float] = None,
    full_page: bool = True,
) -> Path:
    """把 HTML 字符串渲染成 PNG 图片。

    Args:
        html: 完整 HTML 文档
        output_path: 输出路径，默认写到 ``data/reports/``
        width: 视口宽度，默认取 config.push.image_width
        scale: 设备像素比，默认取 config.push.image_scale（2 倍保证清晰度）
        full_page: 是否截取整页（报告通常较长，默认 True）
    """
    config = get_config()
    viewport_width = width or config.push.image_width or 800
    device_scale = scale or config.push.image_scale or 2

    if output_path is None:
        output_dir = DATA_DIR / "reports"
        output_dir.mkdir(parents=True, exist_ok=True)
        import time

        output_path = output_dir / f"report_{int(time.time() * 1000)}.png"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 优先尝试 htmlrender 插件（NoneBot 已初始化时可用）
    try:
        from nonebot_plugin_htmlrender import html_to_pic

        image_bytes = await html_to_pic(
            html,
            viewport={"width": viewport_width, "height": 800},
            device_scale_factor=device_scale,
            full_page=full_page,
            timeout=RENDER_TIMEOUT_MS,
        )
        output_path.write_bytes(image_bytes)
        return output_path
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - 插件渲染失败时退回原生 playwright
        pass

    # 退回原生 playwright
    return await _render_with_playwright(
        html,
        output_path=output_path,
        width=viewport_width,
        scale=device_scale,
        full_page=full_page,
    )


async def _render_with_playwright(
    html: str,
    *,
    output_path: Path,
    width: int,
    scale: float,
    full_page: bool,
) -> Path:
    """用 playwright 直接截图。"""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "图片渲染依赖缺失。请执行：pip install playwright && python -m playwright install chromium"
        ) from exc

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            context = await browser.new_context(
                viewport={"width": width, "height": 800},
                device_scale_factor=scale,
            )
            page = await context.new_page()
            await page.set_content(html, wait_until="networkidle", timeout=RENDER_TIMEOUT_MS)
            await asyncio.sleep(0.35)  # 等字体与布局稳定，避免截到半成品
            await page.screenshot(path=str(output_path), full_page=full_page)
        finally:
            await browser.close()

    return output_path


async def render_file_to_image(html_path: Path, **kwargs: Any) -> Path:
    """从已有 HTML 文件渲染图片。"""
    return await render_html_to_image(html_path.read_text(encoding="utf-8"), **kwargs)


def shrink_for_qq(
    image_path: Path | str,
    *,
    max_height: Optional[int] = None,
    output_path: Optional[Path] = None,
) -> Path:
    """把报告长图压缩到协议端能接受的高度。

    为什么需要这一步：
        报告图是 ``full_page`` 整页截图，实测高 7000+ px、宽 2098 px。
        QQ 服务端对这类超长图会**直接拒收**，而失败只发生在协议端一侧 ——
        接口仍然返回 200、日志里也未必有异常，表现为
        「文字发出来了、图片没有，且没有任何报错」，极难排查。

    做法是等比例缩放，不裁切：报告是给人看的，宁可小一点，
    也不能把下半截「优化方向」裁掉。

    :param max_height: 目标最大高度，默认取 config.push.image_max_height
    :param output_path: 输出路径，默认在原文件名后加 ``_push`` 后缀
    :return: 压缩后的图片路径；无需压缩或失败时原样返回 ``image_path``
    """
    config = get_config()
    limit = max_height or config.push.image_max_height or 2400

    source = Path(image_path)
    if not source.exists():
        return source

    try:
        from PIL import Image
    except ImportError:
        return source

    try:
        with Image.open(source) as img:
            width, height = img.size
            if height <= limit:
                # 本来就够短，不必重新编码（重编码会损失画质）
                return source

            ratio = limit / height
            new_size = (max(1, int(width * ratio)), limit)

            target = output_path or source.with_name(f"{source.stem}_push{source.suffix}")

            resized = img.resize(new_size, Image.LANCZOS)
            if resized.mode not in ("RGB", "L"):
                resized = resized.convert("RGB")

            # optimize + 适度量化：报告以大面积纯色为主，压缩率很高
            resized.save(target, format="PNG", optimize=True)
            return target
    except Exception as exc:  # noqa: BLE001 - 压缩失败就用原图，由协议端决定
        from loguru import logger

        logger.warning("图片压缩失败，改用原图：{}: {}", type(exc).__name__, exc)
        return source


__all__ = ["render_html_to_image", "render_file_to_image", "shrink_for_qq"]
