"""Disposable UI acceptance server. Never loads personal settings or a real bot."""

from pathlib import Path
import secrets
import argparse
import yaml
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from firefly import config as settings

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--runtime", type=Path, help="Reuse a previously created disposable UI database")
args = parser.parse_args()
runtime = args.runtime.resolve() if args.runtime else Path(tempfile.mkdtemp(prefix="firefly-ui-"))
if args.runtime and (not runtime.name.startswith("firefly-ui-") or not (runtime / "config.yaml").is_file()):
    raise SystemExit("Only an existing disposable firefly-ui-* directory can be reused")
cfg = settings.Config(
    server={"host": "127.0.0.1", "port": 2526},
    database={"url": "sqlite+aiosqlite:///" + str(runtime / "test.db")},
    auth={
        "username": "admin",
        "password": "UiAcceptance2026!",
        "secret_key": secrets.token_urlsafe(32),
        "allow_default_password": True,
    },
    llm={"enabled": False},
    push={"enabled": False},
    alert={"enabled": False},
    subscription={"enabled": False},
    onebot={"access_token": secrets.token_urlsafe(32)},
)
if args.runtime:
    previous = yaml.safe_load((runtime / "config.yaml").read_text())
    # The generated file may contain only auth bootstrap fields.
    # Keep server/database/OneBot isolation from the defaults above.
    cfg = settings.Config.model_validate({**cfg.model_dump(), "auth": {**cfg.auth.model_dump(), **previous.get("auth", {})}})
settings.CONFIG_PATH = runtime / "config.yaml"
settings.ENV_PATH = runtime / "unused.env"
settings.DATA_DIR = runtime
settings._CONFIG_CACHE = cfg
from firefly.app import app as app

if __name__ == "__main__":
    import nonebot

    nonebot.run()
