# Cyber Accountant

## Run with Docker

Requires Docker with its daemon running. Build and start from the project root:

```sh
docker build -t cyber-accountant .
docker run --rm --name cyber-accountant -p 8000:8000 cyber-accountant
```

Open <http://localhost:8000>. The starter page checks the server connection.
The REST API health check is at <http://localhost:8000/api/health>, and interactive
API documentation is at <http://localhost:8000/docs>.

## Local development

Requires Python 3.13+ and Node.js 24 LTS with npm. Run these commands from the
project root in two separate terminals.

Server:

```sh
python3 -m venv server/.venv
source server/.venv/bin/activate
python -m pip install -r server/requirements.txt
uvicorn app.main:app --app-dir server --reload
```

Client:

```sh
cd client
npm ci
npm run dev
```

Open the URL printed by Vite. Vite proxies `/api` requests to the server at `127.0.0.1:8000`.

To type-check and build the client:

```sh
cd client
npm run build
```
