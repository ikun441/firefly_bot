"""流萤 Firefly · 数据库会话管理

提供异步引擎、会话依赖注入与建表初始化。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy import event
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import BASE_DIR, get_config

_engine: Optional[AsyncEngine] = None


def _resolve_sqlite_path(url: str) -> str:
    """把 sqlite 相对路径转换为绝对路径

    SQLAlchemy 解析相对路径时以进程工作目录为基准，
    而从不同位置启动服务会导致数据库落到不同位置，因此统一转为绝对路径。
    """
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return url

    raw_path = url[len(prefix):]
    if raw_path in (":memory:", "") or raw_path.startswith("/"):
        return url

    abs_path = (BASE_DIR / raw_path).resolve()
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    return f"{prefix}{abs_path}"


def get_engine() -> AsyncEngine:
    """获取（惰性创建）全局异步引擎"""
    global _engine
    if _engine is None:
        config = get_config()
        url = _resolve_sqlite_path(config.database.url)
        _engine = create_async_engine(
            url,
            echo=config.database.echo,
            future=True,
            # SQLite 在多协程访问下需要关闭同线程检查
            connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        )
        if url.startswith('sqlite'):
            @event.listens_for(_engine.sync_engine, 'connect')
            def configure_sqlite(connection, _record):
                cursor = connection.cursor()
                cursor.execute('PRAGMA foreign_keys=ON')
                cursor.execute('PRAGMA busy_timeout=15000')
                cursor.close()
    return _engine


async def init_db() -> None:
    """创建所有表（幂等）"""
    from ..accounting import models as accounting_models
    from ..accounting.migrations import upgrade
    engine = get_engine()
    new_tables = {value.__tablename__ for value in vars(accounting_models).values() if isinstance(value, type) and getattr(value, '__tablename__', '').startswith('ff_')}
    legacy_tables = [t for t in SQLModel.metadata.sorted_tables if t.name not in new_tables]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: SQLModel.metadata.create_all(c, tables=legacy_tables))
        await conn.run_sync(upgrade)


async def dispose_db() -> None:
    """释放引擎连接池，服务关闭时调用"""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """在脚本/后台任务中使用的事务上下文"""
    engine = get_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入用的会话生成器。

    请求正常结束即提交，抛异常则回滚。这一步不能省 ——
    否则路由里所有 ``session.add()`` / ``flush()`` 的结果在请求结束时
    会被静默丢弃，接口返回 200 却什么都没写进库（曾实际踩到）。
    """
    engine = get_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


__all__ = [
    "get_engine",
    "init_db",
    "dispose_db",
    "session_scope",
    "get_session",
]
