FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/work/home \
    SAL_USE_VCLPLUGIN=gen

RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-calc libreoffice-writer python3 python3-uno \
      fonts-dejavu-core fonts-liberation fonts-crosextra-carlito \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app /input /work \
    && chmod 1777 /work

COPY office_worker.py /app/office_worker.py
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/usr/bin/python3", "/app/office_worker.py"]
