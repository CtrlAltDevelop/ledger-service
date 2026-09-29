# syntax=docker/dockerfile:1
FROM python:3.14-slim AS runtime

# WEB_CONCURRENCY is gunicorn's worker count.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WEB_CONCURRENCY=4

WORKDIR /app

# Dependencies first, so a code change does not reinstall them. The file is
# hash-pinned by `uv export`, and pip refuses anything that does not match.
COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt

RUN useradd --system --uid 10001 --home-dir /app ledger
COPY --chown=ledger:ledger manage.py ./
COPY --chown=ledger:ledger config ./config
COPY --chown=ledger:ledger ledger ./ledger

USER ledger
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--access-logfile", "-"]
