# Local docker-compose image. Railway uses the repository-root Dockerfile.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    MEDIA_ROOT=/app/media

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq5 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt

COPY . ./
RUN mkdir -p /app/media /app/staticfiles \
    && DJANGO_DEBUG=False \
       DJANGO_SECRET_KEY=build-only-not-a-runtime-secret \
       DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 \
       DATABASE_URL=postgresql://build:build@127.0.0.1:5432/build \
       python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout ${GUNICORN_TIMEOUT:-120} --access-logfile - --error-logfile -"]
