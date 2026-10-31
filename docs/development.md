# Development

```bash
cd web
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python3 ../mock/demo.py        # http://127.0.0.1:8000, users alice/bob/dana, password demo
.venv/bin/pytest                         # all tests (tests/web)
.venv/bin/ruff check . ../mock ../tests  # lint
```

The demo runs the real app with a fake directory (`mock/directory.py`) and the sample catalog
in `mock/catalog/`. `dana` is a sysadmin (RCO-Admins) and sees the restricted AlphaFold 3
entry. Nothing in `web/apparadise/` imports `mock/`, and no setting turns the demo on.

To run against a real directory, copy `web/.env.default` to `web/.env`, fill in the REQUIRED
values, and start `.venv/bin/python3 app.py` from `web/`.

## Layout

- `web/apparadise/`: `config.py` (settings), `main.py` (`create_app`), `db.py` (schema and
  repositories), `auth.py` + `ldapauth.py` + `ldapca.py` (AD login), `catalog.py` (catalog
  files and `/api/catalog`), `modules.py` (Lmod + Enroot wrappers), `recipes.py` (recipe
  templates).
- `web/static/`: the frontend, served at `/`.
- `tests/web/`: tests. They never touch the network or a real `.env`.
