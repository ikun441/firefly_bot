"""Permission-aware command catalogue shared by /帮助 and its image."""

from .identity import access


async def help_markdown(session):
    info = await access(session)
    actor = info["actor"]
    rights = info["permissions"]
    who = "管理员" if info["is_admin"] else "QQ " + actor["identifier"]
    labels = {"create": "增", "delete": "删", "update": "改", "read": "查"}
    allowed = "、".join(labels[k] for k, v in rights.items() if v) or "未授权"
    lines = [
        "# 流萤 · 命令帮助",
        f"当前身份：{who}  · 权限：{allowed}",
        "",
        "| 命令 | 用途 |",
        "| --- | --- |",
        "| /帮助 · /help · /菜单 | 查看本人可用命令 |",
    ]
    if rights["create"]:
        lines += [
            "| /记 用途 金额 | 解析业务，确认后保存草稿 |",
            "| 接受 / 不接受 + 修改意见 / 取消 | 处理本人当前对话草稿 |",
        ]
    if rights["read"]:
        lines += [
            "| ai 问题 · /ai 问题 | AI 只读会计问询与分析 |",
            "| 示例：ai 本月利润是多少？为什么利润与现金增加不同？ | 可组合查询科目、凭证及财务报表 |",
            "| /明细 [周期或起止日期] [第N页] | 按会计日期查询凭证及状态，每页10张 |",
            "| /报告 [周期或起止日期] | 指定期间会计汇总，仅已过账 |",
            "| /余额 | 试算平衡与科目余额 |",
            "| /预算 [YYYY-MM] | 查看费用类别预算及已过账执行 |",
        ]
    if rights["read"]:
        lines += [
            "",
            "查询周期：今天 / 昨天 / 本周 / 上周 / 本月 / 上月 / 本季度 / 上季度 / 本年 / 去年。",
            "不填默认本月；可指定 2026-11、2026 或起止日期。",
            "示例：/明细 上月 第2页 · /报告 2026-11 · /报告 2026-11-01 2026-11-30",
            "",
        ]
    if rights["update"]:
        lines += [
            "| /提交 凭证ID | 提交草稿供管理员审核 |",
            "| /修改摘要 凭证ID 新摘要 | 修改未过账草稿摘要 |",
        ]
    if rights["delete"]:
        lines += [
            "| /作废 凭证ID 原因 | 作废未过账凭证 |",
            "| /冲销 凭证ID YYYY-MM-DD 原因 | 申请已过账凭证的全额冲销 |",
        ]
    if info["is_admin"]:
        lines += [
            "| /审核 凭证ID | 审核已提交凭证 |",
            "| /过账 凭证ID | 过账已审核凭证 |",
            "| 网页 → 权限设置 | 绑定管理员 QQ、添加普通 QQ 并授权 |",
        ]
    lines += [
        "",
        "确认业务只保存待处理草稿，不直接过账。",
        "已过账凭证不可改删，冲销也需管理员审核过账。",
        "群聊普通记账请 @机器人；权限按发送者 QQ 校验。",
        "首次使用请由管理员在网页创建账套并核定期初。",
    ]
    if not any(rights.values()):
        lines += ["", "你尚未获得账务权限，请联系管理员添加你的 QQ 账号。"]
    return "\n".join(lines)
