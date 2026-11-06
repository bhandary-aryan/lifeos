# Getting started on your own computer

This runs LifeOS **only on your machine** (http://127.0.0.1:8000). Your data lives in a PostgreSQL database on your computer and in the `media/` folder (uploaded pictures). Nothing is sent anywhere.

You need: **Python 3.12** and **PostgreSQL 16** (developed and tested with exactly these). Node.js is *not* needed: the compiled CSS and JavaScript libraries are included.

Commands below use `python`. On macOS/Linux you may need `python3`; on Windows `py` also works. Run everything from the project folder (the one containing `manage.py`).

## 1. Install the prerequisites (once)

**Python 3.12** — https://www.python.org/downloads/ (Windows: tick **"Add python.exe to PATH"** in the installer). Check: `python --version`.

**PostgreSQL 16** — pick one:
- **Windows:** installer from https://www.postgresql.org/download/windows/ . Remember the password you set for the `postgres` user. Keep the default port 5432. Afterwards open **"SQL Shell (psql)"** from the Start menu (press Enter through the prompts and type your password).
- **macOS:** `brew install postgresql@16 && brew services start postgresql@16`, then `psql postgres`.
- **Ubuntu/Debian:** `sudo apt install postgresql libpq-dev`, then `sudo -u postgres psql`.
- **Docker (any OS), if you prefer:** `docker run --name lifeos-db -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d postgres:16`, then connect with `psql -h localhost -U postgres`. (Not tested in the environment this was built in.)

## 2. Unzip and open a terminal in the folder
Unzip `lifeos.zip`, then `cd lifeos` (Windows: open the folder, then *File → Open PowerShell here*).

## 3. Create the Python environment and install packages
macOS / Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```
Windows (PowerShell):
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process Bypass   (then run it again)
pip install -r requirements-dev.txt
```
You should see `(.venv)` at the start of your prompt. You must activate it again in every new terminal.

## 4. Create your settings file
```bash
python scripts/make_env.py
```
This writes `.env` (a random secret key and a random database password) and **prints two SQL lines**. Keep that output open for the next step. (Never share or commit `.env`.)

## 5. Create the database
In the `psql` window from step 1 (as the `postgres` user), paste the two lines the script printed. They look like:
```sql
CREATE ROLE lifeos WITH LOGIN PASSWORD '…' CREATEDB;
CREATE DATABASE lifeos OWNER lifeos;
```
Then type `\q` to leave psql. (`CREATEDB` lets the automated tests create a throw-away test database.)

## 6. Create the tables and start the app
```bash
python manage.py migrate
python manage.py runserver
```
Open **http://127.0.0.1:8000** in your browser. Click **Create an account** (this is *your* account; the email can be anything), finish or skip the short setup, and you're in. Stop the server with **Ctrl+C**.

## 7. Optional extras
- **See it full of sample data first:** `python manage.py seed_demo` prints a demo login (everything in it is labelled `[Demo]`). It only runs in development mode.
- **Admin site** (maintenance only): `python manage.py createsuperuser`, then http://127.0.0.1:8000/admin/ .
- **Run the tests:** `python -m pytest` (about 5–8 minutes; needs the `CREATEDB` permission from step 5).
- **Password-reset and reminder emails** are printed in the terminal where the server runs (no real email is sent in development).

## 8. Everyday use
1. Make sure PostgreSQL is running (it normally starts with your computer).
2. Open a terminal in the folder, activate the environment (`source .venv/bin/activate` / `.venv\Scripts\Activate.ps1`).
3. `python manage.py runserver`, open http://127.0.0.1:8000 .
After updating the code, run `python manage.py migrate` again.

## 9. Back up your data
Everything is in two places: the `lifeos` database and the `media/` folder.
```bash
pg_dump -U lifeos -h localhost lifeos > lifeos-backup.sql        # restore with: psql -U lifeos -h localhost lifeos < lifeos-backup.sql
```
Also copy `media/`. Settings → *Export* gives you a JSON/CSV copy of your data inside the app too.

## 10. If something goes wrong
| Symptom | Fix |
|---|---|
| `connection refused` / `could not connect to server` | PostgreSQL isn't running. Start it (Windows: Services → postgresql-x64-16; macOS: `brew services start postgresql@16`; Linux: `sudo systemctl start postgresql`). |
| `password authentication failed for user "lifeos"` | The password in `.env` doesn't match the one in `CREATE ROLE`. Fix with `ALTER ROLE lifeos WITH PASSWORD 'new';` and update `DATABASE_URL`, or delete `.env` and redo steps 4–5. |
| `database "lifeos" does not exist` / `role "lifeos" does not exist` | Step 5 wasn't run (or was run as the wrong user). Run it in psql as `postgres`. |
| `relation … does not exist` / `no such table` | Run `python manage.py migrate`. |
| `ModuleNotFoundError` / `No module named django` | The virtual environment isn't active. Activate it (step 3). |
| `Activate.ps1 cannot be loaded` (Windows) | `Set-ExecutionPolicy -Scope Process Bypass`, then activate again. |
| `Error: That port is already in use` | `python manage.py runserver 8001` and open that port. |
| `DisallowedHost` | Open exactly `http://127.0.0.1:8000` or `http://localhost:8000`. |
| Page looks unstyled | Hard-refresh (Ctrl/Cmd+Shift+R). If you changed templates: `npm install && npm run build:css`. |
| `permission denied to create database` when testing | Re-run step 5's role with `CREATEDB`: `ALTER ROLE lifeos CREATEDB;`. |

## 11. Putting it on the internet (not needed for personal use)
Don't just run `runserver` on a public address. Follow [DEPLOYMENT.md](DEPLOYMENT.md) and [SECURITY.md](SECURITY.md) (HTTPS, production settings, backups, a real email provider).
