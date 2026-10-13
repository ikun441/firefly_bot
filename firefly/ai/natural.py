"""自然语言记账：把「昨天打车 38」这类自由文本抽取成结构化流水。

设计要点
--------
1. **不依赖 LLM 也能工作**：内置规则抽取器处理常见中文记账句式，
   它同时承担两个职责 —— LLM 不可用时的降级方案，以及 LLM 输出异常时的兜底。
2. **金额与方向分离**：金额统一取正数，方向由 ``direction`` 表达，
   与 ``Transaction`` 表的约定保持一致。
3. **时间归一化**：把「昨天」「上周五」「3号」这类相对时间折算成绝对时间，
   折算基准是当前时刻而非模型臆测。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..config import get_config
from .llm import LLMNotReadyError, chat_json, is_ready

# ------------------------------------------------------------------------------
# 规则抽取用的词表
# ------------------------------------------------------------------------------

INCOME_KEYWORDS = [
    "收入",
    "收款",
    "收到",
    "进账",
    "工资",
    "报销",
    "退款",
    "返现",
    "利息",
    "分红",
    "客户付款",
    "结算",
    "回款",
]

CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    # 关键词表同时服务两个场景：自然语言记账的抽取、以及导入账单的类别兜底。
    # 因此除了口语词，也要收**账单里真实出现的商户与摘要词**
    # （如「餐饮」「超市」「便利店」），否则导入的账会大量落到「未分类」。
    "餐饮": [
        "吃饭", "午餐", "晚餐", "早餐", "午饭", "晚饭", "早饭", "外卖", "餐厅", "饭店",
        "聚餐", "请客", "咖啡", "奶茶", "喝", "用餐", "快餐", "食堂", "餐饮", "食品",
        "小吃", "烧烤", "火锅", "面馆", "酒楼", "茶饮", "烘焙", "汉堡", "披萨",
    ],
    "交通": [
        "打车", "出租车", "滴滴", "地铁", "公交", "高铁", "火车", "机票", "飞机",
        "加油", "停车", "过路费", "油费", "交通", "车费", "出行", "网约车", "高速公路",
        "共享单车", "充电桩",
    ],
    "办公": [
        "办公", "文具", "打印", "复印", "耗材", "电脑", "显示器", "键盘", "鼠标",
        "软件", "订阅", "纸张", "墨盒", "办公用品",
    ],
    "房租": ["房租", "租金", "租房", "物业", "宽带", "网费", "水电", "租赁"],
    "通信": ["话费", "流量", "电话费", "手机费", "通信", "充值话费", "宽带费"],
    "采购": ["采购", "进货", "原材料", "库存", "物料", "货物", "批发"],
    "宣传推广": ["广告", "推广", "营销", "投放", "宣传", "展会", "印刷", "设计费"],
    "培训学习": ["培训", "学习", "课程", "书籍", "图书", "考试", "报名费", "学费"],
    "差旅": ["差旅", "住宿", "酒店", "旅馆", "出差", "宾馆", "民宿"],
    "医疗": ["医院", "看病", "买药", "体检", "挂号", "药品", "诊所", "药房"],
    "娱乐": ["电影", "游戏", "KTV", "旅游", "演唱会", "娱乐", "健身", "会员", "门票"],
    "购物": [
        "购物", "买衣服", "服装", "化妆品", "日用品", "超市", "电商", "淘宝", "京东",
        "拼多多", "商场", "便利店", "百货", "天猫", "苏宁",
    ],
}

# 相对时间词 → 偏移天数
RELATIVE_DAYS = {
    "今天": 0,
    "今日": 0,
    "昨天": -1,
    "昨日": -1,
    "前天": -2,
    "大前天": -3,
    "明天": 1,
    "后天": 2,
}

WEEKDAY_MAP = {
    "周一": 0,
    "星期一": 0,
    "周二": 1,
    "星期二": 1,
    "周三": 2,
    "星期三": 2,
    "周四": 3,
    "星期四": 3,
    "周五": 4,
    "星期五": 4,
    "周六": 5,
    "星期六": 5,
    "周日": 6,
    "周天": 6,
    "星期日": 6,
    "星期天": 6,
}

AMOUNT_PATTERN = re.compile(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱|圆|rmb|RMB|¥|￥)?")


# ------------------------------------------------------------------------------
# 规则抽取器
# ------------------------------------------------------------------------------


def _extract_amount(text: str) -> Optional[float]:
    """从文本中抽取金额。

    优先匹配带货币单位的数字，避免把「昨天」抽成天数、把「3 号」抽成金额。
    """
    # 1) 带单位
    match = re.search(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱|圆|RMB|rmb|¥|￥)", text)
    if match:
        return float(match.group(1))

    # 2) 排除日期/时间/数量词后取最后一个独立数字
    cleaned = re.sub(r"\d+\s*(?:月|日|号|点|时|分|人|个|份|次|天|周|年)", " ", text)
    numbers = re.findall(r"(?<![.\d])(\d+(?:\.\d{1,2})?)(?![.\d])", cleaned)
    if numbers:
        return float(numbers[-1])

    # 3) 中文金额（三千五 / 两百）
    return _chinese_amount(text)


CN_DIGITS = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def _chinese_amount(text: str) -> Optional[float]:
    """解析简单的中文数字金额，如「三百」「一千二」。"""
    match = re.search(r"([零一两二三四五六七八九十百千万]+)\s*(?:元|块|块钱)?", text)
    if not match:
        return None

    token = match.group(1)
    total = 0
    section = 0
    number = 0

    for char in token:
        if char in CN_DIGITS:
            number = CN_DIGITS[char]
        elif char in CN_UNITS:
            unit = CN_UNITS[char]
            if unit >= 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            number = 0

    result = total + section + number
    return float(result) if result > 0 else None


def _extract_time(text: str, reference: Optional[datetime] = None) -> Optional[datetime]:
    """把相对时间表达折算成绝对时间。"""
    now = reference or datetime.now()

    # 相对日
    for word, offset in RELATIVE_DAYS.items():
        if word in text:
            return (now + timedelta(days=offset)).replace(
                hour=12, minute=0, second=0, microsecond=0
            )

    # 上周/本周 + 星期
    for word, weekday in WEEKDAY_MAP.items():
        if word in text:
            days_ahead = weekday - now.weekday()
            if "上周" in text or "上个" in text:
                days_ahead -= 7
            elif days_ahead > 0:
                days_ahead -= 7  # 「周五」默认指刚过去的周五
            return (now + timedelta(days=days_ahead)).replace(
                hour=12, minute=0, second=0, microsecond=0
            )

    # 显式日期 3/15、3月15日、2025-03-15
    match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return datetime(year, month, day, 12, 0, 0)

    match = re.search(r"(\d{1,2})\s*[月/-]\s*(\d{1,2})\s*[日号]?", text)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            year = now.year
            try:
                candidate = datetime(year, month, day, 12, 0, 0)
            except ValueError:
                return None
            if candidate > now + timedelta(days=1):
                candidate = candidate.replace(year=year - 1)
            return candidate

    # 「15 号」
    match = re.search(r"(\d{1,2})\s*[日号]", text)
    if match:
        day = int(match.group(1))
        if 1 <= day <= 31:
            try:
                candidate = datetime(now.year, now.month, day, 12, 0, 0)
            except ValueError:
                return None
            if candidate > now:
                month = now.month - 1 or 12
                year = now.year if now.month > 1 else now.year - 1
                try:
                    candidate = datetime(year, month, day, 12, 0, 0)
                except ValueError:
                    return None
            return candidate

    return None


def _guess_category(text: str) -> Optional[str]:
    """按关键词命中数猜测类别，命中越多越可能是它。"""
    best: Optional[str] = None
    best_score = 0

    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword in text)
        if score > best_score:
            best = category
            best_score = score

    return best


def _guess_direction(text: str) -> str:
    """判断收支方向，默认按支出处理（记账场景中支出占绝大多数）。"""
    for keyword in INCOME_KEYWORDS:
        if keyword in text:
            return "income"
    if re.search(r"(收入|收款|到账|入账)", text):
        return "income"
    return "expense"


def rule_extract(text: str, reference: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """规则抽取：支持「，」「、」「;」「和」「还有」等分隔的多笔记录。"""
    # 先按分隔符切分，保留口语化连接词
    segments = re.split(r"[，,；;。\n]+|还有|以及|加上", text)
    items: List[Dict[str, Any]] = []

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        amount = _extract_amount(segment)
        if amount is None or amount <= 0:
            continue

        trade_time = _extract_time(segment, reference)
        category = _guess_category(segment)
        direction = _guess_direction(segment)

        # 清理备注：去掉金额与时间表达，留下有意义的描述
        memo = re.sub(r"\d+(?:\.\d{1,2})?\s*(?:元|块|块钱|圆|RMB|rmb|¥|￥)", "", segment)
        memo = memo.strip(" ，,。;；") or segment.strip()

        items.append(
            {
                "amount": round(amount, 2),
                "direction": direction,
                "category": category,
                "trade_time": trade_time,
                "counterparty": None,
                "memo": memo,
                "confidence": 0.62,
                "source": "rule",
            }
        )

    return items


# ------------------------------------------------------------------------------
# LLM 抽取
# ------------------------------------------------------------------------------

SYSTEM_PROMPT = """你是一个中文记账信息抽取引擎。请从用户的自然语言中抽取记账记录。

严格遵守以下规则：
1. 只输出 JSON，不要输出任何解释文字、不要使用 markdown 代码块。
2. 金额一律为大于 0 的数字，不要带货币符号。支出与收入都用正数，方向由 direction 字段表达。
3. direction 只能是 "expense"（支出）或 "income"（收入）。
4. trade_time 输出 ISO 8601 本地时间字符串（YYYY-MM-DDTHH:MM:SS）。若用户说「昨天」「上周五」等相对时间，请换算为绝对时间。
5. category 从给定类别列表中选择最贴近的一个；没有合适的就填 null。
6. confidence 是你对这条抽取结果的置信度，范围 0 到 1。
7. 一句话里可能包含多条记录（用逗号、顿号、句号分隔），请全部抽出来。

输出格式：
{"items": [{"amount": 38, "direction": "expense", "category": "交通", "trade_time": "2025-03-14T12:00:00", "counterparty": null, "memo": "打车", "confidence": 0.95}]}"""


async def extract_transactions(
    text: str,
    *,
    categories: Optional[List[str]] = None,
    reference: Optional[datetime] = None,
) -> Dict[str, Any]:
    """抽取入口：LLM 优先，失败时降级到规则抽取器。

    Returns:
        ``{"success": bool, "items": [...], "engine": "llm"|"rule", "message": str}``
    """
    if not text or not text.strip():
        return {"success": False, "items": [], "engine": "none", "message": "请输入要记账的内容"}

    config = get_config()
    category_hint = "、".join(categories or []) or "（无预设类别，可填 null）"
    now = reference or datetime.now()

    if config.llm.enabled and is_ready():
        user_prompt = (
            f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}（星期{'一二三四五六日'[now.weekday()]}）\n"
            f"可用类别：{category_hint}\n"
            f"用户输入：{text}"
        )
        try:
            result = await chat_json(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
            )

            items = _normalize_items(result.get("items", []), text)
            if items:
                return {
                    "success": True,
                    "items": items,
                    "engine": "llm",
                    "message": f"AI 抽取到 {len(items)} 条记录",
                }
        except LLMNotReadyError:
            pass
        except Exception as exc:  # noqa: BLE001 - 任何异常都降级，保证用户至少能记账
            fallback = rule_extract(text, reference)
            return {
                "success": bool(fallback),
                "items": fallback,
                "engine": "rule",
                "message": f"AI 解析失败已降级为规则解析：{type(exc).__name__}",
            }

    items = rule_extract(text, reference)
    return {
        "success": bool(items),
        "items": items,
        "engine": "rule",
        "message": "已使用规则解析（未配置 LLM API Key）" if items else "未能从这句话中识别出金额",
    }


def _normalize_items(raw_items: Any, source_text: str) -> List[Dict[str, Any]]:
    """把 LLM 输出规整成统一结构，剔除脏数据。"""
    if not isinstance(raw_items, list):
        return []

    items: List[Dict[str, Any]] = []

    for raw in raw_items:
        if not isinstance(raw, dict):
            continue

        try:
            amount = abs(float(raw.get("amount", 0)))
        except (TypeError, ValueError):
            continue

        if amount <= 0:
            continue

        direction = str(raw.get("direction", "expense")).lower()
        if direction not in ("expense", "income"):
            direction = "expense"

        trade_time = raw.get("trade_time") or raw.get("time")
        parsed_time: Optional[datetime] = None
        if isinstance(trade_time, str) and trade_time.strip():
            try:
                parsed_time = datetime.fromisoformat(trade_time.strip().replace("Z", "+00:00"))
            except ValueError:
                parsed_time = _extract_time(trade_time)
        if parsed_time is None:
            parsed_time = _extract_time(source_text) or datetime.now()

        try:
            confidence = float(raw.get("confidence", 0.85))
        except (TypeError, ValueError):
            confidence = 0.85

        items.append(
            {
                "amount": round(amount, 2),
                "direction": direction,
                "category": raw.get("category") or None,
                "trade_time": parsed_time,
                "counterparty": raw.get("counterparty") or None,
                "memo": (raw.get("memo") or source_text)[:200],
                "confidence": max(0.0, min(1.0, confidence)),
                "source": "llm",
            }
        )

    return items


__all__ = ["extract_transactions", "rule_extract"]
