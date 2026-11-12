# Cyber Accountant

- [TASK.md](docs/TASK.md) - описание задачи.
- [DESIGN.md](docs/DESIGN.md) - описание дизайна системы.
- [OCR.md](docs/OCR.md) - локальная конвертация документов в Markdown и координаты символов.

## Development

Only Docker Compose is required on the host. From the project root:

```sh
# Optional: create .env for model API credentials, or keep your existing file.
cp -n .env.example .env

# Start the API, Vite, PostgreSQL, Redis and the Office sandbox.
docker compose -f compose.yaml -f compose.dev.yaml up --build

# Stop the development containers.
docker compose -f compose.yaml -f compose.dev.yaml down
```

Open http://localhost:5173 for the frontend; the API is available at http://localhost:8000.
`compose.dev.yaml` overrides the server configuration and adds Vite. PostgreSQL, Redis and the Office sandbox
are inherited from `compose.yaml`, so always pass both files for development.

Both development containers bind-mount the workdir at `/app`. Python changes restart Uvicorn;
frontend changes use Vite hot reload. Polling is enabled for Docker Desktop/WSL bind mounts.
Vite proxies `/api` to the `server` container. Python dependencies are installed in the image;
frontend dependencies live in a separate `client_node_modules` Docker volume.

After changing Python dependencies, rebuild with the startup command above. After changing
`client/package.json` or its lockfile, refresh the frontend dependency volume:

```sh
docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps client npm ci
```

`.env` is passed to the API if present. Compose overrides `DATABASE_URL`, `REDIS_URL` and `OFFICE_SANDBOX_URL`
to use its own services, so local PostgreSQL is not needed. Tables are initialized automatically
from the models before the API starts. The development project uses its own database volume.
Stop one mode before starting the other because both publish API port 8000.

## Database

The database layer uses SQLAlchemy and Psycopg 3. Its four tables follow `docs/DESIGN.md`.
`database-init` calls `Base.metadata.create_all()`: it creates missing tables but does not alter existing
tables when models change. Changes to an existing schema need explicit SQL or a database recreation
when its development data can be discarded.
Use `app.database.session_scope()` for a short transaction in a worker, or `get_session` as a
dependency in synchronous FastAPI handlers. Transactions commit on success and roll back on failure;
keep OCR and LLM requests outside them. API routes currently still use the in-memory demonstration stores;
adding this database layer does not migrate those routes or their existing data.

With the development stack running, execute database integration tests inside the API container.
They create/drop only a temporary schema:

```sh
docker compose -f compose.yaml -f compose.dev.yaml exec server \
  sh -c 'TEST_DATABASE_URL="$DATABASE_URL" python -m pytest server/tests/test_database.py'
```

## Redis

Both environments include Redis for RQ. The API receives `REDIS_URL=redis://redis:6379/0`;
workers on the same Compose network should use that URL too. Redis is available inside Docker
without publishing port 6379 to the host. Append-only persistence stores queue data in the
`redis_data` volume, which survives container replacement and `docker compose down`.
The API waits for Redis to become healthy before starting.

Check the development Redis service:

```sh
docker compose -f compose.yaml -f compose.dev.yaml exec redis redis-cli ping
# PONG
```

## Docker production

```sh
# From the project root: build and start the API, Office sandbox, PostgreSQL and Redis.
# The application is available at http://localhost:8000.
docker compose up --build

# In another terminal: stop the services and remove their containers.
docker compose down
```

Compose waits for PostgreSQL to become healthy, then the server runs `database-init` before starting the API.
The production image contains the installed API, runtime dependencies and built frontend assets;
it has no source bind mounts, Node.js, development dependencies or Python package build output.
The container connects to PostgreSQL using the internal service name `postgres`; it does not use the local
database or publish port 5432, so both can run together. Database data is stored in the named
`postgres_data` volume and survives `docker compose down`. The Compose credentials are for local development.
