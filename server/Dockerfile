FROM node:24-alpine AS client-build

WORKDIR /app/client
COPY client/package.json client/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci
COPY client/ ./
RUN npm run build

FROM python:3.13-slim AS server-build

WORKDIR /build
COPY server/pyproject.toml ./
COPY server/app/ ./app/
COPY server/checkier/ ./checkier/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install .

FROM python:3.13-slim AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OCR_WORKERS=1 \
    CLIENT_DIST_DIR=/app/client/dist

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus fonts-dejavu-core supervisor \
    && rm -rf /var/lib/apt/lists/*
COPY --from=server-build /install/ /usr/local/
COPY --from=client-build /app/client/dist/ ./client/dist/
COPY server/workers.conf /etc/supervisor/workers.conf
RUN mkdir -p /app/storage && chown 10001:10001 /app/storage
USER 10001:10001
EXPOSE 8000

CMD ["sh", "-c", "database-init && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]

