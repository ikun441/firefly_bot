"""Verify and restore a v2 JSON backup to a new, isolated SQLite database."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from firefly.accounting.recovery import restore_to_new_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="New database path; existing paths are refused",
    )
    args = parser.parse_args()
    if args.backup.stat().st_size > 64 * 1024 * 1024:
        parser.error("备份超过 64 MB，请使用专门恢复流程")
    try:
        data = json.loads(args.backup.read_text(encoding="utf-8"))
        result = asyncio.run(restore_to_new_database(data, args.database))
    except Exception as exc:
        print("恢复未完成：" + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
