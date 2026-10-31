"""Repo-root entry point, so a plain `appmo add apparadise <repo>` deploys the web app only.

appmotel deploys the repo root unless given a subfolder URL. Without this file it would
find zensical.toml and serve the docs site instead. With it, appmotel installs the root
requirements.txt (which is just web/requirements.txt: never the builder's) and runs
`.venv/bin/python3 app.py` here. This file then runs web/app.py from inside web/, exactly
as a subfolder deploy of web/ would, so relative settings such as CATALOG_DIR=../catalog
and DATA_DIR=data mean the same thing either way.

The builder is never installed this way: deploy it as its own app from builder/
(docs/operations/builders.md).
"""

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"

if __name__ == "__main__":
    # appmotel links the app's .env here, at the repo root. systemd already exports it
    # (EnvironmentFile); loading it too, without override, covers a manual start.
    if (ROOT / ".env").is_file():
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    os.chdir(WEB)
    sys.path.insert(0, str(WEB))
    sys.argv[0] = str(WEB / "app.py")
    runpy.run_path(str(WEB / "app.py"), run_name="__main__")
