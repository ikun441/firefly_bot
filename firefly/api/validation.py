"""Chinese input feedback without leaking raw validation objects or input values."""

from fastapi.responses import JSONResponse

LABELS = {
    "summary": "凭证摘要",
    "accounting_date": "会计日期",
    "amount": "金额",
    "lines": "凭证分录",
    "account_id": "会计科目",
    "name": "名称",
    "starts_on": "启用日期",
    "quantity": "数量",
    "unit_price": "单价",
    "budget_category_id": "预算费用类别",
    "reason": "操作说明",
    "unit": "计量单位",
}


def validation_message(errors):
    messages = []
    for error in errors:
        path = error.get("loc", [])
        field = path[-1] if path else ""
        label = LABELS.get(field, "相关信息")
        index = next((x for x in path if type(x) is int), None)
        prefix = f"第{index + 1}条分录：" if index is not None else ""
        kind = error.get("type", "")
        if kind in {"missing", "string_too_short"}:
            message = prefix + "请填写" + label
        elif kind == "too_short" and field == "lines":
            message = "凭证至少需要两条分录"
        elif kind == "string_too_long":
            message = prefix + label + "内容过长"
        else:
            message = prefix + "请检查" + label + "，填写有效内容"
        if message not in messages:
            messages.append(message)
    return "；".join(messages) or "请检查输入内容后重试"


async def validation_error_handler(request, exc):
    return JSONResponse(
        {
            "success": False,
            "code": "INPUT_INVALID",
            "message": validation_message(exc.errors()),
        },
        status_code=422,
    )
