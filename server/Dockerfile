FROM node:24-alpine AS client-build

WORKDIR /app/client
COPY client/package.json client/package-lock.json ./
RUN npm ci
COPY client/ ./
RUN npm run build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY server/pyproject.toml ./server/pyproject.toml
COPY server/app/ ./server/app/
RUN pip install --no-cache-dir ./server
COPY --from=client-build /app/client/dist/ ./client/dist/
USER 10001:10001
EXPOSE 8000

CMD ["sh", "-c", "database-init && exec uvicorn app.main:app --app-dir server --host 0.0.0.0 --port 8000"]

