"""Legacy file parsers produce reviewable facts, never inferred journal entries."""

import csv
import hashlib
import json
from . import service as svc
from .domain import AccountingError
from .identity import require


async def import_file(session, book_id, filename, content):
    await require(session, "create")
    await svc.get_book(session, book_id)
    if len(content) > 8 * 1024 * 1024:
        raise AccountingError("FILE_TOO_LARGE", "文件上限 8 MB")
    suffix = filename.lower().rsplit(".", 1)[-1]
    try:
        if suffix == "xlsx":
            from ..services.excel_import import parse_excel

            items = parse_excel(content)
        elif suffix == "csv":
            items = parse_csv_facts(content)
        else:
            raise AccountingError("FILE_TYPE", "支持 CSV 或 XLSX 文件")
    except (ValueError, csv.Error) as exc:
        if isinstance(exc, AccountingError):
            raise
        raise AccountingError("IMPORT_INVALID", str(exc)[:1024]) from None
    if not items or len(items) > 2000:
        raise AccountingError("ROW_LIMIT", "每批须为 1–2000 行")
    digest = hashlib.sha256(content).hexdigest()
    rows = []
    for index, item in enumerate(items):
        draft = await svc.business_draft(
            session,
            book_id,
            f"file:{digest}:{index}",
            json.loads(
                json.dumps(
                    {
                        "source": "file",
                        "filename": filename[:255],
                        "row": index + 1,
                        "item": item,
                    },
                    default=str,
                )
            ),
        )
        rows.append(draft.id)
    svc.event(
        session,
        "import_business",
        book_id=book_id,
        filename=filename[:255],
        count=len(rows),
        sha256=digest,
    )
    return {
        "draft_ids": rows,
        "count": len(rows),
        "message": "已生成待制证草稿，尚未过账",
    }


def parse_csv_facts(content):
    """Keep every source row, including transfers excluded by legacy cash reports."""
    import io

    for encoding in ("utf-8-sig", "gb18030"):
        try:
            decoded = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("无法识别文件编码，请另存为 UTF-8")
    lines = decoded.splitlines()
    header_index = next(
        (
            i
            for i, line in enumerate(lines[:30])
            if "," in line
            and ("金额" in line or "发生额" in line)
            and ("时间" in line or "日期" in line)
        ),
        0,
    )
    reader = csv.reader(io.StringIO("\n".join(lines[header_index:])), strict=True)
    headers = next(reader, [])
    if (
        not headers
        or len(headers) > 32
        or any(not h.strip() for h in headers)
        or len(set(headers)) != len(headers)
    ):
        raise ValueError("CSV 表头须为不重复的非空列名，最多 32 列")
    items = []
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        line_number = header_index + reader.line_num
        if len(row) != len(headers):
            raise ValueError(
                f"CSV 第 {line_number} 行列数不匹配；含逗号的金额或文本须用双引号包围"
            )
        if any(len(cell) > 4000 for cell in row):
            raise ValueError(f"CSV 第 {line_number} 行字段过长")
        if len(items) >= 2000:
            raise ValueError("每批最多 2000 行")
        items.append({"source_row": line_number, "fields": dict(zip(headers, row))})
    return items
