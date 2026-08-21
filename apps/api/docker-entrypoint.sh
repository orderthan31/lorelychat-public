#!/bin/sh
set -eu

if [ "${LORECHAT_SEED_DEMO:-true}" = "true" ] && [ ! -f /data/.demo-seeded ]; then
  python -m scripts.seed_demo
  touch /data/.demo-seeded
fi

exec "$@"
