"""Version and local-only, authenticated repository maintenance."""
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, Request
from ..services import updater
from .deps import fail, ok
router = APIRouter(tags=['system'])

@router.get('/system/version')
async def version():
    return ok(await updater.version_info())

@router.post('/system/update')
async def update(request: Request):
    if not request.client or request.client.host not in {'127.0.0.1', '::1', 'testclient'}:
        raise HTTPException(403, '仓库更新仅允许从服务所在电脑执行')
    if request.headers.get('x-firefly-update') != '1':
        raise HTTPException(400, '缺少更新操作标记')
    origin = request.headers.get('origin')
    if origin and urlparse(origin).netloc != request.headers.get('host'):
        raise HTTPException(403, '更新请求来源不匹配')
    try:
        return ok(await updater.pull_update())
    except (OSError, RuntimeError, ValueError) as exc:
        return fail(str(exc))
