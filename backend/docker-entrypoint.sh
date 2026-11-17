#!/bin/sh
set -eu

mkdir -p "${MEDIA_ROOT:-/app/media}" /app/staticfiles

# Apply pending schema changes before serving. PostgreSQL locks this, so two
# replicas starting together cannot apply the same migration twice.
# Set RUN_MIGRATE_ON_START=false only when a separate job owns migrate.
case "${RUN_MIGRATE_ON_START:-true}" in
  false|0|no|off) ;;
  *) python manage.py migrate --noinput ;;
esac

if [ "${RUN_SEED_ON_START:-false}" = "true" ] || [ "${RUN_SEED_ON_START:-0}" = "1" ]; then
  python manage.py seed_roles
  python manage.py seed_notification_templates || true
  python manage.py seed_workflows || true
fi

# Always ensure channels + default Self tariff per centre (idempotent; safe after seed_roles).
python manage.py seed_channel_tariffs || true

exec "$@"
