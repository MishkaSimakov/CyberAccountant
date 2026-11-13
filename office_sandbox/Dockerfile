FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/work/home \
    OFFICE_WORK_DIR=/work \
    SAL_USE_VCLPLUGIN=svp \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-calc libreoffice-writer python3 python3-uno python3-venv \
        fonts-dejavu-core fonts-liberation fonts-crosextra-carlito \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv --system-site-packages /opt/venv \
    && mkdir -p /app /work \
    && chmod 1777 /work

WORKDIR /app
COPY pyproject.toml office_worker.py service.py ./
RUN --mount=type=cache,target=/work/home/.cache/pip \
    pip install .
USER 10001:10001
EXPOSE 8001
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8001"]
