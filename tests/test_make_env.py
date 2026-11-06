import subprocess
import sys
from pathlib import Path

import environ

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "make_env.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def test_creates_a_usable_env_file_and_prints_matching_sql(tmp_path):
    target = tmp_path / ".env"
    r = run("--path", str(target), "--db-password", "p@ss/w:rd'1")
    assert r.returncode == 0
    values = environ.Env.read_env(str(target)) or {}
    env = environ.Env()
    environ.Env.read_env(str(target), overwrite=True)
    cfg = env.db("DATABASE_URL")
    assert cfg["USER"] == "lifeos" and cfg["NAME"] == "lifeos" and cfg["HOST"] == "localhost" and cfg["PORT"] == 5432
    assert cfg["PASSWORD"] == "p@ss/w:rd'1"                            # special characters survive URL quoting
    assert "CREATE ROLE lifeos WITH LOGIN PASSWORD 'p@ss/w:rd''1' CREATEDB;" in r.stdout and "CREATE DATABASE lifeos OWNER lifeos;" in r.stdout
    text = target.read_text()
    assert "DJANGO_SETTINGS_MODULE=config.settings.dev" in text and len(env("DJANGO_SECRET_KEY")) >= 50
    del values


def test_refuses_to_overwrite_without_force_and_secret_keys_differ(tmp_path):
    target = tmp_path / ".env"
    assert run("--path", str(target)).returncode == 0
    first = target.read_text()
    again = run("--path", str(target))
    assert again.returncode != 0 and "not overwriting" in again.stderr and target.read_text() == first
    assert run("--path", str(target), "--force").returncode == 0 and target.read_text() != first


def test_rejects_unsafe_names(tmp_path):
    r = run("--path", str(tmp_path / ".env"), "--db-name", "x; DROP TABLE users")
    assert r.returncode != 0 and not (tmp_path / ".env").exists()
