"""Fast-forward updates from the fixed official repository, preserving local config."""
from __future__ import annotations
import asyncio
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import yaml
from .. import __version__
from ..config import BASE_DIR, DATA_DIR

REPOSITORY = 'https://github.com/ikun441/firefly_bot'
_lock = asyncio.Lock()

async def _git(root: Path, *args: str, check: bool = True) -> tuple[int, str]:
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
    proc = await asyncio.create_subprocess_exec('git', '-c', 'credential.interactive=false', *args, cwd=root, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        proc.kill(); await proc.communicate()
        raise RuntimeError('Git 操作超时，请检查网络后重试') from None
    if check and proc.returncode:
        # Remote stderr may include credentials; never return it to the browser.
        raise RuntimeError('Git 操作失败，请检查网络、仓库权限或本地分支状态')
    return proc.returncode or 0, stdout.decode('utf-8', errors='replace').strip()

def installed_version(root: Path = BASE_DIR) -> str:
    text = (root / 'firefly' / '__init__.py').read_text(encoding='utf-8')
    match = re.search(r'''__version__\s*=\s*['"]([^'"]+)''', text)
    return match.group(1) if match else __version__

async def version_info(root: Path = BASE_DIR) -> dict[str, Any]:
    info = dict(running_version=__version__, installed_version=installed_version(root), repository=REPOSITORY, target_branch='main', update_supported=False, commit='', branch='', reason='')
    info['restart_required'] = info['installed_version'] != __version__
    if not shutil.which('git'):
        info['reason'] = '未安装 Git，请安装 Git 后重启服务'
        return info
    try:
        _, top = await _git(root, 'rev-parse', '--show-toplevel')
        if Path(top).resolve() != root.resolve():
            raise RuntimeError('当前目录不是仓库根目录，请使用 git clone 安装')
        _, origin = await _git(root, 'remote', 'get-url', 'origin')
        if origin.removesuffix('.git').rstrip('/') not in {REPOSITORY, 'git@github.com:ikun441/firefly_bot'}:
            raise RuntimeError('origin 不是官方仓库，已停用自动更新')
        _, info['commit'] = await _git(root, 'rev-parse', '--short', 'HEAD')
        _, info['branch'] = await _git(root, 'branch', '--show-current')
        if info['branch'] != 'main':
            raise RuntimeError('一键更新仅支持 main 分支，请先保存开发改动并切换 main')
        _, tracked = await _git(root, 'diff', '--name-only', 'HEAD', '-z')
        _, untracked = await _git(root, 'ls-files', '--others', '--exclude-standard', '-z')
        dirty = [p for p in (tracked + '\0' + untracked).split('\0') if p and p != 'config.yaml']
        staged, _ = await _git(root, 'diff', '--cached', '--quiet', check=False)
        if dirty or staged:
            raise RuntimeError('检测到未提交的源码或暂存区改动，请先提交或备份后更新')
        info['update_supported'] = True
    except (OSError, RuntimeError) as exc:
        info['reason'] = str(exc)
    return info

def _merge_config(base: dict, local: dict) -> dict:
    result = dict(base)
    for key, value in local.items():
        result[key] = _merge_config(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result

async def pull_update(root: Path = BASE_DIR, data_dir: Path = DATA_DIR) -> dict[str, Any]:
    if _lock.locked():
        raise RuntimeError('已有更新任务正在执行，请稍后重试')
    async with _lock:
        info = await version_info(root)
        if not info['update_supported']:
            raise RuntimeError(info['reason'])
        await _git(root, 'fetch', '--no-tags', 'origin', 'main')
        _, old = await _git(root, 'rev-parse', 'HEAD')
        _, new = await _git(root, 'rev-parse', 'FETCH_HEAD')
        if old == new:
            return {**info, 'updated': False, 'message': '当前仓库已是最新提交'}
        rc, _ = await _git(root, 'merge-base', '--is-ancestor', 'HEAD', 'FETCH_HEAD', check=False)
        if rc:
            raise RuntimeError('本地与远端不满足快进条件，已停止更新；不会强制覆盖或降级')
        info = await version_info(root)
        if not info['update_supported']:
            raise RuntimeError(info['reason'])
        _, changed = await _git(root, 'diff', '--name-only', old, new)
        path = root / 'config.yaml'
        original = path.read_bytes()
        local = yaml.safe_load(original) or {}
        if not isinstance(local, dict):
            raise RuntimeError('本地 config.yaml 格式错误，未执行更新')
        backup_dir = data_dir / 'updates'; backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        backup = backup_dir / f'config-before-{stamp}.yaml'
        with backup.open('xb') as out:
            os.chmod(backup, 0o600); out.write(original)
        try:
            await _git(root, 'restore', '--', 'config.yaml')
            await _git(root, 'merge', '--ff-only', 'FETCH_HEAD')
            upstream = yaml.safe_load(path.read_bytes()) or {}
            config = _merge_config(upstream, local)
            config.setdefault('app', {})['version'] = (upstream.get('app') or {}).get('version', installed_version(root))
            temp = path.with_suffix('.yaml.tmp')
            temp.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding='utf-8')
            os.replace(temp, path)
        except BaseException:
            path.write_bytes(original)
            raise
        return dict(updated=True, running_version=__version__, installed_version=installed_version(root), commit=new[:12], restart_required=True, dependencies_changed='requirements.txt' in changed.splitlines(), config_backup=backup.name, message='代码已更新，本地配置已保留。请重启服务；启动脚本会检查并补齐依赖。')
