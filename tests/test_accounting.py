"""Accounting and authorization integration tests; temporary DB, no protocol traffic."""

import asyncio
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from firefly import config as settings
from firefly.db import session as db
from firefly.accounting import service as svc
from firefly.accounting.api import transaction
from firefly.accounting.domain import AccountingError, minor, money, balanced
from firefly.accounting.identity import Actor, acting_as, access, require
from firefly.accounting.models import QQGrant, AccountingEvent, Voucher, BusinessDraft
from firefly.services.auth import create_token


class MoneyTests(unittest.TestCase):
    def test_exact_and_invalid(self):
        self.assertEqual(minor("0.10") + minor("0.20"), 30)
        for value in ["NaN", "Infinity", "0", "-1", "1.001", "100000001", True, 0.1]:
            with self.assertRaises(AccountingError):
                minor(value)
        self.assertEqual(money(-10), "-0.10")

    def test_unbalanced(self):
        with self.assertRaises(AccountingError):
            balanced([])


class PeriodTests(unittest.TestCase):
    def test_calendar_boundaries(self):
        from firefly.accounting.periods import parse_range, parse_detail_args

        today = date(2026, 1, 1)
        cases = {
            "": ("2026-01-01", "2026-01-01"),
            "上月": ("2025-12-01", "2025-12-31"),
            "上周": ("2025-12-22", "2025-12-28"),
            "本周": ("2025-12-29", "2026-01-01"),
            "上季度": ("2025-10-01", "2025-12-31"),
            "去年": ("2025-01-01", "2025-12-31"),
            "2024-02": ("2024-02-01", "2024-02-29"),
            "2026": ("2026-01-01", "2026-12-31"),
            "2026-01-02": ("2026-01-02", "2026-01-02"),
        }
        for value, expected in cases.items():
            self.assertEqual(
                tuple(map(str, parse_range(value, today=today))), expected, value
            )
        self.assertEqual(
            parse_range("2026-01-01 至 2026-02-03"),
            (date(2026, 1, 1), date(2026, 2, 3)),
        )
        self.assertEqual(
            parse_detail_args("上月 第2页", today=today),
            (date(2025, 12, 1), date(2025, 12, 31), 2),
        )

    def test_invalid_ranges_and_pages(self):
        from firefly.accounting.periods import parse_range, parse_detail_args

        for value in [
            "2026-13",
            "2026-02-30",
            "2026-01-02 2026-01-01",
            "随便查",
            "0000",
        ]:
            with self.assertRaises(AccountingError):
                parse_range(value)
        with self.assertRaises(AccountingError):
            parse_detail_args("本月 第0页")


class AccountingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="firefly-accounting-")
        self.old = settings._CONFIG_CACHE
        settings._CONFIG_CACHE = settings.Config(
            database={"url": "sqlite+aiosqlite:///" + self.tmp.name + "/test.db"},
            auth={"secret_key": "isolated-only-test-secret"},
            llm={"enabled": False},
            push={"enabled": False},
        )
        await db.dispose_db()
        await db.init_db()
        from firefly.api.router import api_router
        from firefly.accounting.middleware import AccountingIdentityMiddleware

        self.app = FastAPI()
        self.app.include_router(api_router, prefix="/api")

        @self.app.exception_handler(AccountingError)
        async def err(request, exc):
            return JSONResponse(
                {"success": False, "code": exc.code, "message": str(exc)},
                status_code=exc.status,
            )

        self.app.add_middleware(AccountingIdentityMiddleware)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
        )
        self.client.headers["Authorization"] = "Bearer " + create_token(
            settings.get_config().auth.username
        )
        self.admin = acting_as(Actor("admin", "admin", "web"))
        self.admin.__enter__()
        async with transaction() as s:
            self.book = await svc.create_book(s, "测试账套", date(2026, 1, 1))
            self.chart = {a.code: a.id for a in await svc.accounts(s, self.book["id"])}
        self.bid = self.book["id"]
        self.base = f"/api/v2/books/{self.bid}"
        self.counter = 0

    async def asyncTearDown(self):
        self.admin.__exit__(None, None, None)
        await self.client.aclose()
        await db.dispose_db()
        settings._CONFIG_CACHE = self.old
        self.tmp.cleanup()

    def payload(self, lines, summary="测试", opening=False):
        return dict(
            accounting_date="2026-01-01",
            summary=summary,
            opening=opening,
            lines=[
                dict(account_id=self.chart[code], side=side, amount=amount, cashflow=cf)
                for code, side, amount, cf in lines
            ],
        )

    async def post(self, path, payload, key=None):
        self.counter += 1
        return await self.client.post(
            path,
            json=payload,
            headers={"Idempotency-Key": key or f"test:{self.counter}"},
        )

    async def voucher(self, lines, opening=False):
        r = await self.post(
            self.base + "/vouchers", self.payload(lines, opening=opening)
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]

    async def transition(self, v, action):
        r = await self.post(
            self.base + f"/vouchers/{v['id']}/{action}",
            {"expected_version": v["version"]},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]

    async def complete(self, v):
        for action in ["submit", "approve", "post"]:
            v = await self.transition(v, action)
        return v

    async def opening(self):
        return await self.complete(
            await self.voucher(
                [
                    ("1002", "debit", "100000", "financing"),
                    ("3001", "credit", "100000", ""),
                ],
                True,
            )
        )

    async def test_authentication_and_origin(self):
        self.client.headers.pop("Authorization")
        self.assertEqual(
            (await self.client.get("/api/v2/permissions")).status_code, 401
        )
        self.client.headers["Authorization"] = "Bearer " + create_token("someone-else")
        self.assertEqual(
            (await self.client.get("/api/v2/permissions")).status_code, 401
        )
        self.client.headers["Authorization"] = "Bearer " + create_token("admin")
        self.assertEqual(
            (
                await self.client.post(
                    "/api/v2/books",
                    headers={"Origin": "https://untrusted.test"},
                    json={},
                )
            ).status_code,
            403,
        )

    async def test_admin_binding_and_independent_qq_grants(self):
        control = (await self.client.get("/api/v2/permissions")).json()["data"]
        r = await self.client.put(
            "/api/v2/permissions/administrator",
            json={"admin_qq": "3497689533", "expected_version": control["version"]},
        )
        self.assertEqual(r.status_code, 200, r.text)
        with acting_as(Actor("qq", "3497689533", "qq")):
            async with transaction() as s:
                self.assertTrue((await access(s))["is_admin"])
        r = await self.client.put(
            "/api/v2/permissions/qq/12345678", json={"can_read": True}
        )
        self.assertEqual(r.status_code, 200, r.text)
        with acting_as(Actor("qq", "12345678", "qq", "98765", "45678")):
            async with transaction() as s:
                rights = await access(s)
                self.assertTrue(rights["permissions"]["read"])
                self.assertFalse(rights["permissions"]["create"])
                with self.assertRaises(AccountingError):
                    await require(s, "create")
                self.assertFalse(rights["is_admin"])
        self.assertEqual(
            (
                await self.client.put("/api/v2/permissions/qq/3497689533", json={})
            ).status_code,
            422,
        )
        await self.client.delete("/api/v2/permissions/qq/12345678")
        with acting_as(Actor("qq", "12345678", "qq")):
            async with transaction() as s:
                with self.assertRaises(AccountingError):
                    await require(s, "read")

    async def test_unknown_qq_denied_even_if_configured_superuser(self):
        settings.get_config().onebot.superusers = ["12345678"]
        settings.get_config().push.target_id = "12345678"
        with acting_as(Actor("qq", "12345678", "qq")):
            async with transaction() as s:
                self.assertFalse(any((await access(s))["permissions"].values()))

    async def test_drafts_never_affect_formal_balances(self):
        await self.voucher(
            [("1002", "debit", "99", "operating"), ("5001", "credit", "99", "")]
        )
        result = (await self.client.get(self.base + "/trial-balance")).json()["data"]
        self.assertEqual(result["debit"], "0.00")

    async def test_opening_and_accrual_reports(self):
        await self.opening()
        await self.complete(
            await self.voucher(
                [
                    ("1002", "debit", "50000", "financing"),
                    ("2001", "credit", "50000", ""),
                ]
            )
        )
        await self.complete(
            await self.voucher(
                [("5602", "debit", "200", ""), ("1002", "credit", "200", "operating")]
            )
        )
        await self.complete(
            await self.voucher(
                [("1122", "debit", "10000", ""), ("5001", "credit", "10000", "")]
            )
        )
        await self.complete(
            await self.voucher(
                [("1002", "debit", "6000", "operating"), ("1122", "credit", "6000", "")]
            )
        )
        result = (
            await self.client.get(
                self.base + "/reports",
                params={"start": "2026-01-01", "end": "2026-01-31"},
            )
        ).json()["data"]
        self.assertEqual(
            result["balance_sheet"],
            dict(
                assets="159800.00",
                liabilities="50000.00",
                equity="109800.00",
                balanced=True,
            ),
        )
        self.assertEqual(result["income_statement"]["profit"], "9800.00")
        self.assertEqual(result["cash_flow"]["items"]["operating"], "5800.00")
        self.assertEqual(result["cash_flow"]["items"]["financing"], "50000.00")

    async def test_post_requires_opening(self):
        v = await self.voucher(
            [("1002", "debit", "1", "operating"), ("5001", "credit", "1", "")]
        )
        v = await self.transition(await self.transition(v, "submit"), "approve")
        r = await self.post(
            self.base + f"/vouchers/{v['id']}/post", {"expected_version": v["version"]}
        )
        self.assertEqual(r.status_code, 409, r.text)

    async def test_immutable_in_database_and_api(self):
        v = await self.opening()
        async with transaction() as s:
            with self.assertRaises(IntegrityError):
                await s.execute(
                    text("UPDATE ff_voucher SET summary='tamper' WHERE id=:id"),
                    {"id": v["id"]},
                )
        async with transaction() as s:
            with self.assertRaises(IntegrityError):
                await s.execute(
                    text("DELETE FROM ff_voucher_line WHERE voucher_id=:id"),
                    {"id": v["id"]},
                )
        r = await self.client.patch(
            self.base + f"/vouchers/{v['id']}",
            json={
                k: val
                for k, val in {
                    **self.payload(
                        [("1002", "debit", "1", ""), ("3001", "credit", "1", "")]
                    ),
                    "expected_version": v["version"],
                }.items()
                if k != "opening"
            },
        )
        self.assertEqual(r.status_code, 409)
        async with transaction() as s:
            with self.assertRaises(IntegrityError):
                await s.execute(text("DELETE FROM ff_accounting_event"))

    async def test_idempotency_concurrent_once(self):
        payload = self.payload(
            [("1002", "debit", "1", "operating"), ("5001", "credit", "1", "")]
        )
        a, b = await asyncio.gather(
            self.post(self.base + "/vouchers", payload, "same-key"),
            self.post(self.base + "/vouchers", payload, "same-key"),
        )
        self.assertEqual(a.status_code, 200, a.text)
        self.assertEqual(b.status_code, 200, b.text)
        self.assertEqual(a.json()["data"]["id"], b.json()["data"]["id"])
        changed = {**payload, "summary": "changed"}
        self.assertEqual(
            (await self.post(self.base + "/vouchers", changed, "same-key")).status_code,
            409,
        )

    async def test_live_permission_on_receipt_replay(self):
        async with transaction() as s:
            s.add(QQGrant(qq="12345678", can_create=True))
        with acting_as(Actor("qq", "12345678", "qq")):
            async with transaction() as s:
                await svc.business_draft(s, self.bid, "one", {"amount": "1"})
        async with transaction() as s:
            g = (await s.exec(select(QQGrant))).first()
            g.can_create = False
            s.add(g)
        with acting_as(Actor("qq", "12345678", "qq")):
            async with transaction() as s:
                with self.assertRaises(AccountingError):
                    await svc.business_draft(s, self.bid, "one", {"amount": "1"})

    async def test_qq_actor_and_web_audit(self):
        async with transaction() as s:
            s.add(QQGrant(qq="12345678", can_create=True))
        with acting_as(Actor("qq", "12345678", "qq", "998877", "445566")):
            async with transaction() as s:
                await svc.business_draft(s, self.bid, "qq-source", {"amount": "1"})
        async with transaction() as s:
            entries = (
                await s.exec(
                    select(AccountingEvent).where(
                        AccountingEvent.action == "create_business_draft"
                    )
                )
            ).all()
            self.assertEqual(entries[0].actor["identifier"], "12345678")
            self.assertEqual(entries[0].actor["platform"], "qq")
            self.assertEqual(entries[0].actor["group_id"], "998877")

    async def test_reversal_corrects_without_modifying_original(self):
        await self.opening()
        original = await self.complete(
            await self.voucher(
                [("5602", "debit", "200", ""), ("1002", "credit", "200", "operating")]
            )
        )
        r = await self.post(
            self.base + f"/vouchers/{original['id']}/reversal-drafts",
            {"accounting_date": "2026-01-02", "reason": "录入错误"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        reverse = await self.complete(r.json()["data"])
        self.assertEqual(reverse["lines"][0]["effect_sign"], -1)
        original_again = (
            await self.client.get(self.base + f"/vouchers/{original['id']}")
        ).json()["data"]
        self.assertEqual(original_again["state"], "posted")
        result = (
            await self.client.get(
                self.base + "/reports",
                params={"start": "2026-01-01", "end": "2026-01-31"},
            )
        ).json()["data"]
        self.assertEqual(result["income_statement"]["expense"], "0.00")
        self.assertEqual(
            (
                await self.post(
                    self.base + f"/vouchers/{original['id']}/reversal-drafts",
                    {"accounting_date": "2026-01-02", "reason": "again"},
                )
            ).status_code,
            409,
        )

    async def test_version_unbalanced_and_cross_book_rejected(self):
        v = await self.voucher(
            [("1002", "debit", "1", "operating"), ("5001", "credit", "1", "")]
        )
        r = await self.post(
            self.base + f"/vouchers/{v['id']}/submit", {"expected_version": 999}
        )
        self.assertEqual(r.status_code, 409)
        bad = self.payload([("1002", "debit", "1", ""), ("5001", "credit", "2", "")])
        self.assertEqual(
            (await self.post(self.base + "/vouchers", bad)).status_code, 422
        )
        self.assertEqual(
            (
                await self.client.get(f"/api/v2/books/999/vouchers/{v['id']}")
            ).status_code,
            404,
        )

    async def test_internal_misclassification_not_complete_cashflow(self):
        await self.opening()
        await self.complete(
            await self.voucher(
                [("1002", "debit", "20", "internal"), ("5001", "credit", "20", "")]
            )
        )
        r = (
            await self.client.get(
                self.base + "/reports",
                params={"start": "2026-01-01", "end": "2026-01-31"},
            )
        ).json()["data"]
        self.assertFalse(r["cash_flow"]["complete"])

    async def test_legacy_write_blocked_after_book_and_subscription_paused(self):
        r = await self.client.post("/api/transactions", json={"amount": 1})
        self.assertEqual(r.status_code, 409)
        r = await self.client.post("/api/ai/nl-confirm", json={"items": []})
        self.assertEqual(r.status_code, 409)
        from firefly.services.subscription import run_due_subscriptions

        async with db.session_scope() as s:
            r = await run_due_subscriptions(s)
            self.assertEqual(r["processed"], 0)

    async def test_help_filters_permissions(self):
        from firefly.accounting.help import help_markdown

        async with transaction() as s:
            s.add(QQGrant(qq="12345678", can_read=True))
        with acting_as(Actor("qq", "12345678", "qq")):
            async with transaction() as s:
                doc = await help_markdown(s)
        self.assertIn("/报告", doc)
        self.assertNotIn("/记 用途", doc)
        self.assertNotIn("/过账", doc)
        self.assertIn("ai 问题", doc)

    async def test_help_renders_image_and_does_not_double_finish(self):
        import nonebot

        try:
            nonebot.get_driver()
        except ValueError:
            nonebot.init(driver="~fastapi", _env_file=None)
        from firefly.plugins import chat
        from firefly.render import ai_reply
        from nonebot.adapters.onebot.v11 import PrivateMessageEvent, Message

        event = PrivateMessageEvent.model_validate(
            dict(
                time=1,
                self_id=456,
                post_type="message",
                message_type="private",
                sub_type="friend",
                user_id=12345678,
                message_id=7,
                message=Message("/帮助"),
                original_message=Message("/帮助"),
                raw_message="/帮助",
                font=0,
                sender={"user_id": 12345678},
            )
        )
        with (
            patch.object(chat.cmd_help, "send", new_callable=AsyncMock) as send,
            patch.object(chat.cmd_help, "finish", new_callable=AsyncMock) as finish,
            patch.object(
                ai_reply,
                "render_ai_reply",
                new_callable=AsyncMock,
                return_value=[b"png"],
            ),
        ):
            handler = list(chat.cmd_help.handlers)[0].call
            await handler(NS(self_id="456"), event)
        self.assertEqual(send.call_args.args[0][0].type, "image")
        self.assertTrue(send.call_args.args[0][0].data["file"].startswith("base64://"))
        finish.assert_awaited_once_with()

    async def test_unauthorized_qq_cannot_parse_or_confirm(self):
        import nonebot

        try:
            nonebot.get_driver()
        except ValueError:
            nonebot.init(driver="~fastapi", _env_file=None)
        from firefly.plugins.chat import dispatch

        e = NS(
            get_user_id=lambda: "12345678",
            get_session_id=lambda: "group_1_12345678",
            group_id=1,
        )
        for value in ["办公费 20", "接受"]:
            with self.assertRaises(AccountingError):
                await dispatch(NS(self_id="456"), e, "记", value)

    async def test_month_close_and_posting_closed_period(self):
        await self.opening()
        await self.complete(
            await self.voucher(
                [("5602", "debit", "200", ""), ("1002", "credit", "200", "operating")]
            )
        )
        r = await self.post(self.base + "/periods/2026-01/closing-drafts", {})
        self.assertEqual(r.status_code, 200, r.text)
        await self.complete(r.json()["data"])
        for code, balance in [("1001", "0"), ("1002", "99800")]:
            r = await self.post(
                self.base + "/reconciliations",
                {
                    "account_id": self.chart[code],
                    "as_of": "2026-01-31",
                    "statement_balance": balance,
                    "reason": "测试银行对账单核对",
                    "outstanding": [],
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
        pre = (await self.post(self.base + "/periods/2026-01/pre-close", {})).json()[
            "data"
        ]
        self.assertTrue(pre["can_close"], pre)
        r = await self.post(self.base + "/periods/2026-01/close", {})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(
            r.json()["data"]["snapshot"]["income_statement"]["expense"], "200.00"
        )
        r = await self.post(
            self.base + "/vouchers",
            self.payload(
                [("1002", "debit", "1", "operating"), ("5001", "credit", "1", "")]
            ),
        )
        self.assertEqual(r.status_code, 409)

    async def test_close_blocks_unposted_and_unreconciled(self):
        await self.opening()
        await self.voucher([("1002", "debit", "1", ""), ("5001", "credit", "1", "")])
        r = await self.post(self.base + "/periods/2026-01/close", {})
        self.assertEqual(r.status_code, 409)
        self.assertIn("未过账", r.json()["message"])

    async def test_bank_difference_not_fudged(self):
        await self.opening()
        r = await self.post(
            self.base + "/reconciliations",
            {
                "account_id": self.chart["1002"],
                "as_of": "2026-01-31",
                "statement_balance": "1",
                "reason": "差异",
                "outstanding": [],
            },
        )
        self.assertEqual(r.status_code, 422)

    async def test_ai_readonly_function_call_uses_accounting_book(self):
        from firefly.accounting import ai

        await self.opening()
        call = NS(
            id="a",
            function=NS(
                name="read_financial_reports",
                arguments=json.dumps({"start": "2026-01-01", "end": "2026-01-31"}),
            ),
        )
        fake = NS(
            chat=NS(
                completions=NS(
                    create=AsyncMock(
                        side_effect=[
                            NS(
                                choices=[
                                    NS(message=NS(content=None, tool_calls=[call]))
                                ]
                            ),
                            NS(
                                choices=[
                                    NS(
                                        message=NS(
                                            content="期末权益为100000元，来自投资。",
                                            tool_calls=[],
                                        )
                                    )
                                ]
                            ),
                        ]
                    )
                )
            ),
            close=AsyncMock(),
        )
        with (
            patch.object(ai, "is_ready", return_value=True),
            patch.object(ai, "get_client", return_value=fake),
        ):
            result = await ai.query(self.bid, "ai 资产与利润有什么区别")
        self.assertEqual(result["engine"], "llm")
        self.assertTrue(result["read_only"])
        self.assertEqual(
            result["data"]["results"][0]["data"]["balance_sheet"]["equity"], "100000.00"
        )
        fake.close.assert_awaited_once()

    async def test_ai_tool_rejects_write_and_supplied_book(self):
        from firefly.accounting.ai import execute

        for name, args in [
            ("create_voucher", {}),
            ("read_financial_reports", {"book_id": 999}),
            ("read_financial_reports", {"sql": "DELETE FROM ff_voucher"}),
        ]:
            with self.assertRaises(AccountingError):
                await execute(self.bid, name, args, "测试")

    async def test_ai_read_permission_and_web_bridge(self):
        from firefly.accounting.ai import query

        with acting_as(Actor("qq", "12345678", "qq")):
            with self.assertRaises(AccountingError):
                await query(self.bid, "本月利润")
        r = await self.client.post("/api/ai/chat", json={"question": "ai 本月利润"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["data"]["read_only"])
        self.assertIn("已过账", r.json()["data"]["answer"])

    async def test_qq_confirmation_is_idempotent_and_never_posts(self):
        import nonebot

        try:
            nonebot.get_driver()
        except ValueError:
            nonebot.init(driver="~fastapi", _env_file=None)
        from firefly.plugins.chat import dispatch

        async with transaction() as s:
            s.add(QQGrant(qq="12345678", can_create=True))
        e = NS(
            get_user_id=lambda: "12345678",
            get_session_id=lambda: "group_1_12345678",
            group_id=1,
        )
        bot = NS(self_id="456")
        result = await dispatch(bot, e, "记", "办公费20元")
        self.assertIn("待确认", result)
        result = await dispatch(bot, e, "记", "接受")
        self.assertIn("尚未过账", result)
        await dispatch(bot, e, "记", "接受")
        async with transaction() as s:
            rows = (await s.exec(select(BusinessDraft))).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].actor["identifier"], "12345678")
            self.assertEqual(len((await s.exec(select(Voucher))).all()), 0)

    async def test_file_import_goes_only_to_business_drafts(self):
        content = "交易时间,收支,金额,备注\n2026-01-01,支出,20,办公\n".encode()
        r = await self.client.post(
            self.base + "/imports", files={"file": ("test.csv", content, "text/csv")}
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertGreater(r.json()["data"]["count"], 0)
        async with transaction() as s:
            self.assertEqual(len((await s.exec(select(Voucher))).all()), 0)

    async def test_reconciliation_invalid_items_are_rejected(self):
        await self.opening()
        base = {
            "account_id": self.chart["1002"],
            "as_of": "2026-01-31",
            "statement_balance": "100000",
            "reason": "核对",
        }
        for item in [
            {"side": "bank", "reason": "missing"},
            {"side": "bank", "amount": 0.1, "reason": "float"},
            {"side": "bank", "amount": "0", "reason": 123},
            {"side": "bank", "amount": "NaN", "reason": "bad"},
        ]:
            r = await self.post(
                self.base + "/reconciliations", {**base, "outstanding": [item]}
            )
            self.assertEqual(r.status_code, 422, r.text)

    async def test_new_zero_net_movements_invalidate_reconciliation(self):
        await self.opening()
        for code, balance in [("1001", "0"), ("1002", "100000")]:
            r = await self.post(
                self.base + "/reconciliations",
                {
                    "account_id": self.chart[code],
                    "as_of": "2026-01-31",
                    "statement_balance": balance,
                    "reason": "月末核对",
                    "outstanding": [],
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
        a = await self.complete(
            await self.voucher(
                [("1002", "debit", "10", "operating"), ("1122", "credit", "10", "")]
            )
        )
        r = await self.post(
            self.base + f"/vouchers/{a['id']}/reversal-drafts",
            {"accounting_date": "2026-01-01", "reason": "冲回"},
        )
        await self.complete(r.json()["data"])
        pre = (await self.post(self.base + "/periods/2026-01/pre-close", {})).json()[
            "data"
        ]
        self.assertIn("资金科目需重新对账：1002 银行存款", pre["issues"])

    async def test_closing_duplicate_and_stale_posting_refused(self):
        await self.opening()
        await self.complete(
            await self.voucher(
                [("5602", "debit", "20", ""), ("1002", "credit", "20", "operating")]
            )
        )
        r = await self.post(self.base + "/periods/2026-01/closing-drafts", {})
        closing = r.json()["data"]
        duplicate = await self.post(self.base + "/periods/2026-01/closing-drafts", {})
        self.assertEqual(duplicate.status_code, 409)
        patch_data = {k: closing[k] for k in ["accounting_date", "summary"]}
        patch_data.update(
            expected_version=closing["version"],
            lines=[
                {
                    k: x[k]
                    for k in ["account_id", "side", "amount", "auxiliary", "cashflow"]
                }
                for x in closing["lines"]
            ],
        )
        r = await self.client.patch(
            self.base + f"/vouchers/{closing['id']}", json=patch_data
        )
        self.assertEqual(r.status_code, 409)
        closing = await self.transition(
            await self.transition(closing, "submit"), "approve"
        )
        await self.complete(
            await self.voucher(
                [("5602", "debit", "1", ""), ("1002", "credit", "1", "operating")]
            )
        )
        r = await self.post(
            self.base + f"/vouchers/{closing['id']}/post",
            {"expected_version": closing["version"]},
        )
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "CLOSING_STALE")

    async def test_full_backup_roundtrip_into_new_database(self):
        from firefly.accounting.recovery import restore_to_new_database
        from firefly.services.backup import validate_backup
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlmodel.ext.asyncio.session import AsyncSession

        await self.opening()
        await self.complete(
            await self.voucher(
                [("5602", "debit", "20", ""), ("1002", "credit", "20", "operating")]
            )
        )
        await self.client.put(
            "/api/v2/permissions/qq/12345678", json={"can_read": True}
        )
        payload = (await self.client.get("/api/v2/backup")).json()["data"]
        self.assertEqual(payload["version"], 3)
        self.assertTrue(validate_backup(payload)[0])
        self.assertEqual(payload["counts"]["vouchers"], 2)
        target = Path(self.tmp.name) / "recovered.db"
        result = await restore_to_new_database(payload, target)
        self.assertEqual(result["integrity"], "ok")
        self.assertEqual(result["counts"], payload["counts"])
        engine = create_async_engine("sqlite+aiosqlite:///" + str(target))
        async with AsyncSession(engine) as session:
            grant = (await session.exec(select(QQGrant))).one()
            self.assertTrue(grant.can_read)
            trial = await svc.trial_balance(session, self.bid)
            self.assertEqual(
                next(x["balance"] for x in trial["rows"] if x["code"] == "1002"),
                "99980.00",
            )
            with self.assertRaises(IntegrityError):
                await session.execute(
                    text("UPDATE ff_voucher SET summary='invalid' WHERE state='posted'")
                )
            await session.rollback()
        await engine.dispose()
        with self.assertRaises(ValueError):
            await restore_to_new_database(payload, target)

    async def test_corrupt_backup_refused_without_creating_database(self):
        from firefly.services.backup import tables_digest, validate_backup
        from firefly.accounting.recovery import restore_to_new_database

        await self.opening()
        payload = (await self.client.get("/api/v2/backup")).json()["data"]
        payload["tables"]["voucher_lines"][0]["amount_minor"] += 1
        self.assertFalse(validate_backup(payload)[0])
        target = Path(self.tmp.name) / "bad.db"
        with self.assertRaises(ValueError):
            await restore_to_new_database(payload, target)
        self.assertFalse(target.exists())
        payload["sha256"] = tables_digest(payload["tables"])
        with self.assertRaises(AccountingError):
            await restore_to_new_database(payload, target)
        self.assertFalse(target.exists())

    async def test_service_cannot_restore_legacy_over_accounting(self):
        from firefly.services.backup import restore_backup, LEGACY_BACKUP_TABLES

        payload = {
            "format": "firefly-backup",
            "version": 1,
            "tables": {n: [] for n, m in LEGACY_BACKUP_TABLES},
        }
        async with transaction() as s:
            with self.assertRaises(ValueError):
                await restore_backup(s, payload, make_safety_backup=False)
        r = await self.client.post(
            "/api/backup/restore", json={"filename": "any.json", "confirm": "RESTORE"}
        )
        self.assertEqual(r.status_code, 409)
        r = await self.client.post(
            "/api/ai/natural", json={"text": "办公20", "auto_create": True}
        )
        self.assertEqual(r.status_code, 409)

    async def test_database_guards_cross_book_and_reconciliation(self):
        from firefly.accounting.models import Book, ChartAccount

        v = await self.voucher(
            [("1002", "debit", "1", ""), ("5001", "credit", "1", "")]
        )
        async with transaction() as s:
            other = Book(name="隔离测试", starts_on=date(2026, 1, 1))
            s.add(other)
            await s.flush()
            a = ChartAccount(
                book_id=other.id,
                code="1002",
                name="其他账套",
                kind="asset",
                normal_side="debit",
            )
            s.add(a)
            await s.flush()
            aid = a.id
        with self.assertRaises(IntegrityError):
            async with transaction() as s:
                await s.execute(
                    text(
                        "UPDATE ff_voucher_line SET account_id=:aid WHERE voucher_id=:vid"
                    ),
                    {"aid": aid, "vid": v["id"]},
                )
        r = await self.post(
            self.base + "/reconciliations",
            {
                "account_id": self.chart["1001"],
                "as_of": "2026-01-31",
                "statement_balance": "0",
                "reason": "盘点",
                "outstanding": [],
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        with self.assertRaises(IntegrityError):
            async with transaction() as s:
                await s.execute(text("DELETE FROM ff_reconciliation"))

    async def test_qq_period_queries_and_pagination(self):
        import nonebot

        try:
            nonebot.get_driver()
        except ValueError:
            nonebot.init(driver="~fastapi", _env_file=None)
        from firefly.plugins.chat import dispatch

        async with transaction() as s:
            s.add(QQGrant(qq="12345678", can_read=True))
        e = NS(
            get_user_id=lambda: "12345678",
            get_session_id=lambda: "group_1_12345678",
            group_id=1,
        )
        bot = NS(self_id="456")
        jan = await self.voucher(
            [("1002", "debit", "1", ""), ("5001", "credit", "1", "")]
        )
        payload = self.payload(
            [("1002", "debit", "2", ""), ("5001", "credit", "2", "")],
            summary="二月专属",
        )
        payload["accounting_date"] = "2026-02-01"
        self.assertEqual(
            (await self.post(self.base + "/vouchers", payload)).status_code, 200
        )
        result = await dispatch(bot, e, "明细", "2026-01")
        self.assertIn("2026-01-01 至 2026-01-31", result)
        self.assertIn("共 1 张", result)
        self.assertNotIn("二月专属", result)
        self.assertIn("本页没有凭证", await dispatch(bot, e, "明细", "2026-01 第2页"))
        report = await dispatch(bot, e, "报告", "2026-02-01 2026-02-28")
        self.assertIn("2026-02-01 至 2026-02-28", report)
        self.assertIn("仅已过账", report)
        self.assertIn("资产负债与权益", report)
        self.assertIn("利润：0.00 元", report)
        self.assertNotIn('"balance_sheet"', report)
        self.assertNotIn("True", report)
        self.assertIn("尚未设置费用类别", await dispatch(bot, e, "预算", "2026-01"))
        response = await self.client.get(
            self.base + "/vouchers", params={"start": "2026-01-01", "end": "2026-01-31"}
        )
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["data"][0]["id"], jan["id"])

    async def test_legacy_scheduled_push_and_alerts_pause_for_new_book(self):
        from firefly.scheduler.tasks import job_push_report, job_alerts

        settings.get_config().push.enabled = True
        settings.get_config().push.target_id = "12345678"
        settings.get_config().alert.enabled = True
        from firefly.render import bot

        with patch.object(bot, "send_text", new_callable=AsyncMock) as send:
            self.assertTrue((await job_push_report())["skipped"])
            self.assertTrue((await job_alerts())["skipped"])
        send.assert_not_awaited()

    async def test_create_disabled_subject_from_settings_form(self):
        response = await self.post(self.base + "/chart-accounts", {
            "code": "560201", "name": "办公用品", "kind": "expense", "normal_side": "debit",
            "cash": False, "parent_id": self.chart["5602"], "enabled": False,
            "quantity_enabled": True, "unit": "件",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["data"]["enabled"])
        self.assertTrue(response.json()["data"]["quantity_enabled"])

    async def test_bad_qq_returns_validation_error(self):
        r = await self.client.put(
            "/api/v2/permissions/qq/not-a-qq", json={"can_read": True}
        )
        self.assertEqual(r.status_code, 422, r.text)

    async def test_csv_keeps_internal_transfer_and_exact_source_strings(self):
        content = "交易时间,收支,金额,备注\n2026-01-01,不计收支,0.10,内部转账\n2026-01-02,支出,0.20,办公\n".encode()
        r = await self.client.post(
            self.base + "/imports", files={"file": ("facts.csv", content, "text/csv")}
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["count"], 2)
        async with transaction() as s:
            drafts = (
                await s.exec(select(BusinessDraft).order_by(BusinessDraft.id))
            ).all()
            self.assertEqual(drafts[0].facts["item"]["fields"]["收支"], "不计收支")
            self.assertEqual(drafts[0].facts["item"]["fields"]["金额"], "0.10")
            self.assertEqual(len((await s.exec(select(Voucher))).all()), 0)

    async def test_invalid_import_does_not_partially_save_rows(self):
        for filename, content in [
            (
                "bad.csv",
                "交易时间,收支,金额\n2026-01-01,支出,20\n2026-01-02,支出,1,200\n".encode(),
            ),
            ("bad.xlsx", b"not a workbook"),
        ]:
            r = await self.client.post(
                self.base + "/imports",
                files={"file": (filename, content, "application/octet-stream")},
            )
            self.assertEqual(r.status_code, 422, r.text)
        async with transaction() as s:
            self.assertEqual(len((await s.exec(select(BusinessDraft))).all()), 0)

    async def test_missing_book_trial_returns_not_found(self):
        r = await self.client.get("/api/v2/books/999999/trial-balance")
        self.assertEqual(r.status_code, 404, r.text)

    async def category(self, name="办公费用"):
        r = await self.client.post(
            self.base + "/expense-categories", json={"name": name}
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]

    async def child_account(self, quantity=False):
        r = await self.client.post(
            self.base + "/chart-accounts",
            json={
                "code": "140501",
                "name": "库存商品明细",
                "kind": "asset",
                "normal_side": "debit",
                "cash": False,
                "parent_id": self.chart["1405"],
                "quantity_enabled": quantity,
                "unit": "件",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]

    async def test_expense_category_budget_counts_posted_and_reversal(self):
        cat = await self.category()
        r = await self.client.put(
            self.base + "/expense-budgets",
            json={"category_id": cat["id"], "month": "2026-01", "amount": "100"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        await self.opening()
        data = self.payload(
            [("5602", "debit", "20", ""), ("1002", "credit", "20", "operating")]
        )
        missing = await self.post(self.base + "/vouchers", data)
        self.assertEqual(missing.json()["code"], "BUDGET_CATEGORY_REQUIRED")
        data["lines"][0]["budget_category_id"] = cat["id"]
        v = (await self.post(self.base + "/vouchers", data)).json()["data"]
        overview = (
            await self.client.get(self.base + "/expense-budgets?month=2026-01")
        ).json()["data"]
        self.assertEqual(overview["used"], "0.00")
        v = await self.complete(v)
        overview = (
            await self.client.get(self.base + "/expense-budgets?month=2026-01")
        ).json()["data"]
        self.assertEqual(overview["categories"][0]["used"], "20.00")
        self.assertEqual(overview["categories"][0]["remaining"], "80.00")
        r = await self.post(
            self.base + f"/vouchers/{v['id']}/reversal-drafts",
            {"accounting_date": "2026-01-02", "reason": "退回"},
        )
        await self.complete(r.json()["data"])
        overview = (
            await self.client.get(self.base + "/expense-budgets?month=2026-01")
        ).json()["data"]
        self.assertEqual(overview["used"], "0.00")

    async def test_category_cross_book_and_non_expense_refused(self):
        cat = await self.category()
        other = (
            await self.post(
                "/api/v2/books", {"name": "独立账套", "starts_on": "2026-01-01"}
            )
        ).json()["data"]
        chart = (
            await self.client.get(f"/api/v2/books/{other['id']}/chart-accounts")
        ).json()["data"]
        codes = {a["code"]: a["id"] for a in chart}
        payload = {
            "accounting_date": "2026-01-01",
            "summary": "不得跨账套",
            "lines": [
                {
                    "account_id": codes["5602"],
                    "side": "debit",
                    "amount": "1",
                    "budget_category_id": cat["id"],
                },
                {"account_id": codes["1002"], "side": "credit", "amount": "1"},
            ],
        }
        r = await self.post(f"/api/v2/books/{other['id']}/vouchers", payload)
        self.assertEqual(r.json()["code"], "BUDGET_CATEGORY_INVALID")
        data = self.payload([("1002", "debit", "1", ""), ("3001", "credit", "1", "")])
        data["lines"][0]["budget_category_id"] = cat["id"]
        self.assertEqual(
            (await self.post(self.base + "/vouchers", data)).status_code, 422
        )

    async def test_quantity_price_exact_amount_and_parent_guard(self):
        child = await self.child_account(True)
        await self.opening()
        data = self.payload(
            [("1405", "debit", "8.33", ""), ("1002", "credit", "8.33", "operating")]
        )
        self.assertEqual(
            (await self.post(self.base + "/vouchers", data)).json()["code"],
            "LEAF_REQUIRED",
        )
        data["lines"][0].update(
            account_id=child["id"],
            quantity="2.5",
            unit_price="3.333333",
            unit="件",
            summary="数量商品",
        )
        v = (await self.post(self.base + "/vouchers", data)).json()["data"]
        self.assertEqual(v["lines"][0]["quantity"], "2.5")
        self.assertEqual(v["lines"][0]["summary"], "数量商品")
        v = await self.complete(v)
        trial = (await self.client.get(self.base + "/trial-balance")).json()["data"]
        self.assertEqual(
            next(
                x["quantity_balance"] for x in trial["rows"] if x["id"] == child["id"]
            ),
            "2.5",
        )
        r = await self.client.patch(
            self.base + "/chart-accounts/" + str(child["id"]),
            json={
                "name": child["name"],
                "enabled": True,
                "quantity_enabled": True,
                "unit": "箱",
                "expected_version": child["version"],
            },
        )
        self.assertEqual(r.json()["code"], "UNIT_LOCKED")
        r = await self.post(
            self.base + f"/vouchers/{v['id']}/reversal-drafts",
            {"accounting_date": "2026-01-02", "reason": "退货"},
        )
        await self.complete(r.json()["data"])
        trial = (await self.client.get(self.base + "/trial-balance")).json()["data"]
        self.assertEqual(
            next(
                x["quantity_balance"] for x in trial["rows"] if x["id"] == child["id"]
            ),
            "0",
        )
        data["lines"][0]["amount"] = "8.34"
        data["lines"][1]["amount"] = "8.34"
        self.assertEqual(
            (await self.post(self.base + "/vouchers", data)).json()["code"],
            "QUANTITY_AMOUNT_MISMATCH",
        )

    async def test_quantity_required_and_unknown_child_parent(self):
        child = await self.child_account(True)
        data = self.payload([("1405", "debit", "1", ""), ("1002", "credit", "1", "")])
        data["lines"][0]["account_id"] = child["id"]
        self.assertEqual(
            (await self.post(self.base + "/vouchers", data)).json()["code"],
            "QUANTITY_REQUIRED",
        )
        r = await self.client.post(
            self.base + "/chart-accounts",
            json={
                "code": "140502",
                "name": "错误上级",
                "kind": "asset",
                "normal_side": "debit",
                "parent_id": 99999,
            },
        )
        self.assertEqual(r.json()["code"], "PARENT_INVALID")

    async def test_multi_book_management_preserves_ledger(self):
        await self.opening()
        other = (
            await self.post(
                "/api/v2/books", {"name": "第二账套", "starts_on": "2026-02-01"}
            )
        ).json()["data"]
        rows = (await self.client.get("/api/v2/books")).json()["data"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(next(x["id"] for x in rows if x["is_current"]), other["id"])
        r = await self.client.patch(
            self.base,
            json={
                "name": "已更名账套",
                "starts_on": "2026-01-01",
                "expected_version": 1,
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        r = await self.client.delete(self.base)
        self.assertTrue(r.json()["data"]["archived"])
        self.assertEqual(
            (await self.client.get(self.base + "/vouchers")).json()["data"][0]["state"],
            "posted",
        )
        self.assertEqual(
            (
                await self.post(
                    self.base + "/vouchers",
                    self.payload(
                        [("1002", "debit", "1", ""), ("3001", "credit", "1", "")]
                    ),
                )
            ).json()["code"],
            "BOOK_ARCHIVED",
        )
        info = (await self.client.get(self.base)).json()["data"]
        r = await self.client.patch(
            self.base,
            json={
                "name": info["name"],
                "starts_on": info["starts_on"],
                "expected_version": info["version"],
                "archived": False,
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(
            (await self.post(self.base + "/activate", {})).json()["data"][
                "active_book_id"
            ],
            self.bid,
        )
        r = await self.client.patch(
            self.base,
            json={
                "name": "改日期",
                "starts_on": "2026-02-01",
                "expected_version": r.json()["data"]["version"],
            },
        )
        self.assertEqual(r.json()["code"], "BOOK_DATE_LOCKED")

    async def test_chinese_operations_and_input_feedback(self):
        from fastapi.exceptions import RequestValidationError
        from firefly.api.validation import validation_error_handler

        self.app.add_exception_handler(RequestValidationError, validation_error_handler)
        payload = self.payload(
            [("1002", "debit", "1", ""), ("3001", "credit", "1", "")]
        )
        payload["summary"] = ""
        r = await self.post(self.base + "/vouchers", payload)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["message"], "请填写凭证摘要")
        self.assertNotIn("detail", r.json())
        await self.category()
        response = await self.client.get("/api/v2/operation-events")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["data"]["items"]
        self.assertTrue(any(x["title"] == "维护预算费用类别" for x in items))
        self.assertTrue(
            all("detail" not in item and "action" not in item for item in items)
        )
        self.assertIn("网页端", {x["platform_name"] for x in items})

    async def test_multi_book_dimension_backup_roundtrip(self):
        from firefly.accounting.recovery import restore_to_new_database

        cat = await self.category()
        await self.client.put(
            self.base + "/expense-budgets",
            json={"category_id": cat["id"], "month": "2026-01", "amount": "100"},
        )
        await self.opening()
        child = await self.child_account(True)
        data = self.payload(
            [("1405", "debit", "10", ""), ("1002", "credit", "10", "operating")]
        )
        data["lines"][0].update(
            account_id=child["id"], quantity="2", unit_price="5", unit="件"
        )
        await self.complete(
            (await self.post(self.base + "/vouchers", data)).json()["data"]
        )
        await self.post(
            "/api/v2/books", {"name": "第二账套", "starts_on": "2026-01-01"}
        )
        payload = (await self.client.get("/api/v2/backup")).json()["data"]
        result = await restore_to_new_database(
            payload, Path(self.tmp.name) / "dimensions.db"
        )
        self.assertEqual(result["counts"], payload["counts"])
        self.assertEqual(result["counts"]["expense_categories"], 1)
        self.assertEqual(len(result["trial_balances"]), 2)


if __name__ == "__main__":
    unittest.main()
