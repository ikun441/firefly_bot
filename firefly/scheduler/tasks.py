"""定时任务：报告推送、订阅自动入账、阈值预警。

所有任务都用同一种模式：**先查配置是否启用，再执行，最后写审计日志**。
这样答辩时可以明确指出「哪些动作是系统自动做的」，而日志能为每条动作提供凭据。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List

from ..config import get_config

logger = logging.getLogger("firefly.scheduler")

_scheduler = None


def _get_scheduler():
    """惰性创建由应用显式启动和关闭的异步调度器。"""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        # Own the scheduler lifecycle explicitly; importing the NoneBot plugin
        # alone does not load its startup hooks or start its scheduler.
        _scheduler = AsyncIOScheduler(timezone=get_config().app.timezone)
        return _scheduler
    except ImportError:
        logger.warning("APScheduler 未安装，自动预警与定时推送不可用")
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


_alerts_lock = asyncio.Lock()


async def job_alerts() -> Dict[str, Any]:
    """Check every minute; persist delivery level only after a successful send.

    The local ledger is sent exclusively to its configured push destination.
    Each scope can warn once per local day, then escalate to over-budget once.
    """
    config = get_config()
    if not config.alert.enabled:
        return {"skipped": True, "reason": "alert.enabled = false"}
    if not config.push.enabled:
        return {"skipped": True, "reason": "push.enabled = false"}

    from ..api.deps import get_or_create_user
    from ..db.models import SystemState
    from ..db.session import session_scope
    from ..render.bot import resolve_target, send_text
    from ..services.alerts import collect_alerts
    from ..services.audit import log_action

    kind, target = resolve_target()
    if not target:
        return {"skipped": True, "reason": "未配置预警推送目标"}
    async with _alerts_lock:
        now = datetime.now(timezone.utc)
        today = now.astimezone(ZoneInfo(config.app.timezone)).date().isoformat()
        async with session_scope() as session:
            user = await get_or_create_user(session)
            alerts = await collect_alerts(session, user_id=user.id, now=now)
            pending = []
            for alert in alerts:
                digest = hashlib.sha256(f"{user.id}:{kind}:{target}:{alert.identity}".encode()).hexdigest()[:32]
                key = f"alert_v2_{today}_{digest}"
                record = await session.get(SystemState, key)
                try:
                    previous = int(json.loads(record.value).get("level", 0)) if record else 0
                except (TypeError, ValueError, AttributeError):
                    previous = 0
                if alert.level > previous:
                    pending.append((alert, key, record))

            # Batch below the protocol text limit and commit each successful batch.
            batches = []
            batch, length = [], 0
            for item in pending:
                if batch and length + len(item[0].text) > 3000:
                    batches.append(batch)
                    batch, length = [], 0
                batch.append(item)
                length += len(item[0].text) + 1
            if batch:
                batches.append(batch)
            sent_count = 0
            failed = False
            for batch in batches:
                text = "✨ 流萤预警\n" + "\n".join(item[0].text for item in batch)
                try:
                    sent = await send_text(text, target_type=kind, target_id=target)
                except Exception as exc:
                    logger.warning("预警发送失败：%s", type(exc).__name__)
                    sent = False
                if not sent:
                    failed = True
                    logger.warning("预警未投递，保留待下次检查重试")
                    break
                for alert, key, record in batch:
                    state = record or SystemState(key=key)
                    state.value = json.dumps({"level": alert.level})
                    state.updated_at = now
                    session.add(state)
                await log_action(session, "threshold_alert_sent", user_id=user.id,
                    operator="system", channel="bot", target_type="alert",
                    detail=f"阈值预警已投递 {len(batch)} 条", extra={"count": len(batch)})
                await session.commit()
                sent_count += len(batch)
            if sent_count:
                logger.info("预警已投递 %d 条", sent_count)
            return {"alert_count": len(alerts), "pending_count": len(pending),
                    "sent_count": sent_count, "success": not failed, "date": today}


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

    for job in scheduler.get_jobs():
        if job.id.startswith("firefly_"):
            scheduler.remove_job(job.id)

    # 报告推送：按 config.push.schedule_times 里的每个时间点注册
    for index, time_str in enumerate(config.push.schedule_times or []):
        try:
            hour, minute = (int(part) for part in str(time_str).split(":")[:2])
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("Invalid time")
        except (ValueError, TypeError):
            logger.warning("无效的推送时间配置: %s", time_str)
            continue

        scheduler.add_job(
            job_push_report,
            "cron",
            hour=hour,
            minute=minute,
            timezone=config.app.timezone,
            id=f"firefly_push_report_{index}",
            replace_existing=True,
        )

    # 订阅自动入账：每天跑一次（默认 08:00）
    try:
        sub_hour, sub_minute = (int(part) for part in str(config.subscription.run_time).split(":")[:2])
        if not (0 <= sub_hour <= 23 and 0 <= sub_minute <= 59):
            raise ValueError("Invalid time")
    except (ValueError, TypeError):
        sub_hour, sub_minute = 8, 0

    scheduler.add_job(
        job_subscriptions,
        "cron",
        hour=sub_hour,
        minute=sub_minute,
        timezone=config.app.timezone,
        id="firefly_subscriptions",
        replace_existing=True,
    )

    # Every minute covers all committed write channels; startup checks also catch
    # existing overspend and retry if the protocol adapter connects later.
    scheduler.add_job(
        job_alerts,
        "interval",
        seconds=60,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=1),
        id="firefly_alerts",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )

    if not scheduler.running:
        scheduler.start()
    logger.info(
        "定时任务已启动：推送 %s / 订阅 %s / 预警每 60 秒",
        config.push.schedule_times,
        config.subscription.run_time,
    )


def shutdown_scheduler() -> None:
    """Release owned background jobs before disposing the database."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def reschedule() -> None:
    """配置变更后重新注册（前端「设置」页保存后调用）。"""
    scheduler = _get_scheduler()
    if scheduler is None:
        return

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


__all__ = ["register_jobs", "shutdown_scheduler", "reschedule", "get_jobs", "job_push_report", "job_subscriptions", "job_alerts"]
