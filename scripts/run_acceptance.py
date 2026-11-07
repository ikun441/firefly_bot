"""Local acceptance instance: dedicated database/config, real OneBot connection.
Run with the project's Python. Never reads or writes a production ledger.
"""

from pathlib import Path
import secrets
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from firefly import config as settings

runtime = ROOT / "data" / "acceptance"
runtime.mkdir(parents=True, exist_ok=True)
cfg = settings.load_config().model_copy(deep=True)
cfg.app.debug = False
cfg.server.host = "127.0.0.1"
cfg.server.port = 2525
cfg.database.url = "sqlite+aiosqlite:///" + str(runtime / "accounting.db")
cfg.auth.enabled = True
cfg.push.enabled = False
cfg.alert.enabled = False
cfg.subscription.enabled = False
# Keep the administrator credentials from the project's config.yaml.
# Isolate the token signing secret, database and writable runtime configuration.
previous = (
    yaml.safe_load((runtime / "config.yaml").read_text())
    if (runtime / "config.yaml").exists()
    else {}
)
old_auth = previous.get("auth", {})
same_credentials = all(
    old_auth.get(key) == getattr(cfg.auth, key)
    for key in ("username", "password", "password_hash", "password_salt")
)
cfg.auth.secret_key = (
    old_auth.get("secret_key") if same_credentials else ""
) or secrets.token_urlsafe(32)
settings.CONFIG_PATH = runtime / "config.yaml"
settings.ENV_PATH = runtime / "unused.env"
settings.DATA_DIR = runtime
settings._CONFIG_CACHE = cfg
settings.CONFIG_PATH.write_text(yaml.safe_dump(cfg.model_dump(), allow_unicode=True))
settings.CONFIG_PATH.chmod(0o600)

from firefly.app import app, driver
from firefly.accounting.models import AccessControl
from firefly.accounting.api import transaction
from firefly.accounting.identity import Actor, acting_as
from firefly.accounting.service import event


@driver.on_startup
async def bind_test_administrator():
    with acting_as(Actor("admin", cfg.auth.username, "web")):
        async with transaction() as s:
            control = await s.get(AccessControl, 1)
            if control.admin_qq != "3497689533":
                old = control.admin_qq
                control.admin_qq = "3497689533"
                control.version += 1
                s.add(control)
                event(
                    s,
                    "bind_administrator",
                    before=old,
                    after=control.admin_qq,
                    authorization="project owner",
                )


# The API is administrator-only and only accepts the explicitly named test group.
from fastapi import Depends
from firefly.api.deps import require_auth


@app.post("/api/v2/acceptance/send-help", dependencies=[Depends(require_auth)])
async def send_real_help():
    import nonebot
    from firefly.accounting.help import help_markdown
    from firefly.render.ai_reply import send_ai_reply

    found = []
    for bot in nonebot.get_bots().values():
        groups = await bot.get_group_list()
        found.extend((bot, g) for g in groups if g.get("group_name") == "梅穿库子")
    if len(found) != 1:
        return {
            "success": False,
            "message": "测试群需唯一匹配，未发送",
            "matches": len(found),
        }
    bot, group = found[0]
    with acting_as(Actor("admin", cfg.auth.username, "web")):
        async with transaction() as s:
            markdown = await help_markdown(s)
            event(s, "acceptance_help_send", group_id=str(group["group_id"]), group_name=group["group_name"])
    message_ids = []

    async def send(message):
        receipt = await bot.send_group_msg(group_id=group["group_id"], message=message)
        message_ids.append(receipt.get("message_id"))
        return receipt

    result = await send_ai_reply(
        send,
        "联调测试 · 管理员已授权发送至本群\n\n" + markdown,
        title="流萤 · 命令帮助",
    )
    return {
        "success": True,
        "data": {
            "group_id": group["group_id"],
            "group_name": group["group_name"],
            "message_ids": message_ids,
            **result,
        },
    }


@app.post("/api/v2/acceptance/send-report", dependencies=[Depends(require_auth)])
async def send_real_report(period: str = "本月"):
    """Authorized outbound integration check; does not simulate a QQ sender."""
    import nonebot
    from firefly.accounting.service import active_book, reports
    from firefly.accounting.periods import parse_range
    from firefly.plugins.chat import report_markdown
    from firefly.render.ai_reply import send_ai_reply

    found = []
    for bot in nonebot.get_bots().values():
        found.extend((bot, g) for g in await bot.get_group_list() if g.get("group_name") == "梅穿库子")
    if len(found) != 1:
        return {"success": False, "message": "测试群需唯一匹配，未发送"}
    bot, group = found[0]
    with acting_as(Actor("admin", cfg.auth.username, "web")):
        async with transaction() as s:
            book = await active_book(s)
            if not book:
                return {"success": False, "message": "请先创建账套"}
            start, end = parse_range(period)
            markdown = report_markdown(await reports(s, book.id, start, end))
            event(s, "acceptance_report_send", book_id=book.id, group_id=str(group["group_id"]), start=str(start), end=str(end))
    receipts = []
    async def send(message):
        receipt = await bot.send_group_msg(group_id=group["group_id"], message=message)
        receipts.append(receipt.get("message_id"))
        return receipt
    result = await send_ai_reply(send, "联调测试 · 网页管理员发起\n\n" + markdown, title="流萤 · 会计报告")
    verified = []
    for mid in receipts:
        delivered = await bot.get_msg(message_id=mid)
        verified.append({"message_id": mid, "types": [m.get("type") for m in delivered.get("message", [])]})
    return {"success": True, "data": {**result, "group_id": group["group_id"], "verified_messages": verified}}


@app.get("/api/v2/acceptance/status", dependencies=[Depends(require_auth)])
async def acceptance_status():
    import nonebot

    bots = list(nonebot.get_bots().values())
    groups = []
    for bot in bots:
        groups.extend(
            {"group_id": g["group_id"], "group_name": g["group_name"]}
            for g in await bot.get_group_list()
            if g.get("group_name") == "梅穿库子"
        )
    return {
        "success": True,
        "data": {"bots": [str(b.self_id) for b in bots], "test_groups": groups},
    }


# StaticFiles is a catch-all mount; acceptance routes must precede it.
for route in list(app.router.routes):
    if getattr(route, "path", "").startswith("/api/v2/acceptance/"):
        app.router.routes.remove(route)
        app.router.routes.insert(0, route)

if __name__ == "__main__":
    import nonebot

    nonebot.run()
