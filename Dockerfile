FROM python:3.12-slim

# Chromium turns letters into print-ready PDFs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
ENV CHROME=/usr/bin/chromium \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1

WORKDIR /srv
COPY . .
RUN pip install --no-cache-dir ".[postgres,s3,monitoring]"

EXPOSE 8000
# Apply database migrations, then start the web app.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.web:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
