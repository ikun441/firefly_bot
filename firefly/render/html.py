"""报告静态 HTML 构建。

**关键约束：与前端共用同一份数据源（``Report.payload``）。**
前端做动画渲染、这里做静态出图，两者读的是同一个 JSON，
因此不可能出现「网页上写着涨 12%、图片上写着涨 8%」这种答辩翻车现场。

图表部分用内联 SVG 手绘，而不是引入 ECharts：
出图走的是 headless Chromium 截图，多一个外部 CDN 依赖就多一个失败点，
而且我们只需要柱状/饼图两种基本图形，手绘反而更可控、渲染更快。
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import get_config

# 与前端保持一致的配色（暗色深空 + 萤火青）
COLORS = {
    "bg": "#0f1420",
    "card": "#161c2b",
    "card_alt": "#1b2233",
    "border": "#263047",
    "text": "#e8eefc",
    "text_dim": "#94a3b8",
    "firefly": "#5eead4",
    "violet": "#a78bfa",
    "amber": "#fbbf24",
    "rose": "#fb7185",
    "emerald": "#34d399",
    "sky": "#38bdf8",
}

CHART_PALETTE = [
    "#5eead4", "#a78bfa", "#fbbf24", "#fb7185", "#34d399",
    "#38bdf8", "#f472b6", "#facc15", "#4ade80", "#c084fc",
]


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _money(value: Any, symbol: str = "¥") -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"{symbol}{number:,.2f}"


def _pct(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _level_color(level: str) -> str:
    return {
        "high": COLORS["rose"],
        "medium": COLORS["amber"],
        "low": COLORS["sky"],
        "info": COLORS["text_dim"],
    }.get(str(level).lower(), COLORS["text_dim"])


# ------------------------------------------------------------------------------
# 内联 SVG 图表
# ------------------------------------------------------------------------------


def _bar_chart(
    items: List[Dict[str, Any]],
    *,
    width: int = 640,
    height: int = 220,
    value_key: str = "amount",
    label_key: str = "name",
) -> str:
    """水平柱状图：类别名在左、数值在右，适合中文字段名。"""
    if not items:
        return f'<div style="color:{COLORS["text_dim"]};font-size:13px;padding:20px 0">暂无数据</div>'

    items = items[:8]
    row_height = 30
    total_height = max(height, len(items) * row_height + 10)
    label_width = 96
    bar_max = width - label_width - 90

    max_value = max(float(item.get(value_key) or 0) for item in items) or 1.0

    parts = [f'<svg width="{width}" height="{total_height}" viewBox="0 0 {width} {total_height}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">']

    for index, item in enumerate(items):
        value = float(item.get(value_key) or 0)
        bar_width = max(2.0, value / max_value * bar_max)
        y = index * row_height + 6
        color = CHART_PALETTE[index % len(CHART_PALETTE)]
        label = _esc(str(item.get(label_key, ""))[:8])
        icon = _esc(item.get("icon", ""))

        parts.append(
            f'<text x="0" y="{y + 15}" fill="{COLORS["text"]}" font-size="12.5" '
            f'font-family="-apple-system,PingFang SC,sans-serif">{icon} {label}</text>'
        )
        parts.append(
            f'<rect x="{label_width}" y="{y + 4}" width="{bar_max}" height="15" rx="7.5" '
            f'fill="{COLORS["border"]}" opacity="0.45"/>'
        )
        parts.append(
            f'<rect x="{label_width}" y="{y + 4}" width="{bar_width:.1f}" height="15" rx="7.5" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{label_width + bar_width + 9}" y="{y + 16}" fill="{COLORS["text_dim"]}" '
            f'font-size="12" font-family="-apple-system,PingFang SC,sans-serif">{value:,.2f}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def _ring_chart(
    items: List[Dict[str, Any]],
    *,
    size: int = 210,
    value_key: str = "amount",
    label_key: str = "name",
) -> str:
    """甜甜圈图：用 stroke-dasharray 手绘圆环，避免引入图表库。"""
    if not items:
        return f'<div style="color:{COLORS["text_dim"]};font-size:13px;padding:20px 0">暂无数据</div>'

    items = items[:7]
    total = sum(float(item.get(value_key) or 0) for item in items)
    if total <= 0:
        return f'<div style="color:{COLORS["text_dim"]};font-size:13px;padding:20px 0">暂无数据</div>'

    radius = size / 2 - 22
    circumference = 2 * 3.14159265 * radius
    center = size / 2

    # 环形图是方形 SVG，不能像条形图那样 width:100% ——
    # 拉满栅格后会比所有正文都大，喧宾夺主。
    # 限定最大宽度并居中，让它与相邻卡片保持视觉平衡。
    parts = [f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:{size}px;height:auto;display:block;margin:0 auto">']
    parts.append(
        f'<circle cx="{center}" cy="{center}" r="{radius}" fill="none" '
        f'stroke="{COLORS["border"]}" stroke-width="20" opacity="0.5"/>'
    )

    offset = 0.0
    for index, item in enumerate(items):
        value = float(item.get(value_key) or 0)
        fraction = value / total
        length = fraction * circumference
        color = CHART_PALETTE[index % len(CHART_PALETTE)]
        parts.append(
            f'<circle cx="{center}" cy="{center}" r="{radius}" fill="none" stroke="{color}" '
            f'stroke-width="20" stroke-dasharray="{length:.2f} {circumference - length:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {center} {center})"/>'
        )
        offset += length

    parts.append(
        f'<text x="{center}" y="{center - 2}" text-anchor="middle" fill="{COLORS["text"]}" '
        f'font-size="17" font-weight="600" font-family="-apple-system,PingFang SC,sans-serif">'
        f'{total:,.0f}</text>'
    )
    parts.append(
        f'<text x="{center}" y="{center + 17}" text-anchor="middle" fill="{COLORS["text_dim"]}" '
        f'font-size="11.5" font-family="-apple-system,PingFang SC,sans-serif">合计</text>'
    )
    parts.append("</svg>")

    legend = ['<div style="display:flex;flex-wrap:wrap;gap:10px 14px;margin-top:10px">']
    for index, item in enumerate(items):
        color = CHART_PALETTE[index % len(CHART_PALETTE)]
        value = float(item.get(value_key) or 0)
        legend.append(
            f'<div style="display:flex;align-items:center;gap:6px;font-size:12px;color:{COLORS["text_dim"]}">'
            f'<span style="width:9px;height:9px;border-radius:3px;background:{color};display:inline-block"></span>'
            f'{_esc(str(item.get(label_key, ""))[:8])} {value / total * 100:.1f}%</div>'
        )
    legend.append("</div>")

    return "".join(parts) + "".join(legend)


def _waterfall_chart(items: List[Dict[str, Any]], *, width: int = 716, height: int = 260) -> str:
    """归因瀑布图：把「涨了」拆成「哪几类各涨了多少」。"""
    if not items:
        return f'<div style="color:{COLORS["text_dim"]};font-size:13px;padding:20px 0">暂无归因数据</div>'

    items = items[:10]
    padding_left, padding_right, padding_top, padding_bottom = 54, 20, 24, 52
    plot_width = width - padding_left - padding_right
    plot_height = height - padding_top - padding_bottom

    # 累计值序列
    running = 0.0
    bars = []
    for item in items:
        value = float(item.get("value") or 0)
        kind = item.get("type", "increase")
        if kind == "total":
            bars.append({"label": item.get("name", ""), "start": 0.0, "end": value, "kind": "total"})
            running = value
        else:
            start = running
            end = running + value
            bars.append({"label": item.get("name", ""), "start": min(start, end), "end": max(start, end), "kind": kind, "delta": value})
            running = end

    max_value = max((bar["end"] for bar in bars), default=1.0) or 1.0
    max_value *= 1.12

    slot = plot_width / max(1, len(bars))
    bar_width = min(56.0, slot * 0.62)

    def to_y(value: float) -> float:
        return padding_top + plot_height - (value / max_value * plot_height)

    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">']

    # 网格线
    for step in range(5):
        value = max_value * step / 4
        y = to_y(value)
        parts.append(
            f'<line x1="{padding_left}" y1="{y:.1f}" x2="{width - padding_right}" y2="{y:.1f}" '
            f'stroke="{COLORS["border"]}" stroke-width="1" opacity="0.42"/>'
        )
        parts.append(
            f'<text x="{padding_left - 8}" y="{y + 4:.1f}" text-anchor="end" fill="{COLORS["text_dim"]}" '
            f'font-size="10.5" font-family="-apple-system,PingFang SC,sans-serif">{value:,.0f}</text>'
        )

    for index, bar in enumerate(bars):
        x = padding_left + index * slot + (slot - bar_width) / 2
        y_top = to_y(bar["end"])
        y_bottom = to_y(bar["start"])
        current_height = max(2.0, y_bottom - y_top)

        if bar["kind"] == "total":
            color = COLORS["violet"]
        elif bar["kind"] == "increase":
            color = COLORS["rose"]
        elif bar["kind"] == "decrease":
            color = COLORS["emerald"]
        else:
            color = COLORS["text_dim"]

        parts.append(
            f'<rect x="{x:.1f}" y="{y_top:.1f}" width="{bar_width:.1f}" height="{current_height:.1f}" '
            f'rx="5" fill="{color}" opacity="0.92"/>'
        )

        # 柱顶数值
        if "delta" in bar:
            text = f'{bar["delta"]:+,.0f}'
        else:
            text = f'{bar["end"]:,.0f}'
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{y_top - 6:.1f}" text-anchor="middle" '
            f'fill="{COLORS["text"]}" font-size="10.5" font-weight="600" '
            f'font-family="-apple-system,PingFang SC,sans-serif">{text}</text>'
        )

        # x 轴标签（过长则截断）
        label = bar["label"]
        if len(label) > 5:
            label = label[:5] + "…"
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{height - padding_bottom + 18:.1f}" text-anchor="middle" '
            f'fill="{COLORS["text_dim"]}" font-size="10.5" '
            f'font-family="-apple-system,PingFang SC,sans-serif">{_esc(label)}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def _line_chart(
    points: List[Dict[str, Any]],
    *,
    width: int = 660,
    height: int = 200,
    value_key: str = "expense",
    label_key: str = "label",
) -> str:
    """趋势折线图（带面积填充）。"""
    if not points:
        return f'<div style="color:{COLORS["text_dim"]};font-size:13px;padding:20px 0">暂无趋势数据</div>'

    padding_left, padding_right, padding_top, padding_bottom = 48, 18, 18, 34
    plot_width = width - padding_left - padding_right
    plot_height = height - padding_top - padding_bottom

    values = [float(point.get(value_key) or 0) for point in points]
    max_value = max(values) * 1.15 or 1.0

    def to_x(index: int) -> float:
        if len(points) == 1:
            return padding_left + plot_width / 2
        return padding_left + index * (plot_width / (len(points) - 1))

    def to_y(value: float) -> float:
        return padding_top + plot_height - (value / max_value * plot_height)

    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">']

    for step in range(4):
        value = max_value * step / 3
        y = to_y(value)
        parts.append(
            f'<line x1="{padding_left}" y1="{y:.1f}" x2="{width - padding_right}" y2="{y:.1f}" '
            f'stroke="{COLORS["border"]}" stroke-width="1" opacity="0.4"/>'
        )

    coordinates = [(to_x(index), to_y(value)) for index, value in enumerate(values)]

    area = " ".join(f"{x:.1f},{y:.1f}" for x, y in coordinates)
    parts.append(
        f'<polygon points="{padding_left},{padding_top + plot_height} {area} '
        f'{width - padding_right},{padding_top + plot_height}" fill="{COLORS["firefly"]}" opacity="0.14"/>'
    )
    parts.append(
        f'<polyline points="{area}" fill="none" stroke="{COLORS["firefly"]}" '
        f'stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>'
    )

    for index, (x, y) in enumerate(coordinates):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" fill="{COLORS["bg"]}" stroke="{COLORS["firefly"]}" stroke-width="2"/>')
        label = str(points[index].get(label_key, ""))
        parts.append(
            f'<text x="{x:.1f}" y="{height - padding_bottom + 17:.1f}" text-anchor="middle" '
            f'fill="{COLORS["text_dim"]}" font-size="10.5" '
            f'font-family="-apple-system,PingFang SC,sans-serif">{_esc(label)}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------------------------
# 页面装配
# ------------------------------------------------------------------------------


def _highlight_card(item: Dict[str, Any]) -> str:
    tone = str(item.get("tone", "info"))
    color = {
        "warning": COLORS["amber"],
        "positive": COLORS["emerald"],
        "danger": COLORS["rose"],
        "info": COLORS["sky"],
    }.get(tone, COLORS["text_dim"])

    return f"""
    <div style="flex:1;min-width:150px;background:{COLORS['card_alt']};border-radius:12px;
                padding:12px 15px;border-left:3px solid {color}">
      <div style="font-size:11.5px;color:{COLORS['text_dim']};margin-bottom:5px">{_esc(item.get('label'))}</div>
      <div style="font-size:15px;font-weight:600;color:{COLORS['text']};line-height:1.35">{_esc(item.get('value'))}</div>
      <div style="font-size:11px;color:{color};margin-top:6px">{_esc(item.get('desc', ''))}</div>
    </div>"""


def _check_row(item: Dict[str, Any]) -> str:
    level = str(item.get("level", "info")).lower()
    color = _level_color(level)
    label = {"high": "高风险", "medium": "中风险", "low": "低风险", "info": "提示"}.get(level, "提示")

    return f"""
    <div style="background:{COLORS['card_alt']};border-radius:10px;padding:12px 14px;
                margin-bottom:9px;border-left:3px solid {color}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
        <div style="font-size:13.5px;font-weight:600;color:{COLORS['text']}">{_esc(item.get('title'))}</div>
        <div style="font-size:11px;color:{color};white-space:nowrap;border:1px solid {color};
                    border-radius:20px;padding:1px 9px">{label}</div>
      </div>
      <div style="font-size:12px;color:{COLORS['text_dim']};margin-top:7px;line-height:1.65">
        {_esc(item.get('description'))}
      </div>
      <div style="font-size:12px;color:{COLORS['firefly']};margin-top:7px;line-height:1.6">
        💡 {_esc(item.get('suggestion'))}
      </div>
    </div>"""


def _action_row(item: Dict[str, Any], symbol: str) -> str:
    priority = str(item.get("priority", "low"))
    color = {"high": COLORS["rose"], "medium": COLORS["amber"], "low": COLORS["sky"]}.get(priority, COLORS["text_dim"])
    amount_text = ""
    if item.get("amount"):
        try:
            amount_text = f"　涉及 {symbol}{float(item['amount']):,.2f}"
        except (TypeError, ValueError):
            amount_text = ""

    return f"""
    <div style="display:flex;gap:11px;padding:11px 0;border-bottom:1px solid {COLORS['border']}">
      <div style="flex-shrink:0;width:20px;height:20px;border-radius:50%;background:{color};
                  color:#0f1420;font-size:11.5px;font-weight:700;display:flex;
                  align-items:center;justify-content:center">{_esc(item.get('index', ''))}</div>
      <div style="flex:1">
        <div style="font-size:13px;color:{COLORS['text']};font-weight:600">
          {_esc(item.get('icon', ''))} {_esc(item.get('title'))}{amount_text}
        </div>
        <div style="font-size:11.5px;color:{COLORS['text_dim']};margin-top:4px;line-height:1.6">
          {_esc(item.get('description', ''))}
        </div>
      </div>
    </div>"""


def build_report_html(report: Any, *, for_image: bool = False, ai_summary: str = "") -> str:
    """根据 Report.payload 构建静态 HTML。

    Args:
        report: ``Report`` 实例
        for_image: 为 True 时使用固定宽度与打印友好样式，供截图使用
    """
    config = get_config()
    import base64
    from ..config import INDEX_DIR
    logo = INDEX_DIR / "assets/brand/firefly-logo.jpg"
    logo_html = '<img alt="流萤 Logo" width="44" height="44" style="border-radius:12px;object-fit:cover;vertical-align:middle;margin-right:10px" src="data:image/jpeg;base64,' + base64.b64encode(logo.read_bytes()).decode() + '" />' if logo.is_file() else ""
    payload = report.payload or {}
    symbol = config.ledger.currency_symbol

    meta = payload.get("meta", {})
    overview = payload.get("overview", {})
    structure = payload.get("structure", {})
    anomaly = payload.get("anomaly", {})
    budget = payload.get("budget", {})
    attribution = payload.get("attribution", {})
    trend = payload.get("trend", {})
    tax = payload.get("tax", {})
    optimization = payload.get("optimization", {})
    highlights = payload.get("highlights", [])

    period_label = meta.get("period_label") or report.period
    generated_at = meta.get("generated_at") or (report.created_at.isoformat() if report.created_at else datetime.now().isoformat())

    compare = overview.get("compare", {})

    def compare_text(field: str) -> str:
        item = compare.get(field) or {}
        ratio = item.get("ratio")
        if ratio is None:
            return "—"
        arrow = "↑" if item.get("direction") == "up" else "↓" if item.get("direction") == "down" else "→"
        return f"{arrow} {abs(float(ratio)):.1f}%"

    def compare_color(field: str, invert: bool = False) -> str:
        item = compare.get(field) or {}
        direction = item.get("direction")
        if direction == "flat" or direction is None:
            return COLORS["text_dim"]
        up_is_bad = not invert
        if direction == "up":
            return COLORS["rose"] if up_is_bad else COLORS["emerald"]
        return COLORS["emerald"] if up_is_bad else COLORS["rose"]

    # 服务层输出的键名是 expense_categories / income_categories；
    # 这里兼容 by_category 旧写法，避免两边字段名不一致导致图表空白
    category_items = (
        structure.get("expense_categories")
        or structure.get("by_category")
        or []
    )
    # 服务层的趋势数据在 monthly 键下，元素形如 {label, income, expense}；
    # 兼容 points 旧写法，避免字段名不一致导致趋势图整块消失
    trend_points = trend.get("monthly") or trend.get("points") or []
    waterfall_items = attribution.get("waterfall", []) or []
    checks = tax.get("checks", []) or []
    action_items = optimization.get("action_items", []) or []

    max_width = 760 if for_image else 1000

    body = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(report.title or '流萤财税报告')}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: {'20px' if for_image else '30px 20px'};
    background: {COLORS['bg']};
    color: {COLORS['text']};
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: {max_width}px; margin: 0 auto; }}
  .card {{
    background: {COLORS['card']};
    border: 1px solid {COLORS['border']};
    border-radius: 16px;
    padding: 20px 22px;
    margin-bottom: 16px;
  }}
  .section-title {{
    font-size: 15px; font-weight: 700; margin: 0 0 15px 0;
    display: flex; align-items: center; gap: 8px;
  }}
  .section-title::before {{
    content: ''; width: 3px; height: 15px; border-radius: 2px;
    background: {COLORS['firefly']};
  }}
  .two-col {{ display: flex; gap: 18px; flex-wrap: wrap; }}
  .col {{ flex: 1; min-width: 260px; }}
  .metric-grid {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  .metric {{
    flex: 1; min-width: 132px; background: {COLORS['card_alt']};
    border-radius: 12px; padding: 13px 15px;
  }}
  .metric-label {{ font-size: 11.5px; color: {COLORS['text_dim']}; margin-bottom: 7px; }}
  .metric-value {{ font-size: 20px; font-weight: 700; letter-spacing: -0.4px; }}
  .metric-sub {{ font-size: 11px; margin-top: 5px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  th {{
    text-align: left; padding: 8px 8px; color: {COLORS['text_dim']};
    font-weight: 500; font-size: 11.5px; border-bottom: 1px solid {COLORS['border']};
  }}
  td {{ padding: 9px 8px; border-bottom: 1px solid {COLORS['border']}60; }}
  tr:last-child td {{ border-bottom: none; }}
  .badge {{
    display: inline-block; padding: 2px 9px; border-radius: 20px;
    font-size: 11px; font-weight: 600;
  }}
  .footer-note {{
    font-size: 11px; color: {COLORS['text_dim']}; line-height: 1.75;
    text-align: center; padding: 14px 0 4px;
  }}
</style>
</head>
<body>
<div class="wrap">

  <!-- 报头 -->
  <div class="card" style="background:linear-gradient(135deg,{COLORS['card']} 0%,#1a2233 100%)">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap">
      <div>
        <div style="font-size:20px;font-weight:800;letter-spacing:-0.4px;margin-bottom:7px">
          ✨ {_esc(report.title or f'{period_label}财税报告')}
        </div>
        <div style="font-size:12px;color:{COLORS['text_dim']}">
          {_esc(period_label)} · 统计区间 {_esc(meta.get('period_start', '')[:10])} ~ {_esc(meta.get('period_end', '')[:10])}
          · 共 {_esc(meta.get('transaction_count', 0))} 笔流水
        </div>
      </div>
      <div style="text-align:right;font-size:11px;color:{COLORS['text_dim']}">
        <div style="color:{COLORS['firefly']};font-weight:600;font-size:12.5px">{logo_html}流萤 Firefly</div>
        <div style="margin-top:4px">生成于 {_esc(str(generated_at)[:19].replace('T', ' '))}</div>
        <div style="margin-top:3px">耗时 {_esc(report.elapsed_ms or 0)} ms</div>
      </div>
    </div>
  </div>

  <!-- 高亮结论 -->
  {f'<div class="card"><div class="section-title">本期要点</div><div style="display:flex;gap:11px;flex-wrap:wrap">{"".join(_highlight_card(h) for h in highlights[:4])}</div></div>' if highlights else ''}

  <!-- 一、财务税务整体状况 -->
  <div class="card">
    <div class="section-title">一、财务税务整体状况</div>

    <div class="metric-grid">
      <div class="metric">
        <div class="metric-label">本期收入</div>
        <div class="metric-value" style="color:{COLORS['emerald']}">{_money(overview.get('income'), symbol)}</div>
        <div class="metric-sub" style="color:{compare_color('income', invert=True)}">{compare_text('income')} 环比</div>
      </div>
      <div class="metric">
        <div class="metric-label">本期支出</div>
        <div class="metric-value" style="color:{COLORS['rose']}">{_money(overview.get('expense'), symbol)}</div>
        <div class="metric-sub" style="color:{compare_color('expense')}">{compare_text('expense')} 环比</div>
      </div>
      <div class="metric">
        <div class="metric-label">净结余</div>
        <div class="metric-value" style="color:{COLORS['firefly'] if float(overview.get('net') or 0) >= 0 else COLORS['rose']}">{_money(overview.get('net'), symbol)}</div>
        <div class="metric-sub" style="color:{COLORS['text_dim']}">结余率 {_pct(overview.get('savings_rate'))}</div>
      </div>
      <div class="metric">
        <div class="metric-label">日均支出</div>
        <div class="metric-value">{_money(overview.get('avg_daily_expense'), symbol)}</div>
        <div class="metric-sub" style="color:{COLORS['text_dim']}">净资产 {_money(overview.get('net_assets'), symbol)}</div>
      </div>
    </div>

    <div class="two-col" style="margin-top:20px">
      <div class="col">
        <div style="font-size:12.5px;color:{COLORS['text_dim']};margin-bottom:9px">支出结构 TOP {min(8, len(category_items))}</div>
        {_ring_chart(category_items)}
      </div>
      <div class="col">
        <div style="font-size:12.5px;color:{COLORS['text_dim']};margin-bottom:9px">类别支出金额</div>
        {_bar_chart(category_items)}
      </div>
    </div>
  </div>

  {f'''<div class="card">
    <div class="section-title">支出变动归因</div>
    <div style="font-size:12px;color:{COLORS['text_dim']};margin-bottom:12px">{_esc(attribution.get('summary', ''))}</div>
    {_waterfall_chart(waterfall_items)}
  </div>''' if waterfall_items else ''}

  {f'''<div class="card">
    <div class="section-title">近 {len(trend_points)} 期趋势</div>
    {_line_chart(trend_points)}
  </div>''' if trend_points else ''}

  <!-- 异常与预算 -->
  <div class="card">
    <div class="section-title">异常检测与预算执行</div>
    <div class="two-col">
      <div class="col">
        <table>
          <tr><td style="color:{COLORS['text_dim']}">扫描流水</td><td style="text-align:right">{_esc(anomaly.get('scanned_count', anomaly.get('total_count', 0)))} 笔</td></tr>
          <tr><td style="color:{COLORS['text_dim']}">金额异常</td><td style="text-align:right;color:{COLORS['amber']}">{_esc(anomaly.get('anomaly_count', 0))} 笔</td></tr>
          <tr><td style="color:{COLORS['text_dim']}">个人消费混入</td><td style="text-align:right;color:{COLORS['rose']}">{_esc(anomaly.get('personal_mixed_count', 0))} 笔</td></tr>
          <tr><td style="color:{COLORS['text_dim']}">异常金额占比</td><td style="text-align:right">{_pct(anomaly.get('anomaly_amount_ratio', anomaly.get('anomaly_ratio')))}</td></tr>
        </table>
      </div>
      <div class="col">
        {f'''<table>
          <tr><td style="color:{COLORS['text_dim']}">预算合计</td><td style="text-align:right">{_money(budget.get('total_budget'), symbol)}</td></tr>
          <tr><td style="color:{COLORS['text_dim']}">已使用</td><td style="text-align:right">{_money(budget.get('total_actual'), symbol)}</td></tr>
          <tr><td style="color:{COLORS['text_dim']}">执行率</td><td style="text-align:right;color:{COLORS['rose'] if float(budget.get('total_usage_ratio') or 0) >= 100 else COLORS['firefly']}">{_pct(budget.get('total_usage_ratio'))}</td></tr>
          <tr><td style="color:{COLORS['text_dim']}">超支类别</td><td style="text-align:right">{_esc(budget.get('over_count', 0))} 个</td></tr>
        </table>''' if budget.get('has_budget') else f'<div style="font-size:12.5px;color:{COLORS["text_dim"]};line-height:1.8">尚未设置预算。<br/>在「预算」页为常用类别设定额度后，报告会自动计算执行率并在接近上限时提醒。</div>'}
      </div>
    </div>
  </div>

  <!-- 二、财务税务优化方向 -->
  <div class="card">
    <div class="section-title">二、财务税务优化方向</div>

    <div class="metric-grid" style="margin-bottom:18px">
      <div class="metric">
        <div class="metric-label">税务风险项</div>
        <div class="metric-value" style="color:{_level_color(tax.get('highest_level', 'info'))}">{_esc(tax.get('risk_count', 0))}</div>
        <div class="metric-sub" style="color:{COLORS['text_dim']}">高风险 {_esc(tax.get('counts', {}).get('high', 0))} 项</div>
      </div>
      <div class="metric">
        <div class="metric-label">纳税人身份</div>
        <div class="metric-value" style="font-size:16px">{'小规模纳税人' if tax.get('taxpayer_type') == 'small_scale' else '一般纳税人'}</div>
        <div class="metric-sub" style="color:{COLORS['text_dim']}">周期 {_esc(tax.get('period_days', 30))} 天</div>
      </div>
      <div class="metric">
        <div class="metric-label">可扣除支出</div>
        <div class="metric-value" style="font-size:17px">{_money(tax.get('deductible_expense'), symbol)}</div>
        <div class="metric-sub" style="color:{COLORS['text_dim']}">已挂税务口径科目</div>
      </div>
      <div class="metric">
        <div class="metric-label">优化行动项</div>
        <div class="metric-value" style="color:{COLORS['amber']}">{len(action_items)}</div>
        <div class="metric-sub" style="color:{COLORS['text_dim']}">按优先级排序</div>
      </div>
    </div>

    {f'<div style="font-size:12.5px;color:{COLORS["text_dim"]};margin-bottom:10px">税务风险自检清单</div>{"".join(_check_row(c) for c in checks)}' if checks else ''}

    {f'<div style="font-size:12.5px;color:{COLORS["text_dim"]};margin:20px 0 6px">优先行动项</div>{"".join(_action_row(a, symbol) for a in action_items[:8])}' if action_items else ''}
  </div>

  {f'<div class="card" data-section="ai-summary"><div class="section-title">AI 解读</div><div style="font-size:13px;line-height:1.95;white-space:pre-wrap">{_esc(ai_summary)}</div></div>' if ai_summary else ''}

  <!-- 摘要文本 -->
  {f'''<div class="card">
    <div class="section-title">文字摘要</div>
    <div style="font-size:12.5px;line-height:1.95;color:{COLORS['text']};white-space:pre-wrap">{_esc(report.summary_text or '')}</div>
  </div>''' if report.summary_text else ''}

  <div class="footer-note">
    {_esc(tax.get('disclaimer') or config.tax_check.disclaimer)}<br/>
    由「流萤 Firefly」自动生成 · 数据口径：{_esc(meta.get('currency_symbol', symbol))} · 报告 ID {_esc(report.id)}
  </div>

</div>
</body>
</html>
"""

    return body


async def render_report_html(report: Any, *, ai_summary: str = "", artifact_id: str = "") -> Path:
    """把报告渲染成独立 HTML 文件并返回路径。

    注意用的是 ``for_image=True``：这份 HTML 是给出图用的，
    必须走窄版布局（max_width 760）。

    曾经这里漏了 ``for_image=True``，后果是出图沿用网页版的 1000px 布局：
    画布越宽，截图后被缩到 QQ 可读尺寸时的降采样倍数就越大，
    正文从 14px 一路糊到 4.8px。
    布局宽度直接决定最终清晰度，不是无关紧要的样式细节。
    """
    from ..config import DATA_DIR

    output_dir = DATA_DIR / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    suffix = "_" + artifact_id if artifact_id else ""
    path = output_dir / f"report_{report.id}{suffix}.html"
    path.write_text(build_report_html(report, for_image=True, ai_summary=ai_summary), encoding="utf-8")
    return path


__all__ = ["build_report_html", "render_report_html"]
