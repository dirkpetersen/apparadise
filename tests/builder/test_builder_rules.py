import ast
from pathlib import Path

from dotenv import dotenv_values

from apparadise_builder.config import Settings

ROOT = Path(__file__).resolve().parents[2] / "builder"
APPMOTEL_ONLY = {"memory_limit"}


def test_env_default_matches_settings():
    keys = {k.lower() for k in dotenv_values(ROOT / ".env.default")}
    assert keys - APPMOTEL_ONLY == set(Settings.model_fields)


def test_required_are_empty_in_env_default():
    values = dotenv_values(ROOT / ".env.default")
    for name, f in Settings.model_fields.items():
        if f.is_required():
            assert values[name.upper()] == "", name


def test_builder_never_imports_web_or_mock():
    for f in list(ROOT.rglob("*.py")):
        if ".venv" in f.parts:
            continue
        for node in ast.walk(ast.parse(f.read_text())):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            for m in mods:
                assert m.split(".")[0] not in {"apparadise", "mock"}, (f, m)
