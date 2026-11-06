#!/usr/bin/env python3
"""Create a local-development .env file (random secret key, database URL) and print the SQL to create the database.

    python scripts/make_env.py                      # random database password, database "lifeos"
    python scripts/make_env.py --db-password 'pw'   # choose your own

Never overwrites an existing .env unless you pass --force. The generated file is for LOCAL development only;
production settings are described in docs/DEPLOYMENT.md.
"""
import argparse
import secrets
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent


def build(db_name, db_user, db_password, host, port):
    secret = secrets.token_urlsafe(50)
    url = f"postgres://{quote(db_user, safe='')}:{quote(db_password, safe='')}@{host}:{port}/{quote(db_name, safe='')}"
    env = (f"DJANGO_SETTINGS_MODULE=config.settings.dev\nDJANGO_SECRET_KEY={secret}\nDATABASE_URL={url}\n"
           "LIFEOS_SITE_NAME=LifeOS\nLIFEOS_SITE_URL=http://127.0.0.1:8000\n")
    sql = (f"CREATE ROLE {db_user} WITH LOGIN PASSWORD '{db_password.replace(chr(39), chr(39) * 2)}' CREATEDB;\n"
           f"CREATE DATABASE {db_name} OWNER {db_user};")
    return env, sql


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db-name", default="lifeos")
    p.add_argument("--db-user", default="lifeos")
    p.add_argument("--db-password", default=None, help="default: a random password")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", default="5432")
    p.add_argument("--path", default=str(ROOT / ".env"), help="where to write the file (default: ./.env)")
    p.add_argument("--force", action="store_true", help="overwrite an existing file")
    a = p.parse_args(argv)
    for label, value in (("--db-name", a.db_name), ("--db-user", a.db_user)):
        if not value.replace("_", "").isalnum():
            sys.exit(f"{label} may contain only letters, digits and underscores.")
    target = Path(a.path)
    if target.exists() and not a.force:
        sys.exit(f"{target} already exists; not overwriting it (use --force to replace it).")
    password = a.db_password or secrets.token_urlsafe(12)
    env, sql = build(a.db_name, a.db_user, password, a.host, a.port)
    target.write_text(env)
    try:
        target.chmod(0o600)                     # owner-only where the OS supports it
    except OSError:
        pass
    print(f"Wrote {target}\n\nNow create the database. In psql (or pgAdmin's query tool) run:\n\n{sql}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
