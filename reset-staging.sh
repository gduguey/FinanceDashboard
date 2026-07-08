#!/bin/bash
# Wipe staging back to a clean slate — deletes its Postgres data, Caddy
# certs, and bind-mounted data/ folder. Never touches production; entirely
# scoped to the `staging` compose project and its own directory.
# Usage: ./reset-staging.sh <ssh-host>

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <ssh-host>"
    exit 1
fi

HOST="$1"
REMOTE_DIR="~/FinanceDashboard-staging"

echo "==> Wiping staging on $HOST"
ssh "$HOST" "
    set -e
    cd $REMOTE_DIR
    docker compose -p staging -f docker-compose.staging.yml down -v
    rm -rf ./data
    mkdir -p ./data
    docker compose -p staging -f docker-compose.staging.yml up -d --build
"
echo "==> Staging reset to a clean slate. Create a test account through the UI to reseed."
