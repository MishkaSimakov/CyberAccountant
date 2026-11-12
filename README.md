# Cyber Accountant

- [TASK.md](docs/TASK.md) - описание задачи.
- [DESIGN.md](docs/DESIGN.md) - описание дизайна системы.
- [OCR.md](docs/OCR.md) - локальная конвертация документов в Markdown и координаты символов.

## Local development start

```sh
# From the project root on Linux/WSL; requires Python 3.13+, Node.js 24+ and Docker.
# Install OCR system dependencies (Debian/Ubuntu).
sudo apt install tesseract-ocr tesseract-ocr-rus fonts-dejavu-core

# Create the server environment.
python3 -m venv server/.venv

# Install server and development dependencies.
server/.venv/bin/python -m pip install -e './server[dev]'

# Install frontend dependencies.
npm --prefix client ci

# Terminal 1: build and start the Office development container on localhost:8001.
docker build -t cyberaccountant-office ./office_sandbox
docker run --rm --init --name office-dev \
  -p 127.0.0.1:8001:8001 \
  cyberaccountant-office

# Terminal 2: start the API at http://localhost:8000 with automatic reload.
OFFICE_SANDBOX_URL=http://127.0.0.1:8001 server/.venv/bin/python -m uvicorn server.app.main:app --reload

# Terminal 3: start the frontend and open the URL printed by Vite.
npm --prefix client run dev
```

## Docker start

```sh
# From the project root: build and start both services at http://localhost:8000.
docker compose up --build

# In another terminal: stop the services and remove their containers.
docker compose down
```
