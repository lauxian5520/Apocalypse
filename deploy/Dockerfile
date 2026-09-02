# Build context is the repository root (see docker-compose.yml).
FROM python:3.11-slim

WORKDIR /app

# gcc/libpq/libjpeg: building Pillow and psycopg2 wheels.
# fonts-dejavu-core: python:*-slim ships no fonts, so the captcha would fall
# back to Pillow's built-in bitmap face and render unreadably.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev libffi-dev libjpeg-dev fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Code only. Runtime state arrives through the /app/var volume.
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# The harness can run commands on behalf of a model, so the process must not be
# root. /app/var is the one writable path; it is a volume, and the uid below
# has to own it on the host too (see README).
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/var \
    && chown -R app:app /app
USER app

WORKDIR /app/backend
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
