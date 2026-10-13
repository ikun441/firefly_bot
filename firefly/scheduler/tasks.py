"""定时任务：报告推送、订阅自动入账、阈值预警。

所有任务都用同一种模式：**先查配置是否启用，再执行，最后写审计日志**。
这样答辩时可以明确指出「哪些动作是系统自动做的」，而日志能为每条动作提供凭据。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..config import get_config

logger = logging.getLogger("firefly.scheduler")

_scheduler = None


def _get_scheduler():
    """拿到 nonebot-plugin-apscheduler 的调度器实例。"""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    try:
        from nonebot_plugin_apscheduler import scheduler

        _scheduler = scheduler
        return _scheduler
    except Exception:  # noqa: BLE001 - 插件未加载时退回独立调度器
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler

            _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
            return _scheduler
        except Exception:  # noqa: BLE001
            return None


# ------------------------------------------------------------------------------
# 任务：定时生成并推送报告
# ------------------------------------------------------------------------------


async def job_push_report() -> Dict[str, Any]:
    """生成当期报告、渲染成图片、推送到协议端。"""
    config = get_config()
    if not config.push.enabled:
        return {"skipped": True, "reason": "push.enabled = false"}

    from ..db.session import session_scope
    from ..render.push import push_report
    from ..services import report as report_service

    async with session_scope() as session:
        from ..api.deps import get_or_create_user

        user = await get_or_create_user(session)

        report = await report_service.generate_report(
            session,
            user_id=user.id,
            period=config.push.report_period,
            persist=True,
            channel="bot",
        )

        # 定时推送沿用 config.yaml 的 push 分组：
        # content_type 决定发文字/图片/都发，ai_summary 决定是否附 AI 解读。
        # 手动推送时这些可以在前端逐次覆盖，定时任务则固定用配置值。
        result = await push_report(
            report=report,
            session=session,
            content_type=config.push.content_type,
            ai_summary=config.push.ai_summary,
            ai_temperature=config.push.ai_temperature,
            ai_max_tokens=config.push.ai_max_tokens,
        )

    logger.info("定时报告推送完成: %s", result)
    return result


# ------------------------------------------------------------------------------
# 任务：订阅自动入账
# ------------------------------------------------------------------------------


async def job_subscriptions() -> Dict[str, Any]:
    """把到期的周期订阅转成真实流水。"""
    config = get_config()
    if not config.subscription.enabled:
        return {"skipped": True, "reason": "subscription.enabled = false"}

    from ..db.session import session_scope
    from ..services import subscription as subscription_service

    async with session_scope() as session:
        result = await subscription_service.run_due_subscriptions(session)

    if result["processed"]:
        logger.info("周期订阅自动入账 %d 条", result["processed"])
    return result


# ------------------------------------------------------------------------------
# 任务：阈值主动预警
# ------------------------------------------------------------------------------


async def job_alerts() -> Dict[str, Any]:
    """检查单日支出上限、预算超限、余额安全线，触发时主动推送。

    每个预警当天只发一次 —— 用一个 SystemState 键记录已发送的日期，
    避免调度器重试或多次触发时把用户轰炸一遍。
    """
    config = get_config()
    if not config.alert.enabled:
        return {"skipped": True, "reason": "alert.enabled = false"}

    from sqlmodel import select

    from ..db.models import SystemState
    from ..db.session import session_scope
    from ..services import budget as budget_service
    from ..services import ledger as ledger_service

    alerts: List[str] = []
    today = date.today()

    async with session_scope() as session:
        from ..api.deps import get_or_create_user

        user = await get_or_create_user(session)

        # 1) 单日支出
        day_start = datetime.combine(today, datetime.min.time())
        day_end = day_start + timedelta(days=1)
        daily = await ledger_service.sum_by_direction(
            session, user_id=user.id, start=day_start, end=day_end
        )
        if daily["expense"] > config.alert.daily_expense_limit:
            alerts.append(
                f"⚠️ 今日支出已达 {daily['expense']:.2f} 元，"
                f"超过单日上限 {config.alert.daily_expense_limit:.2f} 元。"
            )

        # 2) 预算超限
        month_start, month_end = ledger_service.period_bounds("monthly")
        status = await budget_service.get_budget_status(
            session, user_id=user.id, start=month_start, end=month_end, period="monthly"
        )
        for item in status.get("items", []):
            if item["state"] == "over":
                alerts.append(
                    f"🔴 预算超支：「{item['name']}」已用 {item['actual']:.2f} / "
                    f"{item['budget']:.2f} 元（{item['usage_ratio'] * 100:.0f}%）。"
                )
            elif item["state"] == "warn":
                alerts.append(
                    f"🟡 预算预警：「{item['name']}」已使用 {item['usage_ratio'] * 100:.0f}%，"
                    f"接近上限 {item['budget']:.2f} 元。"
                )

        # 3) 账户余额安全线
        accounts = await ledger_service.get_account_balances(session, user_id=user.id)
        for account in accounts:
            if account.get("balance", 0) < config.alert.low_balance_threshold:
                alerts.append(
                    f"⚠️ 账户「{account.get('name', '默认账户')}」余额 "
                    f"{account.get('balance', 0):.2f} 元，低于安全线 "
                    f"{config.alert.low_balance_threshold:.2f} 元。"
                )

        # 去重：同一类预警每天只发一次
        dedup_key = f"alert_sent_{today.isoformat()}"
        record = (
            await session.execute(select(SystemState).where(SystemState.key == dedup_key))
        ).scalars().first()

        already_sent = set()
        if record and isinstance(record.value, dict):
            already_sent = set(record.value.get("sent", []))

        new_alerts = [alert for alert in alerts if alert[:14] not in already_sent]

        if new_alerts:
            from ..render.bot import send_text

            text = "✨ 流萤预警\n" + "\n".join(new_alerts)
            sent = await send_text(text)

            if sent:
                state = record or SystemState(key=dedup_key, value={})
                state.value = {"sent": list(already_sent) + [alert[:14] for alert in new_alerts]}
                # 带时区：SQLModel 的 DateTime 拒绝 naive 值
                state.updated_at = datetime.now(timezone.utc)
                session.add(state)

            logger.info("发送预警 %d 条", len(new_alerts))

    return {"alert_count": len(alerts), "date": today.isoformat()}


# ------------------------------------------------------------------------------
# 注册与重排
# ------------------------------------------------------------------------------


def register_jobs() -> None:
    """把任务注册到调度器。幂等：重复调用会先清掉同名任务。"""
    scheduler = _get_scheduler()
    if scheduler is None:
        logger.warning("调度器不可用，定时任务未注册")
        return

    config = get_config()

    # 报告推送：按 config.push.schedule_times 里的每个时间点注册
    for index, time_str in enumerate(config.push.schedule_times or []):
        try:
            hour, minute = (int(part) for part in str(time_str).split(":")[:2])
        except (ValueError, TypeError):
            logger.warning("无效的推送时间配置: %s", time_str)
            continue

        scheduler.add_job(
            job_push_report,
            "cron",
            hour=hour,
            minute=minute,
            id=f"firefly_push_report_{index}",
            replace_existing=True,
        )

    # 订阅自动入账：每天跑一次（默认 08:00）
    try:
        sub_hour, sub_minute = (int(part) for part in str(config.subscription.run_time).split(":")[:2])
    except (ValueError, TypeError):
        sub_hour, sub_minute = 8, 0

    scheduler.add_job(
        job_subscriptions,
        "cron",
        hour=sub_hour,
        minute=sub_minute,
        id="firefly_subscriptions",
        replace_existing=True,
    )

    # 阈值预警：每天 21:00 盘点当日
    scheduler.add_job(
        job_alerts,
        "cron",
        hour=21,
        minute=0,
        id="firefly_alerts",
        replace_existing=True,
    )

    logger.info(
        "定时任务已注册：推送 %s / 订阅 %s / 预警 21:00",
        config.push.schedule_times,
        config.subscription.run_time,
    )


def reschedule() -> None:
    """配置变更后重新注册（前端「设置」页保存后调用）。"""
    scheduler = _get_scheduler()
    if scheduler is None:
        return

    for job_id in (
        ["firefly_subscriptions", "firefly_alerts"]
        + [f"firefly_push_report_{index}" for index in range(10)]
    ):
        try:
            scheduler.remove_job(job_id)
        except Exception:  # noqa: BLE001 - 任务原本就不存在
            pass

    register_jobs()


def get_jobs() -> List[Dict[str, Any]]:
    """列出当前已注册的定时任务（前端可展示下次执行时间）。"""
    scheduler = _get_scheduler()
    if scheduler is None:
        return []

    jobs = []
    for job in scheduler.get_jobs():
        if not job.id.startswith("firefly_"):
            continue
        jobs.append(
            {
                "id": job.id,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
        )
    return jobs


__all__ = ["register_jobs", "reschedule", "get_jobs", "job_push_report", "job_subscriptions", "job_alerts"]
