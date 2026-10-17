"""v1.0.2 regression coverage; isolated DBs, mocked LLM, no real QQ sends."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx
from sqlmodel import select

from firefly import config as settings
from firefly.api.router import api_router
from firefly.api.deps import get_or_create_user
from firefly.ai import assistant
from firefly.ai.read_tools import validate_plan
from firefly.db import session as db
from firefly.db.models import BotDraft
from firefly.services import bot_conversation as convo, ledger


class RegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="firefly-v102-")
        self.old = settings._CONFIG_CACHE
        settings._CONFIG_CACHE = settings.Config(database={"url": "sqlite+aiosqlite:///" + self.tmp.name + "/test.db"}, auth={"enabled": False}, llm={"enabled": False}, anomaly={"enabled": False})
        await db.dispose_db()
        await db.init_db()
        app = FastAPI()
        app.include_router(api_router, prefix="/api")
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", trust_env=False)
        self.categories = (await self.client.get("/api/categories")).json()["data"]
        self.cat = next(c for c in self.categories if c["direction"] == "expense")
        self.income = next(c for c in self.categories if c["direction"] == "income")
        self.account = (await self.client.get("/api/accounts")).json()["data"][0]["id"]
        async with db.session_scope() as session:
            self.user_id = (await get_or_create_user(session)).id
            other = await get_or_create_user(session, platform_id="another-user")
            self.other_id = other.id
            self.other_account = (await ledger.list_accounts(session, user_id=other.id))[0].id
        self.key = convo.conversation_key("bot", "private_sender", "sender")

    async def asyncTearDown(self):
        await self.client.aclose()
        await db.dispose_db()
        settings._CONFIG_CACHE = self.old
        self.tmp.cleanup()

    async def records(self, **query):
        return (await self.client.get("/api/transactions", params=query)).json()

    async def create(self, **patches):
        data = {"amount": 38, "category_id": self.cat["id"], "account_id": self.account, "memo": "回归测试"}
        data.update(patches)
        return (await self.client.post("/api/transactions", json=data)).json()

    async def send(self, text, key=None):
        async with db.session_scope() as session:
            return await convo.handle_message(session, user_id=self.user_id, key=key or self.key, text=text)

    async def test_natural_preview_maps_category_and_does_not_write(self):
        parsed = {"success": True, "engine": "rule", "items": [{"amount": 38, "direction": "expense", "category": self.cat["name"]}]}
        with patch("firefly.ai.natural.extract_transactions", new_callable=AsyncMock, return_value=parsed):
            result = (await self.client.post("/api/ai/natural", json={"text": "打车38"})).json()
        self.assertEqual(result["data"]["items"][0]["category_id"], self.cat["id"])
        self.assertEqual((await self.records())["total"], 0)

    async def test_nl_confirmation_preserves_selected_category_and_invoice(self):
        items = [{"amount": 12.34, "direction": "expense", "category_id": str(self.cat["id"]), "account_id": str(self.account), "has_invoice": True}, {"amount": 20, "direction": "income", "category": self.income["name"], "has_invoice": False}]
        result = (await self.client.post("/api/ai/nl-confirm", json={"items": items})).json()
        self.assertTrue(result["success"], result)
        rows = result["data"]["created"]
        self.assertEqual(rows[0]["category_id"], self.cat["id"])
        self.assertEqual(rows[0]["account_id"], self.account)
        self.assertTrue(rows[0]["has_invoice"])
        self.assertEqual(rows[1]["category_id"], self.income["id"])
        self.assertFalse(rows[1]["has_invoice"])

    async def test_nl_whole_batch_reference_validation(self):
        for invalid in [{"account_id": self.other_account}, {"category_id": self.income["id"]}, {"direction": "bad"}]:
            items = [{"amount": 1, "category_id": self.cat["id"]}, {"amount": 2, "category_id": self.cat["id"], **invalid}]
            result = (await self.client.post("/api/ai/nl-confirm", json={"items": items})).json()
            self.assertFalse(result["success"], result)
            self.assertEqual((await self.records())["total"], 0)

    async def test_crud_balance_nullable_references_and_audit_snapshots(self):
        result = await self.create(has_invoice=True)
        self.assertTrue(result["success"], result)
        txn_id = result["data"]["transaction"]["id"]
        result = (await self.client.patch(f"/api/transactions/{txn_id}", json={"amount": 50, "memo": "已修正", "has_invoice": False, "trade_time": "2026-10-17T14:30:00+08:00"})).json()
        self.assertTrue(result["success"], result)
        self.assertIn("06:30", result["data"]["trade_time"])
        accounts = (await self.client.get("/api/accounts")).json()["data"]
        self.assertEqual(next(a for a in accounts if a["id"] == self.account)["balance"], -50)
        result = (await self.client.patch(f"/api/transactions/{txn_id}", json={"category_id": None, "account_id": None})).json()
        self.assertIsNone(result["data"]["category_id"])
        self.assertIsNone(result["data"]["account_id"])
        self.assertTrue((await self.client.delete(f"/api/transactions/{txn_id}")).json()["success"])
        logs = (await self.client.get("/api/audit/logs", params={"target_type": "transaction", "target_id": txn_id})).json()
        self.assertEqual(logs["total"], 4)
        edit = next(l for l in logs["data"] if l["action"] == "update_transaction" and l["extra"]["before"]["amount"] == 38)
        self.assertEqual(edit["extra"]["after"]["amount"], 50)
        self.assertFalse(edit["extra"]["after"]["has_invoice"])
        self.assertEqual(next(l for l in logs["data"] if l["action"] == "delete_transaction")["extra"]["before"]["memo"], "已修正")

    async def test_crud_blocks_other_users_and_direction_mismatch(self):
        self.assertFalse((await self.create(account_id=self.other_account))["success"])
        row = (await self.create())["data"]["transaction"]
        for payload in [{"account_id": self.other_account}, {"direction": "income"}]:
            result = (await self.client.patch('/api/transactions/' + str(row["id"]), json=payload)).json()
            self.assertFalse(result["success"], result)
        async with db.session_scope() as session:
            other = await ledger.create_transaction(session, user_id=self.other_id, amount=999, memo="不能读取")
            other_id = other.id
        self.assertFalse((await self.client.get(f"/api/transactions/{other_id}")).json()["success"])
        self.assertFalse((await self.client.delete(f"/api/transactions/{other_id}")).json()["success"])
        self.assertEqual((await self.records())["total"], 1)

    async def test_list_filters_and_count_use_same_account_scope(self):
        for i in range(4):
            await self.create(amount=i + 1)
        await self.create(amount=9, account_id=None)
        result = await self.records(account_id=self.account, keyword="回归", limit=2, offset=2)
        self.assertEqual(result["total"], 4)
        self.assertEqual(len(result["data"]), 2)

    async def test_ai_receives_verified_owned_data_and_no_config(self):
        await self.create(amount=123.45)
        settings.get_config().llm.enabled = True
        settings.get_config().llm.api_key = "SECRET_DO_NOT_SEND"
        planner = AsyncMock(return_value={"intent": "financial_overview", "period": "monthly", "user_id": self.other_id})
        synthesis = AsyncMock(return_value="依据本期数据，注意控制开支。")
        with patch.object(assistant, "is_ready", return_value=True), patch.object(assistant, "chat_json", planner), patch.object(assistant, "chat", synthesis):
            result = (await self.client.post("/api/ai/chat", json={"question": "分析我的财务"})).json()["data"]
        self.assertEqual(result["data"]["summary"]["expense"], 123.45)
        self.assertEqual(result["analysis_engine"], "llm")
        payload = synthesis.call_args.args[0][1]["content"]
        self.assertIn("123.45", payload)
        self.assertNotIn("SECRET_DO_NOT_SEND", payload)
        self.assertNotIn("user_id", result["plan"])
        self.assertEqual((await self.records())["total"], 1)

    async def test_ai_read_tools_reject_write_intents_and_bound_arguments(self):
        plan = validate_plan({"intent": ["delete"], "period": {}, "custom_days": 10**30, "top_n": 999, "sql": "DELETE", "user_id": 9})
        self.assertEqual(plan["intent"], "unknown")
        self.assertEqual(plan["custom_days"], 366)
        self.assertEqual(plan["top_n"], 10)
        self.assertNotIn("sql", plan)
        settings.get_config().llm.enabled = True
        with patch.object(assistant, "is_ready", return_value=True), patch.object(assistant, "chat_json", new_callable=AsyncMock, return_value={"intent": "delete_transactions"}):
            result = (await self.client.post("/api/ai/chat", json={"question": "删除所有数据"})).json()
        self.assertEqual(result["data"]["intent"], "unknown")
        self.assertEqual((await self.records())["total"], 0)

    async def test_ai_income_category_and_calendar_bounds(self):
        await self.create(amount=90, direction="income", category_id=self.income["id"])
        async with db.session_scope() as session:
            result = await assistant._execute(session, user_id=self.user_id, plan={"intent": "sum_by_category", "category": self.income["name"], "period": "monthly", "direction": "income"}, question="本月收入")
        self.assertEqual(result["data"]["amount"], 90)
        self.assertIn("收入 90", result["answer"])
        class MarchFirst(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 3, 1, 12, tzinfo=tz)
        with patch.object(assistant, "datetime", MarchFirst):
            period, days, reference = assistant._resolve_period({"period": "monthly"}, "上个月")
        self.assertEqual(reference.month, 2)
        start, end = ledger.period_bounds(period, reference=reference)
        self.assertEqual(start.day, 1)
        self.assertEqual(end.month, 3)

    async def test_ai_failure_returns_real_calculated_answer(self):
        await self.create(amount=10)
        settings.get_config().llm.enabled = True
        with patch.object(assistant, "is_ready", return_value=True), patch.object(assistant, "chat_json", new_callable=AsyncMock, return_value={"intent": "sum_total"}), patch.object(assistant, "chat", new_callable=AsyncMock, side_effect=RuntimeError("offline")):
            result = (await self.client.post("/api/ai/chat", json={"question": "支出多少"})).json()["data"]
        self.assertEqual(result["data"]["expense"], 10)
        self.assertEqual(result["analysis_engine"], "rule")

    async def test_qq_first_round_is_offline_and_acceptance_posts_once(self):
        with patch.object(convo, "refine_transactions", new_callable=AsyncMock) as llm:
            reply = await self.send("今天打车 38 元 已开票")
            self.assertIn("离线规则", reply)
            self.assertEqual((await self.records())["total"], 0)
            llm.assert_not_called()
        self.assertIn("已按你接受", await self.send("接受"))
        rows = (await self.records())["data"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["has_invoice"])
        self.assertIsNotNone(rows[0]["category_id"])
        self.assertIn("没有待确认", await self.send("接受"))
        self.assertEqual((await self.records())["total"], 1)

    async def test_qq_rejections_loop_ai_preview_until_acceptance(self):
        await self.send("今天打车 38 元")
        ai = AsyncMock(side_effect=[[{"amount": 40, "direction": "expense", "memo": "第一次修改"}], [{"amount": 42, "direction": "expense", "memo": "第二次修改", "has_invoice": True}]])
        with patch.object(convo, "refine_transactions", ai):
            self.assertIn("AI 结构化", await self.send("不接受，实际40元"))
            self.assertEqual((await self.records())["total"], 0)
            self.assertIn("42.00", await self.send("不接受，42元已开票"))
            self.assertEqual((await self.records())["total"], 0)
            self.assertIn("已按你接受", await self.send("接受"))
        rows = (await self.records())["data"]
        self.assertEqual(rows[0]["amount"], 42)
        self.assertTrue(rows[0]["has_invoice"])
        self.assertEqual(ai.await_count, 2)

    async def test_qq_ai_error_does_not_reuse_rejected_items(self):
        await self.send("打车 38 元")
        with patch.object(convo, "refine_transactions", new_callable=AsyncMock, side_effect=ValueError("bad JSON")):
            self.assertIn("未成功", await self.send("不接受"))
        self.assertIn("没有可接受", await self.send("接受"))
        self.assertEqual((await self.records())["total"], 0)
        self.assertIn("已取消", await self.send("取消"))

    async def test_qq_conversation_isolation_cancel_expiry_and_numeric_query(self):
        await self.send("打车 38 元")
        self.assertIn("没有待确认", await self.send("接受", key="different-session"))
        self.assertIn("已取消", await self.send("取消"))
        await self.send("买书 20 元")
        async with db.session_scope() as session:
            draft = (await session.exec(select(BotDraft).where(BotDraft.session_key == self.key))).first()
            draft.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.add(draft)
        self.assertIn("已过期", await self.send("接受"))
        self.assertIsNone(await self.send("查询最近10笔流水"))
        self.assertFalse(convo.looks_like_record("今年2026年支出多少"))
        self.assertEqual((await self.records())["total"], 0)

    async def test_qq_duplicate_concurrent_acceptance(self):
        await self.send("打车 38 元")
        replies = await asyncio.gather(self.send("接受"), self.send("接受"))
        self.assertEqual((await self.records())["total"], 1)
        self.assertEqual(sum("已按你接受" in r for r in replies), 1)

    async def test_qq_failed_batch_rolls_back_acceptance_and_created_rows(self):
        await self.send("打车 38 元，吃饭 20 元")
        original = ledger.create_transaction
        calls = 0
        async def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated database failure")
            return await original(*args, **kwargs)
        with patch.object(ledger, "create_transaction", fail_second):
            with self.assertRaises(RuntimeError):
                await self.send("接受")
        self.assertEqual((await self.records())["total"], 0)
        self.assertIn("已按你接受", await self.send("接受"))
        self.assertEqual((await self.records())["total"], 2)

    async def test_ai_local_month_boundary_and_previous_calendar_comparison(self):
        await self.create(amount=10, trade_time="2026-02-28T23:30:00+08:00")
        await self.create(amount=20, trade_time="2026-03-01T00:30:00+08:00")
        class MarchFirst(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 3, 1, 12, tzinfo=tz)
        async with db.session_scope() as session:
            with patch.object(assistant, "datetime", MarchFirst):
                result = await assistant._execute(session, user_id=self.user_id, plan={"intent": "compare_period", "period": "monthly"}, question="这个月比上个月多花了吗")
        self.assertEqual(result["data"]["current"]["expense"], 20)
        self.assertEqual(result["data"]["previous"]["expense"], 10)

    async def test_qq_identity_mapping_only_private_owners_share_local(self):
        class Event:
            message_type = "private"
            def get_user_id(self):
                return "123"
        cfg = settings.get_config()
        cfg.onebot.superusers = ["123"]
        event = Event()
        self.assertEqual(convo.platform_id_for_event(event), "local")
        event.message_type = "group"; event.group_id = 999
        self.assertNotEqual(convo.platform_id_for_event(event), "local")
        cfg.onebot.superusers = []; cfg.push.target_id = "different"
        event.message_type = "private"
        self.assertNotEqual(convo.platform_id_for_event(event), "local")

    async def test_private_onebot_handler_queries_and_confirms_without_at(self):
        import nonebot
        try:
            nonebot.get_driver()
        except ValueError:
            nonebot.init(driver="~fastapi", _env_file=None)
        from nonebot.adapters.onebot.v11 import PrivateMessageEvent, Message
        from firefly.plugins import chat as plugin
        from types import SimpleNamespace
        settings.get_config().onebot.superusers = ["123"]
        def event(text):
            return PrivateMessageEvent.model_validate({"time": 1, "self_id": 456, "post_type": "message", "message_type": "private", "sub_type": "friend", "user_id": 123, "message_id": 7, "message": Message(text), "original_message": Message(text), "raw_message": text, "font": 0, "sender": {"user_id": 123, "nickname": "test"}, "to_me": False})
        fake_bot = SimpleNamespace(self_id="456")
        self.assertTrue(await plugin._addressed(event("查询最近10笔流水")))
        with patch.object(plugin.record, "finish", new_callable=AsyncMock) as finish, patch.object(assistant, "answer_question", new_callable=AsyncMock, return_value={"answer": "当前支出 0 元", "intent": "sum_total", "tools": ["sum_total"]}) as query:
            await plugin._handle_message(fake_bot, event("查询最近10笔流水"))
            self.assertEqual(query.call_args.kwargs["user_id"], self.user_id)
            self.assertIn("当前支出", str(finish.call_args.args[0]))
            self.assertEqual(finish.call_args.args[0][0].type, "text")
        with patch.object(plugin.record, "finish", new_callable=AsyncMock) as finish:
            await plugin._handle_message(fake_bot, event("打车38元"))
            self.assertIn("离线规则", str(finish.call_args.args[0]))
            self.assertEqual((await self.records())["total"], 0)
            await plugin._handle_message(fake_bot, event("接受"))
            self.assertEqual((await self.records())["total"], 1)
    async def test_qq_invalid_amounts_never_create_a_confirmable_draft(self):
        for text in ["打车 0 元", "打车 -3 元", "打车 100000001 元", "打车 1.234 元", "2026-99-99 打车 12 元"]:
            await self.send(text)
            self.assertIn("没有待确认", await self.send("接受"))
        self.assertEqual((await self.records())["total"], 0)


if __name__ == "__main__":
    unittest.main()
