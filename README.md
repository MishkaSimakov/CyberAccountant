# Cyber Accountant

- [TASK.md](docs/TASK.md) - описание задачи.
- [DESIGN.md](docs/DESIGN.md) - описание дизайна системы.
- [OCR.md](docs/OCR.md) - локальная конвертация документов в Markdown и координаты символов.

## Local development start

```sh
# From the project root on Linux/WSL; requires Python 3.13+, Node.js 24+, PostgreSQL and Docker.
# Install OCR system dependencies (Debian/Ubuntu).
sudo apt install tesseract-ocr tesseract-ocr-rus fonts-dejavu-core

# Create the server environment.
python3 -m venv server/.venv

# Install server and development dependencies.
server/.venv/bin/python -m pip install -e './server[dev]'

# Create .env once, or add DATABASE_URL from .env.example to an existing .env.
cp -n .env.example .env

# Install frontend dependencies.
npm --prefix client ci
```

Start your locally installed PostgreSQL before initializing the database or running the API:

```sh
# Linux/WSL (systemd).
sudo systemctl start postgresql

# macOS with Homebrew: find your installed PostgreSQL service and start it.
brew services list
brew services start postgresql@14 # Replace with your installed formula, e.g. postgresql@18.
```

Create the application role and database once. Connect as your local PostgreSQL administrator
(on Linux this is commonly `sudo -u postgres psql`; on Homebrew, `psql postgres`):

```sql
CREATE ROLE cyberaccountant WITH LOGIN PASSWORD 'cyberaccountant';
CREATE DATABASE cyberaccountant OWNER cyberaccountant;
```

The development connection in `.env.example` is:

```dotenv
DATABASE_URL=postgresql+psycopg://cyberaccountant:cyberaccountant@127.0.0.1:5432/cyberaccountant
```

Adjust the credentials, host or port in `.env` for your installation. Create the tables, then start the services:

```sh
# From the project root; safe to run again against an initialized database.
server/.venv/bin/database-init

# Terminal 1: build and start the Office development container on localhost:8001.
docker build -t cyberaccountant-office ./office_sandbox
docker run --rm --init --name office-dev \
  -p 127.0.0.1:8001:8001 \
  cyberaccountant-office

# Terminal 2: start the API at http://localhost:8000 with automatic reload.
OFFICE_SANDBOX_URL=http://127.0.0.1:8001 server/.venv/bin/python -m uvicorn app.main:app --app-dir server --reload --env-file .env

# Terminal 3: start the frontend and open the URL printed by Vite.
npm --prefix client run dev
```

The database layer uses SQLAlchemy and Psycopg 3. Its four tables follow `docs/DESIGN.md`.
`database-init` calls `Base.metadata.create_all()`: it creates missing tables but does not alter existing
tables when models change. Changes to an existing schema need explicit SQL or a database recreation
when its development data can be discarded.
Use `app.database.session_scope()` for a short transaction in a worker, or `get_session` as a
dependency in synchronous FastAPI handlers. Transactions commit on success and roll back on failure;
keep OCR and LLM requests outside them. API routes currently still use the in-memory demonstration stores;
adding this database layer does not migrate those routes or their existing data.

Database integration tests use `TEST_DATABASE_URL` and create/drop only a temporary schema:

```sh
TEST_DATABASE_URL=postgresql+psycopg://cyberaccountant:cyberaccountant@127.0.0.1:5432/cyberaccountant \
  server/.venv/bin/python -m pytest server/tests/test_database.py
```

## Docker start

```sh
# From the project root: build and start the API, Office sandbox and PostgreSQL.
# The application is available at http://localhost:8000.
docker compose up --build

# In another terminal: stop the services and remove their containers.
docker compose down
```

Compose waits for PostgreSQL to become healthy, then the server runs `database-init` before starting the API.
The container connects to PostgreSQL using the internal service name `postgres`; it does not use the local
database or publish port 5432, so both can run together. Database data is stored in the named
`postgres_data` volume and survives `docker compose down`. The Compose credentials are for local development.
