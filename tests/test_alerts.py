"""Real temporary DB and scheduler; only the protocol sender is mocked."""
import asyncio
from datetime import date, datetime, timedelta, timezone
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from sqlmodel import select

from firefly import config as settings
from firefly.api.deps import get_or_create_user
from firefly.db import session as db
from firefly.db.models import Budget, SystemState, Transaction, TransactionDirection
from firefly.render import bot
from firefly.scheduler import tasks
from firefly.services import ledger
from firefly.services.alerts import Alert, collect_alerts


class AlertTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="firefly-alerts-")
        self.old = settings._CONFIG_CACHE
        settings._CONFIG_CACHE = settings.Config(
            database={"url": "sqlite+aiosqlite:///" + self.tmp.name + "/test.db"},
            push={"enabled": True, "target_id": "1234", "target_type": "private"},
            alert={"enabled": True, "daily_expense_limit": 0, "low_balance_threshold": 0},
            llm={"enabled": False}, anomaly={"enabled": False},
        )
        await db.dispose_db()
        await db.init_db()
        self.now = datetime(2026, 10, 18, 4, tzinfo=timezone.utc)
        async with db.session_scope() as session:
            self.user = (await get_or_create_user(session)).id
            cats = await ledger.list_categories(session, user_id=self.user)
            self.cats = [c.id for c in cats if c.direction == TransactionDirection.EXPENSE][:2]
            self.other = (await get_or_create_user(session, platform_id="isolated-other")).id
        self.sender = patch.object(bot, "send_text", new_callable=AsyncMock, return_value=True)
        self.send = self.sender.start()
        self.clock = patch.object(tasks, "datetime", wraps=datetime)
        self.clock_mock = self.clock.start()
        self.clock_mock.now.side_effect = lambda tz=None: self.now.astimezone(tz) if tz else self.now

    async def asyncTearDown(self):
        self.clock.stop()
        self.sender.stop()
        await db.dispose_db()
        settings._CONFIG_CACHE = self.old
        self.tmp.cleanup()

    async def budget(self, *, category=None, amount=100, period="monthly", **fields):
        async with db.session_scope() as session:
            item = Budget(user_id=self.user, category_id=category, amount=amount,
                          period=period, start_date=date(2026, 10, 1), **fields)
            session.add(item)
            await session.flush()
            return item.id

    async def expense(self, amount, *, category=None, user=None, when=None):
        async with db.session_scope() as session:
            await ledger.create_transaction(session, user_id=user or self.user,
                amount=amount, category_id=category, trade_time=when or self.now)

    async def states(self):
        async with db.session_scope() as session:
            return list((await session.exec(select(SystemState))).all())

    async def test_category_warning_percentage_and_total_only_overspend(self):
        await self.budget(category=self.cats[0])
        await self.budget(amount=70)
        await self.expense(80, category=self.cats[0])
        result = await tasks.job_alerts()
        self.assertEqual(result["sent_count"], 2)
        text = self.send.call_args.args[0]
        self.assertIn("80.0%", text)
        self.assertNotIn("8000", text)
        self.assertIn("总预算", text)
        self.assertIn("预算超支", text)
        self.assertIn("超出 10.00 元", text)
        self.assertEqual(self.send.call_args.kwargs, {"target_type": "private", "target_id": "1234"})

    async def test_warning_can_escalate_and_restart_preserves_dedup(self):
        await self.budget()
        await self.expense(80)
        self.assertEqual((await tasks.job_alerts())["sent_count"], 1)
        await self.expense(30)
        self.assertEqual((await tasks.job_alerts())["sent_count"], 1)
        await db.dispose_db()  # Persistent marker survives an engine/process restart.
        self.assertEqual((await tasks.job_alerts())["sent_count"], 0)
        self.assertEqual(self.send.await_count, 2)
        states = await self.states()
        self.assertEqual(len(states), 1)
        self.assertIsInstance(states[0].value, str)
        self.assertEqual(json.loads(states[0].value)["level"], 2)

    async def test_second_category_is_not_suppressed_by_same_text_prefix(self):
        for cat in self.cats:
            await self.budget(category=cat)
        await self.expense(110, category=self.cats[0])
        self.assertEqual((await tasks.job_alerts())["sent_count"], 1)
        await self.expense(120, category=self.cats[1])
        self.assertEqual((await tasks.job_alerts())["sent_count"], 1)
        self.assertEqual(len(await self.states()), 2)

    async def test_failed_send_is_not_marked_and_retries(self):
        await self.budget()
        await self.expense(110)
        self.send.return_value = False
        self.assertFalse((await tasks.job_alerts())["success"])
        self.assertEqual(await self.states(), [])
        self.send.return_value = True
        self.assertEqual((await tasks.job_alerts())["sent_count"], 1)

    async def test_sender_exception_does_not_disable_future_checks(self):
        await self.budget()
        await self.expense(110)
        self.send.side_effect = RuntimeError("protocol unavailable")
        self.assertFalse((await tasks.job_alerts())["success"])
        self.assertEqual(await self.states(), [])
        self.send.side_effect = None
        self.assertEqual((await tasks.job_alerts())["sent_count"], 1)

    async def test_switches_and_missing_target(self):
        await self.budget()
        await self.expense(110)
        cfg = settings.get_config()
        for section in (cfg.alert, cfg.push):
            section.enabled = False
            self.assertTrue((await tasks.job_alerts())["skipped"])
            section.enabled = True
        cfg.push.target_id = ""
        cfg.onebot.superusers = []
        self.assertTrue((await tasks.job_alerts())["skipped"])
        cfg.push.target_id = "1234"
        cfg.budget.enabled = False
        self.assertEqual((await tasks.job_alerts())["alert_count"], 0)
        self.send.assert_not_awaited()

    async def test_disabled_future_expired_and_replaced_budgets(self):
        await self.budget(category=self.cats[0])
        async with db.session_scope() as session:
            session.add(Budget(user_id=self.user, category_id=self.cats[0], amount=100,
                               start_date=date(2026, 10, 2), alert_enabled=False))
            session.add(Budget(user_id=self.user, category_id=self.cats[1], amount=100,
                               start_date=date(2026, 11, 1)))
        await self.budget(end_date=date(2026, 10, 17))
        for cat in self.cats:
            await self.expense(110, category=cat)
        self.assertEqual((await tasks.job_alerts())["alert_count"], 0)
        self.send.assert_not_awaited()

    async def test_all_budget_periods_and_other_user_isolation(self):
        for period in ("daily", "weekly", "monthly", "quarterly", "yearly"):
            await self.budget(period=period)
        await self.expense(500, user=self.other)
        self.assertEqual((await tasks.job_alerts())["alert_count"], 0)
        await self.expense(110)
        self.assertEqual((await tasks.job_alerts())["sent_count"], 5)

    async def test_local_midnight_and_month_boundary_are_converted_to_utc(self):
        self.now = datetime(2026, 10, 31, 16, 30, tzinfo=timezone.utc)  # Nov 1 00:30 Shanghai.
        await self.budget()
        await self.expense(500, when=datetime(2026, 10, 31, 15, 59, tzinfo=timezone.utc))
        await self.expense(85, when=self.now)
        result = await tasks.job_alerts()
        self.assertEqual(result["date"], "2026-11-01")
        self.assertEqual(result["sent_count"], 1)
        self.assertIn("85.00 / 100.00", self.send.call_args.args[0])

    async def test_new_day_reminds_again_but_same_day_correction_does_not_downgrade(self):
        await self.budget()
        await self.expense(110)
        await tasks.job_alerts()
        async with db.session_scope() as session:
            item = (await session.exec(select(Transaction).where(Transaction.user_id == self.user))).first()
            item.amount = 90
            session.add(item)
        self.assertEqual((await tasks.job_alerts())["sent_count"], 0)
        self.now += timedelta(days=1)
        self.assertEqual((await tasks.job_alerts())["sent_count"], 1)
        self.assertEqual((await tasks.job_alerts())["sent_count"], 0)

    async def test_configured_destination_change_gets_its_own_delivery_state(self):
        await self.budget()
        await self.expense(110)
        await tasks.job_alerts()
        settings.get_config().push.target_id = "5678"
        self.assertEqual((await tasks.job_alerts())["sent_count"], 1)
        self.assertEqual(self.send.call_args.kwargs["target_id"], "5678")

    async def test_real_scheduler_automatically_delivers_budget_alert(self):
        from apscheduler.events import EVENT_JOB_EXECUTED
        self.now = datetime.now(timezone.utc)
        await self.budget()
        await self.expense(110)
        cfg = settings.get_config()
        cfg.push.schedule_times = []
        cfg.subscription.enabled = False
        finished = asyncio.Event()
        outcomes = []
        def complete(event):
            if event.job_id == "firefly_alerts":
                outcomes.append(event.retval)
                finished.set()
        with patch.object(tasks, "_scheduler", None):
            try:
                tasks.register_jobs()
                tasks._scheduler.add_listener(complete, EVENT_JOB_EXECUTED)
                await asyncio.wait_for(finished.wait(), timeout=4)
                self.assertEqual(outcomes[0]["sent_count"], 1)
                self.assertEqual(len(await self.states()), 1)
                self.send.assert_awaited_once()
            finally:
                tasks.shutdown_scheduler()
                await asyncio.sleep(0)

    async def test_concurrent_checks_send_once(self):
        await self.budget()
        await self.expense(110)
        # Hold the send briefly so another call enters while the first is active.
        async def send(*args, **kwargs):
            await asyncio.sleep(.01)
            return True
        self.send.side_effect = send
        with patch.object(tasks, "_alerts_lock", asyncio.Lock()):
            results = await asyncio.gather(tasks.job_alerts(), tasks.job_alerts())
        self.assertEqual(sum(r["sent_count"] for r in results), 1)
        self.send.assert_awaited_once()

    async def test_partial_batch_failure_marks_only_successful_delivery(self):
        candidates = [Alert(f"scope:{i}", 2, f"预警{i} " + "长" * 900) for i in range(5)]
        self.send.side_effect = [True, False]
        with patch("firefly.services.alerts.collect_alerts", AsyncMock(return_value=candidates)):
            result = await tasks.job_alerts()
            self.assertEqual(result["sent_count"], 3)
            self.assertFalse(result["success"])
            self.assertEqual(len(await self.states()), 3)
            self.send.side_effect = None
            self.assertEqual((await tasks.job_alerts())["sent_count"], 2)

    async def test_daily_expense_and_balance_alerts_remain_supported(self):
        cfg = settings.get_config()
        cfg.alert.daily_expense_limit = 50
        cfg.alert.low_balance_threshold = 1
        await self.expense(80)
        result = await tasks.job_alerts()
        self.assertGreaterEqual(result["sent_count"], 2)
        text = self.send.call_args.args[0]
        self.assertIn("今日支出", text)
        self.assertIn("余额", text)
        self.assertEqual((await tasks.job_alerts())["sent_count"], 0)


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_actually_runs_startup_check_and_can_reschedule(self):
        old = settings._CONFIG_CACHE
        settings._CONFIG_CACHE = settings.Config(
            push={"enabled": False, "schedule_times": ["25:99", "bad"]},
            subscription={"enabled": False, "run_time": "33:00"})
        fired = asyncio.Event()
        async def check():
            fired.set()
        try:
            with patch.object(tasks, "_scheduler", None), patch.object(tasks, "job_alerts", check):
                tasks.register_jobs()
                scheduler = tasks._scheduler
                self.assertTrue(scheduler.running)
                job = scheduler.get_job("firefly_alerts")
                self.assertEqual(job.trigger.interval.total_seconds(), 60)
                self.assertEqual(job.max_instances, 1)
                await asyncio.wait_for(fired.wait(), timeout=4)
                tasks.reschedule()
                self.assertEqual(len([j for j in scheduler.get_jobs() if j.id == "firefly_alerts"]), 1)
                self.assertEqual(str(scheduler.timezone), "Asia/Shanghai")
                tasks.shutdown_scheduler()
                await asyncio.sleep(0)
                self.assertFalse(scheduler.running)
        finally:
            settings._CONFIG_CACHE = old
