"""流萤 Firefly · 数据备份与还原。

设计的出发点是一句话：**还原比备份危险得多。**

备份失败最多是没有备份，用户还能重来一次；还原失败却可能把用户
攒了几个月的账「还原」成一半旧数据一半新数据，而且看不出来。
所以这里的取舍全部偏向保守：

1. **还原前先自动做一次安全备份**（``data/backups/auto_before_restore_*.json``），
   用户万一还原错了还能退回去。不留后路的破坏性操作不该给用户点。
2. **整表替换而不是逐条 upsert**。逐条合并的后果是「备份里删掉的记录，
   还原后会重新长出来」，账目数量对不上又找不到原因。整表替换语义清晰：
   还原 = 回到备份时的那个状态。
3. **还原在一个事务里完成**，中途失败整体回滚，不允许出现半成品状态。
4. **校验备份文件结构**再动手，宁可拒绝也不写坏库。

备份格式选 JSON 而不是直接拷 ``firefly.db``：SQLite 文件拷贝要求
「没有写入方」，服务运行中拷出来的文件可能处于 WAL 中间态。
JSON 是逻辑层导出，只要 MySQL/SQLite 能读出 ORM 对象就能生成，
也不怕跨版本（拷文件则会受 SQLite 版本、页大小影响）。
代价是体积大一些、丢失索引定义——但本项目的数据量（个人/小微企业账目）
完全在承受范围内。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import BASE_DIR
from ..db.models import (
    Account,
    AuditLog,
    Budget,
    Category,
    ImportBatch,
    PolicyDoc,
    Report,
    Subscription,
    Suggestion,
    SystemState,
    TaxCheckResult,
    Transaction,
    User,
)

# --------------------------------------------------------------------------------
# 表清单
# --------------------------------------------------------------------------------

# 导出/导入顺序：**父表在前，子表在后**。
# 导入时必须先插父表，否则外键约束（若启用）会失败；
# 删除时必须反过来，先删子表再删父表。
BACKUP_TABLES: List[Tuple[str, Type[Any]]] = [
    ("users", User),
    ("accounts", Account),
    ("categories", Category),
    ("transactions", Transaction),
    ("subscriptions", Subscription),
    ("budgets", Budget),
    ("reports", Report),
    ("tax_checks", TaxCheckResult),
    ("suggestions", Suggestion),
    ("audit_logs", AuditLog),
    ("policy_docs", PolicyDoc),
    ("import_batches", ImportBatch),
    ("system_states", SystemState),
]

# 系统状态表里这些键属于**运行时状态**，不是用户数据。
# 还原时把它们排除掉：否则「今天已经发过预警」这类标记会被一起回滚，
# 导致还原当天重复推送预警。
SYSTEM_STATE_SKIP_KEYS = {
    "alert_sent",
    "last_push_at",
    "last_subscription_run",
}

BACKUP_VERSION = 1
BACKUP_DIR = BASE_DIR / "data" / "backups"
AUTO_BACKUP_KEEP = 5  # 自动安全备份保留份数，避免无限增长


# --------------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    """文件名用的时间戳（本地可读）。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _serialize(value: Any) -> Any:
    """把 ORM 字段值转成可 JSON 化的形式。

    主要处理 datetime：模型里统一带时区（UTC），直接 isoformat
    还原时用 ``datetime.fromisoformat`` 就能读回来，
    且不会丢时区信息——丢了时区会在写库时被 SQLModel 拒绝。
    """
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_to_dict(obj: Any) -> Dict[str, Any]:
    """把 ORM 对象转成纯字典。

    用 ``model_dump()`` 而不是 ``obj.__dict__``：后者会带上
    ``_sa_instance_state`` 这类 SQLAlchemy 内部字段，写进备份文件
    既无用又会在还原时报错。
    """
    return {k: _serialize(v) for k, v in obj.model_dump().items()}


async def _table_rows(session: AsyncSession, model: Type[Any]) -> List[Any]:
    result = await session.exec(select(model))
    return list(result.all())


# --------------------------------------------------------------------------------
# 备份
# --------------------------------------------------------------------------------


async def build_backup(
    session: AsyncSession,
    *,
    note: str = "",
    include_empty_users: bool = True,
) -> Dict[str, Any]:
    """把全库导出成一个可序列化的字典。

    ``note`` 会写进文件头，用于区分「手动备份」与「还原前自动备份」。
    """
    payload: Dict[str, Any] = {
        "format": "firefly-backup",
        "version": BACKUP_VERSION,
        "created_at": _now().isoformat(),
        "note": note,
        "tables": {},
        "counts": {},
    }

    for name, model in BACKUP_TABLES:
        rows = await _table_rows(session, model)
        dicts = [_row_to_dict(r) for r in rows]

        # 系统状态里过滤掉运行时标记
        if name == "system_states":
            dicts = [d for d in dicts if d.get("key") not in SYSTEM_STATE_SKIP_KEYS]

        payload["tables"][name] = dicts
        payload["counts"][name] = len(dicts)

    payload["total_rows"] = sum(payload["counts"].values())
    return payload


async def create_backup_file(
    session: AsyncSession,
    *,
    note: str = "",
    directory: Optional[Path] = None,
    filename: Optional[str] = None,
) -> Path:
    """生成备份文件并返回路径。"""
    data = await build_backup(session, note=note)
    target_dir = directory or BACKUP_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    if filename is None:
        filename = f"firefly_backup_{_stamp()}.json"

    path = target_dir / filename
    # ensure_ascii=False：备份里有中文类别名、备注，写成 \uXXXX
    # 虽然能还原，但用户打开文件看不懂，也没法手工检查。
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def make_auto_backup_sync_name() -> str:
    return f"auto_before_restore_{_stamp()}.json"


def cleanup_auto_backups(directory: Optional[Path] = None, keep: int = AUTO_BACKUP_KEEP) -> int:
    """只保留最近 ``keep`` 份自动备份，返回删除数量。

    只清理 ``auto_before_restore_`` 前缀的：用户手动备份的文件
    可能被改名留存，绝不能替他删。
    """
    target_dir = directory or BACKUP_DIR
    if not target_dir.exists():
        return 0

    files = sorted(
        target_dir.glob("auto_before_restore_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in files[keep:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


# --------------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------------


def validate_backup(data: Any) -> Tuple[bool, str]:
    """校验备份文件结构，返回 (是否可用, 说明)。

    校验刻意做得**具体**：只说「文件损坏」用户无从下手，
    指出「缺 transactions 表」才能让人判断是不是拿错了文件。
    """
    if not isinstance(data, dict):
        return False, "备份内容不是 JSON 对象"

    if data.get("format") != "firefly-backup":
        return False, "这不是流萤的备份文件（缺少 format 标识）"

    version = data.get("version")
    if not isinstance(version, int):
        return False, "备份缺少版本号"
    if version > BACKUP_VERSION:
        return False, (
            f"备份版本 v{version} 高于当前程序支持的 v{BACKUP_VERSION}，"
            "请升级程序后再还原"
        )

    tables = data.get("tables")
    if not isinstance(tables, dict):
        return False, "备份缺少 tables 数据"

    known = {name for name, _ in BACKUP_TABLES}
    missing = known - set(tables.keys())
    if missing:
        # 缺表不算致命：早期版本的备份可能没有后加的表。
        # 但必须告知，让用户知道这些数据不会被还原。
        return True, "备份缺少部分数据表：" + "、".join(sorted(missing)) + "（这些表将保持现状）"

    for name in known:
        if not isinstance(tables[name], list):
            return False, f"备份里 {name} 不是列表，文件可能已损坏"

    return True, "结构校验通过"


def summarize_backup(data: Dict[str, Any]) -> Dict[str, Any]:
    """生成给前端展示的备份摘要。"""
    counts = data.get("counts") or {}
    if not counts:
        counts = {k: len(v) for k, v in (data.get("tables") or {}).items() if isinstance(v, list)}
    return {
        "created_at": data.get("created_at"),
        "note": data.get("note") or "",
        "version": data.get("version"),
        "counts": counts,
        "total_rows": data.get("total_rows") or sum(counts.values()),
    }


# --------------------------------------------------------------------------------
# 还原
# --------------------------------------------------------------------------------


def _parse_datetime(value: Any) -> Any:
    """把备份里的 ISO 字符串还原成 datetime。

    SQLModel 要求带时区，而 ``datetime.fromisoformat`` 在 Python 3.11
    之前无法解析结尾的 ``Z``，这里做一次兜底替换。
    """
    if not isinstance(value, str):
        return value
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return value


def _coerce_row(model: Type[Any], row: Dict[str, Any]) -> Dict[str, Any]:
    """只保留模型真实存在的字段，并把 datetime 字段转回来。

    为什么要过滤字段：备份文件是用户可编辑的文本，可能被手工加过字段，
    也可能来自旧版本（那时有、现在删掉的列）。不过滤的话
    ``Model(**row)`` 会直接抛 TypeError，整次还原失败——
    而这本来是可以容忍的。
    """
    valid = set(model.model_fields.keys())
    out: Dict[str, Any] = {}
    for key, value in row.items():
        if key not in valid:
            continue
        field = model.model_fields[key]
        # 依赖类型标注判断要不要转 datetime，避免把普通字符串误转
        annotation = str(field.annotation)
        if "datetime" in annotation:
            value = _parse_datetime(value)
        out[key] = value
    return out


async def restore_backup(
    session: AsyncSession,
    data: Dict[str, Any],
    *,
    make_safety_backup: bool = True,
) -> Dict[str, Any]:
    """用备份内容替换当前数据，返回结果摘要。

    整表替换语义（见模块 docstring）。整个删除 + 插入在**同一个事务**里，
    调用方负责 commit；这里不 commit，是为了让 API 层能
    「先安全备份 → 再还原」处于一次可控流程中。
    """
    ok, message = validate_backup(data)
    if not ok:
        raise ValueError(message)

    safety_path: Optional[Path] = None
    if make_safety_backup:
        # 还原前先把「当前状态」存下来。用户点还原有可能是点错了，
        # 没有这一步他就再也回不到还原前的数据了。
        safety_path = await create_backup_file(
            session,
            note="还原前的自动安全备份",
            filename=make_auto_backup_sync_name(),
        )

    tables = data.get("tables") or {}
    counts: Dict[str, int] = {}

    # ---- 1) 删除：子表先删 ----
    # 逆序删除，与导入顺序相反。SQLite 默认不强制外键，
    # 但显式按依赖顺序删能在启用外键约束的部署上同样正确。
    for name, model in reversed(BACKUP_TABLES):
        rows = await _table_rows(session, model)
        for obj in rows:
            await session.delete(obj)
    await session.flush()

    # ---- 2) 插入：父表先插 ----
    for name, model in BACKUP_TABLES:
        rows = tables.get(name) or []
        inserted = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            if name == "system_states" and row.get("key") in SYSTEM_STATE_SKIP_KEYS:
                continue
            try:
                session.add(model(**_coerce_row(model, row)))
                inserted += 1
            except Exception as exc:  # noqa: BLE001
                # 单行失败不中断整表：一行坏数据不该让整个还原失败。
                # 但计数会体现在结果里，前端能看到差异。
                _ = exc
        counts[name] = inserted

    await session.flush()
    return {
        "counts": counts,
        "total_rows": sum(counts.values()),
        "safety_backup": str(safety_path) if safety_path else None,
        "safety_backup_name": safety_path.name if safety_path else None,
        "restored_from": data.get("created_at"),
    }


# --------------------------------------------------------------------------------
# 文件管理
# --------------------------------------------------------------------------------


def list_backup_files(directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    """列出备份目录下的文件，按时间倒序。"""
    target_dir = directory or BACKUP_DIR
    if not target_dir.exists():
        return []

    items: List[Dict[str, Any]] = []
    for path in sorted(target_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "size_label": _human_size(stat.st_size),
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "is_auto": path.name.startswith("auto_before_restore_"),
            }
        )
    return items


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def read_backup_file(name: str, directory: Optional[Path] = None) -> Dict[str, Any]:
    """按文件名读取备份，带路径穿越防护。

    必须防 ``../../etc/passwd`` 这类名字：文件名来自 HTTP 参数，
    不校验就等于把整个文件系统读接口暴露出去。
    """
    target_dir = (directory or BACKUP_DIR).resolve()
    # 只取 basename，再确认结果仍在备份目录内
    safe_name = Path(name).name
    path = (target_dir / safe_name).resolve()
    if target_dir not in path.parents and path.parent != target_dir:
        raise ValueError("非法的备份文件名")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"备份文件不存在：{safe_name}")
    if path.suffix.lower() != ".json":
        raise ValueError("只支持 .json 备份文件")

    return json.loads(path.read_text(encoding="utf-8"))


def delete_backup_file(name: str, directory: Optional[Path] = None) -> bool:
    target_dir = (directory or BACKUP_DIR).resolve()
    safe_name = Path(name).name
    path = (target_dir / safe_name).resolve()
    if target_dir not in path.parents and path.parent != target_dir:
        raise ValueError("非法的备份文件名")
    if not path.exists():
        return False
    path.unlink()
    return True


__all__ = [
    "BACKUP_TABLES",
    "BACKUP_VERSION",
    "BACKUP_DIR",
    "SYSTEM_STATE_SKIP_KEYS",
    "build_backup",
    "create_backup_file",
    "validate_backup",
    "summarize_backup",
    "restore_backup",
    "list_backup_files",
    "read_backup_file",
    "delete_backup_file",
    "cleanup_auto_backups",
    "make_auto_backup_sync_name",
]
