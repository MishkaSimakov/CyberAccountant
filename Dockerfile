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
COPY server/requirements.txt ./server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt
COPY server/app/ ./server/app/
COPY --from=client-build /app/client/dist/ ./client/dist/
USER 10001:10001
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--app-dir", "server", "--host", "0.0.0.0", "--port", "8000"]

