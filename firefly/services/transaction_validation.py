"""Shared ownership and direction checks for transaction references."""
from . import ledger


async def validate_references(session, *, user_id, category_id, account_id, direction):
    categories = await ledger.list_categories(session, user_id=user_id)
    category = next((c for c in categories if c.id == category_id), None)
    if category_id is not None and category is None:
        raise ValueError("类别不存在或无权使用")
    if category is not None and category.direction != direction:
        raise ValueError("类别与收支方向不一致")
    if account_id is not None:
        accounts = await ledger.list_accounts(session, user_id=user_id)
        if not any(a.id == account_id for a in accounts):
            raise ValueError("账本不存在或无权使用")
