"""Run the existing API suite against an isolated ASGI app and temporary database.
No listening port, real-account messages or user-config writes.
Run: python -m tests.run_isolated_api
"""
import asyncio
import functools
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import httpx
import yaml
from firefly import config as settings
from firefly.db import session as db
from firefly.services.auth import ensure_auth_config
from tests.test_api import ApiTester, print_report

async def main():
    with tempfile.TemporaryDirectory(prefix='firefly-api-suite-') as directory:
        root=Path(directory)
        settings.CONFIG_PATH=root/'config.yaml';settings.ENV_PATH=root/'unused.env'
        settings.DATA_DIR=root/'data'
        cfg=settings.Config(database={'url':'sqlite+aiosqlite:///'+str(root/'test.db')},llm={'enabled':False},push={'enabled':False,'target_id':''},auth={'enabled':True,'password':'IsolatedTest2026!','allow_default_password':True})
        settings.CONFIG_PATH.write_text(yaml.safe_dump(cfg.model_dump(),allow_unicode=True),encoding='utf-8');settings._CONFIG_CACHE=cfg
        ensure_auth_config();await db.init_db()
        from firefly.app import app
        from nonebot import get_bots
        assert not get_bots(), 'Isolated suite must never attach a real bot'
        client=httpx.AsyncClient
        factory=functools.partial(client,transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),trust_env=False)
        tester=ApiTester('http://testserver','admin','IsolatedTest2026!',use_llm=False)
        with patch('tests.test_api.httpx.AsyncClient',factory): result=await tester.run()
        print_report(result)
        (Path(tempfile.gettempdir())/'firefly-api-suite-results.json').write_text(json.dumps({'passed':result.passed,'failed':result.failed,'skipped':result.skipped,'warned':result.warned,'results':[r.__dict__ for r in result.results]},ensure_ascii=False),encoding='utf-8')
        from firefly.scheduler.tasks import shutdown_scheduler
        shutdown_scheduler()
        await db.dispose_db()
        return 1 if result.failed else 0

if __name__=='__main__':raise SystemExit(asyncio.run(main()))
