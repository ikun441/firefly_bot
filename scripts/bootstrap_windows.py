"""Windows launcher: stdlib-only, executable by an older bootstrap Python too."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / 'firefly_env'
LOG = ROOT / 'logs' / 'startup.log'

def compatible(command):
    try:
        return subprocess.run(command + ['-c', 'import sys;sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

def choose_python():
    candidates = []
    if os.name == 'nt' and shutil.which('py'):
        candidates += [['py', '-3.' + version] for version in ('11', '12', '10')]
    candidates.append([sys.executable])
    candidates += [[name] for name in ('python3.11', 'python3.12', 'python3.10', 'python') if shutil.which(name)]
    for parent in (Path.home(), Path(os.environ.get('LOCALAPPDATA', str(Path.home())))):
        for name in ('miniforge3', 'miniconda3', 'anaconda3'):
            base = parent / name
            candidates.append([str(base / 'python.exe')])
            candidates += [[str(env / 'python.exe')] for env in (base / 'envs').glob('*')]
    for command in candidates:
        if compatible(command): return command
    raise RuntimeError('Python 3.10-3.12 is required. Install Python 3.11 and enable Add Python to PATH.')

def run(command, required=True):
    # Keep both console output and a log. The .cmd wrapper retains the console
    # even if Python exits before this function can run.
    with LOG.open('a', encoding='utf-8') as log:
        proc = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env={**os.environ, 'PYTHONIOENCODING':'utf-8', 'PYTHONUTF8':'1'})
        try:
            for line in proc.stdout:
                print(line, end='', flush=True); log.write(line); log.flush()
            code = proc.wait()
        except KeyboardInterrupt:
            try: proc.wait(timeout=10)
            except subprocess.TimeoutExpired: proc.terminate(); proc.wait()
            return 130
    if code and required: raise RuntimeError('Command failed with exit code %s. See logs/startup.log.' % code)
    return code

def prepare_environment(command):
    python = VENV / 'Scripts' / 'python.exe'
    if not python.exists(): run(command + ['-m', 'venv', str(VENV)])
    if not compatible([str(python)]):
        raise RuntimeError('firefly_env uses an unsupported Python. Rename it and restart with Python 3.11.')
    marker = VENV / '.requirements.sha256'
    digest = hashlib.sha256((ROOT / 'requirements.txt').read_bytes()).hexdigest()
    probe = subprocess.run([str(python), '-c', 'import nonebot,fastapi,sqlmodel,uvicorn,openpyxl,defusedxml,socksio'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    if not marker.exists() or marker.read_text().strip() != digest or probe:
        run([str(python), '-m', 'pip', 'install', '--upgrade', 'pip'], required=False)
        code = run([str(python), '-m', 'pip', 'install', '-r', 'requirements.txt', '-i', 'https://pypi.tuna.tsinghua.edu.cn/simple'], required=False)
        if code: run([str(python), '-m', 'pip', 'install', '-r', 'requirements.txt'])
        marker.write_text(digest, encoding='ascii')
    # Browser download failure should not prevent the accounting API starting.
    probe = subprocess.run([str(python), '-c', 'from pathlib import Path;from playwright.sync_api import sync_playwright;p=sync_playwright().start();ok=Path(p.chromium.executable_path).exists();p.stop();raise SystemExit(0 if ok else 1)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    if probe:
        code = run([str(python), '-m', 'playwright', 'install', 'chromium'], required=False)
        if code: print('WARNING: Chromium unavailable; text/accounting still work. Install it later for report images.')
    return python

def main():
    os.chdir(ROOT)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a', encoding='utf-8') as log: log.write('\n--- Firefly startup ---\n')
    command = choose_python()
    print('Preparing Firefly with a supported Python...')
    python = prepare_environment(command)
    if not (ROOT / '.env').exists() and (ROOT / '.env.example').exists():
        shutil.copyfile(ROOT / '.env.example', ROOT / '.env')
    # Read the configured port, not a hardcoded 2525. YAML stays in Python,
    # outside CMD's parentheses, percent expansion and encoding rules.
    probe = subprocess.run([str(python), '-c', 'from firefly.config import get_config;c=get_config();print(str(c.server.host)+"\\n"+str(c.server.port))'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    if probe.returncode: raise RuntimeError('Invalid config.yaml/.env. Run python -m firefly.app to inspect the configuration.')
    host, port = probe.stdout.strip().splitlines()[-2:]; port = int(port)
    check = socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET)
    try: check.bind((host, port))
    except OSError: raise RuntimeError('Configured port %s is in use. Close the previous service or choose another port.' % port)
    finally: check.close()
    print('Console: http://127.0.0.1:%s/ | log: logs/startup.log' % port)
    return run([str(python), '-m', 'firefly.app'], required=False)

if __name__ == '__main__':
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
    except Exception as exc:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open('a', encoding='utf-8') as log: traceback.print_exc(file=log)
        print('ERROR: %s\nDetails: %s' % (exc, LOG))
        sys.exit(1)
