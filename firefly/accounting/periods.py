"""Shared, deterministic date ranges for accounting commands."""

import calendar
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from firefly.config import get_config
from .domain import AccountingError

USAGE = "支持：今天、昨天、本周、上周、本月、上月、本季度、上季度、本年、去年、2026-11、2026 或 2026-11-01 2026-11-30"


def local_today():
    return datetime.now(ZoneInfo(get_config().app.timezone)).date()


def parse_range(value="", *, today=None):
    today = today or local_today()
    value = value.strip() or "本月"
    aliases = {
        "今日": "今天",
        "昨日": "昨天",
        "这个月": "本月",
        "上个月": "上月",
        "今年": "本年",
        "本年度": "本年",
        "上年": "去年",
        "本季": "本季度",
        "上季": "上季度",
    }
    value = aliases.get(value, value)
    if value == "今天":
        return today, today
    if value == "昨天":
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday
    if value in {"本周", "上周"}:
        monday = today - timedelta(days=today.weekday())
        return (
            (monday, today)
            if value == "本周"
            else (monday - timedelta(days=7), monday - timedelta(days=1))
        )
    if value == "本月":
        return today.replace(day=1), today
    if value == "上月":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if value in {"本季度", "上季度"}:
        start = date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
        if value == "本季度":
            return start, today
        end = start - timedelta(days=1)
        return date(end.year, (end.month - 1) // 3 * 3 + 1, 1), end
    if value == "本年":
        return date(today.year, 1, 1), today
    if value == "去年":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    try:
        if re.fullmatch(r"\d{4}", value):
            year = int(value)
            return date(year, 1, 1), date(year, 12, 31)
        if re.fullmatch(r"\d{4}-\d{2}", value):
            start = date.fromisoformat(value + "-01")
            return start, start.replace(
                day=calendar.monthrange(start.year, start.month)[1]
            )
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            day = date.fromisoformat(value)
            return day, day
        match = re.fullmatch(
            r"(\d{4}-\d{2}-\d{2})(?:\s+|\s*(?:至|到|~|～)\s*)(\d{4}-\d{2}-\d{2})", value
        )
        if match:
            start, end = (date.fromisoformat(part) for part in match.groups())
            if end < start:
                raise AccountingError(
                    "DATE_RANGE", "截止日期不能早于开始日期。" + USAGE
                )
            return start, end
    except ValueError as exc:
        if isinstance(exc, AccountingError):
            raise
        raise AccountingError("DATE_INVALID", "日期或月份无效。" + USAGE) from None
    raise AccountingError("PERIOD_ARGUMENT", "无法识别查询周期。" + USAGE)


def parse_detail_args(value="", *, today=None):
    page = 1
    match = re.search(r"(?:^|\s)第(\d+)页$", value.strip())
    if match:
        page = int(match.group(1))
        value = value[: match.start()].strip()
    if not 1 <= page <= 10000:
        raise AccountingError("PAGE_INVALID", "页码须为 1–10000")
    start, end = parse_range(value, today=today)
    return start, end, page
