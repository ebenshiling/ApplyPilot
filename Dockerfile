FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY pyproject.toml README.md LICENSE /app/
COPY src /app/src
COPY scripts /app/scripts
COPY profile.example.json /app/profile.example.json
COPY ops /app/ops

RUN mkdir -p /ms-playwright \
    && pip install --upgrade pip \
    && pip install -e . \
    && pip install --no-deps python-jobspy \
    && pip install pydantic tls-client requests markdownify regex \
    && python -m playwright install --with-deps chromium

RUN mkdir -p /data/workspace /data/multi /transfer

EXPOSE 8765

CMD ["applypilot", "dashboard-serve", "--host", "0.0.0.0", "--port", "8765", "--multi-user"]
