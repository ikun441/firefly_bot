"""Chinese presentation of audit events; raw evidence stays available to the application."""

ACTIONS = {
    "create_book": "创建会计账套",
    "read_book": "查看账套详情",
    "update_book": "编辑账套",
    "activate_book": "切换当前账套",
    "archive_book": "归档账套",
    "web_request": "网页操作请求",
    "qq_command_request": "QQ命令操作",
    "create_account": "新增会计科目",
    "update_account": "调整会计科目",
    "create_voucher": "保存凭证草稿",
    "update_voucher": "修改凭证草稿",
    "submit": "提交凭证审核",
    "withdraw": "撤回凭证",
    "approve": "审核通过",
    "reject": "驳回凭证",
    "revoke-approval": "撤销凭证审核",
    "post": "凭证过账",
    "void": "作废凭证",
    "reversal_draft": "申请全额冲销",
    "confirm_zero_opening": "核定全零期初",
    "bind_administrator": "绑定管理员QQ",
    "set_qq_permissions": "设置QQ操作权限",
    "revoke_qq_permissions": "撤销QQ操作权限",
    "list_books": "查看账套",
    "list_vouchers": "查看凭证明细",
    "read_voucher": "查看凭证",
    "read_trial_balance": "查看科目余额",
    "read_reports": "查看财务汇总",
    "read_business_drafts": "查看业务草稿",
    "resolve_business_draft": "处理业务草稿",
    "create_business_draft": "保存业务草稿",
    "preview_business": "解析记账业务",
    "confirm_business": "确认业务草稿",
    "cancel_conversation": "取消记账业务",
    "reject_business": "退回业务解析",
    "refine_business": "重新解析业务",
    "import_business": "导入业务资料",
    "reconcile_cash_account": "完成资金对账",
    "close_period": "关闭会计期间",
    "ai_question": "发起AI问询",
    "ai_read_tool": "AI读取账务资料",
    "ai_answer": "完成AI分析",
    "acceptance_help_send": "发送帮助图片",
    "acceptance_report_send": "联调发送报告图片",
    "export_full_backup": "导出完整备份",
    "save_expense_category": "维护预算费用类别",
    "set_expense_budget": "设置费用预算",
    "read_expense_budgets": "查看费用预算",
    "qq_read_balances": "查询科目余额",
    "qq_list_vouchers": "查询凭证明细",
}


def actor_text(actor):
    kind = actor.get("kind")
    who = (
        "管理员 " + actor.get("identifier", "")
        if kind == "admin"
        else "QQ " + actor.get("identifier", "")
        if kind == "qq"
        else "登录尝试 " + actor.get("identifier", "")
        if kind == "login"
        else "系统任务"
    )
    platform = {"web": "网页端", "qq": "QQ端", "system": "系统"}.get(
        actor.get("platform"), "系统"
    )
    return who, platform


def event_view(event):
    data = event.model_dump(mode="json")
    detail = data.get("detail") or {}
    title = ACTIONS.get(event.action, "账务操作")
    operator, platform = actor_text(event.actor or {})
    parts = []
    if event.voucher_id:
        parts.append("凭证编号：" + str(event.voucher_id))
    if event.action == "web_request":
        parts.append(str(detail.get("operation_name") or "网页操作"))
    if event.action == "qq_command_request":
        parts.append("命令：" + str(detail.get("command") or "帮助"))
    if detail.get("name") and event.action in {
        "create_book",
        "update_book",
        "activate_book",
        "archive_book",
    }:
        parts.append("账套：" + str(detail["name"]))
    if detail.get("code"):
        parts.append("科目：" + str(detail["code"]) + " " + str(detail.get("name", "")))
    if detail.get("parent_name"):
        parts.append("上级科目：" + str(detail["parent_name"]))
    if detail.get("category_name"):
        parts.append("费用类别：" + str(detail["category_name"]))
    if detail.get("month"):
        parts.append("期间：" + str(detail["month"]))
    if detail.get("amount"):
        parts.append("预算额度：" + str(detail["amount"]) + " 元")
    if detail.get("filename"):
        parts.append("文件：" + str(detail["filename"]))
    if detail.get("count") is not None:
        parts.append("处理 " + str(detail["count"]) + " 条资料")
    if detail.get("start") or detail.get("end"):
        parts.append(
            "查询期间："
            + str(detail.get("start") or "启用日")
            + " 至 "
            + str(detail.get("end") or "当前")
        )
    if detail.get("reason"):
        parts.append("说明：" + str(detail["reason"]))
    if event.action == "bind_administrator":
        parts.append("管理员QQ：" + str(detail.get("after") or "已解除绑定"))
    if detail.get("qq"):
        parts.append("QQ账号：" + str(detail["qq"]))
    if event.action == "set_qq_permissions":
        grant = detail.get("after") or {}
        rights = [
            label
            for key, label in [
                ("can_create", "新增"),
                ("can_delete", "作废与冲销"),
                ("can_update", "修改"),
                ("can_read", "查询"),
            ]
            if grant.get(key)
        ]
        parts.append(
            "授权："
            + (
                "、".join(rights)
                if grant.get("enabled") and rights
                else "未授予操作权限"
            )
        )
    if (
        event.action in {"create_account", "update_account"}
        and "quantity_enabled" in detail
    ):
        parts.append(
            "数量核算：" + ("已开启" if detail["quantity_enabled"] else "未开启")
        )
    if event.action == "resolve_business_draft":
        parts.append(
            "处理结果："
            + ("已关联凭证" if detail.get("state") == "linked" else "已作废")
        )
    if event.actor.get("group_id"):
        parts.append("群号：" + event.actor["group_id"])
    return {
        **data,
        "title": title,
        "description": "；".join(parts) or "操作已记录",
        "operator_name": operator,
        "platform_name": platform,
    }
