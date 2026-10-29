#!/bin/sh
set -eu

mkdir -p "${MEDIA_ROOT:-/app/media}" /app/staticfiles

# Migrate is preferred via railway.toml preDeployCommand so only one replica
# applies schema changes. Keep an opt-in start-time migrate for single-service boots.
if [ "${RUN_MIGRATE_ON_START:-false}" = "true" ] || [ "${RUN_MIGRATE_ON_START:-0}" = "1" ]; then
  python manage.py migrate --noinput
fi

if [ "${RUN_SEED_ON_START:-false}" = "true" ] || [ "${RUN_SEED_ON_START:-0}" = "1" ]; then
  python manage.py seed_roles
  python manage.py seed_notification_templates || true
  python manage.py seed_workflows || true
fi

exec "$@"
