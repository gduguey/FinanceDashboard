#!/bin/bash
# Wipe staging back to a clean slate — deletes its Postgres data, Caddy
# certs, and bind-mounted data/ folder. Never touches production; entirely
# scoped to the `staging` compose project and its own directory.
# Usage: ./deploy/reset-staging.sh <ssh-host>

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <ssh-host>"
    exit 1
fi

HOST="$1"
REMOTE_DIR='$HOME/FinanceDashboard-staging'

echo "==> Wiping staging on $HOST"
ssh "$HOST" "
    set -e
    cd $REMOTE_DIR
    # Validate the Clerk key BEFORE anything destructive: the rebuild's frontend
    # Dockerfile hard-fails without it (docker-compose.staging.yml's build.args
    # read it from the shell env, not .env.staging), so bail now rather than
    # wiping staging and only then discovering the rebuild can't succeed.
    VITE_CLERK_PUBLISHABLE_KEY=\$(grep -m1 '^CLERK_PUBLISHABLE_KEY=' .env.staging | cut -d= -f2-)
    if [ -z \"\$VITE_CLERK_PUBLISHABLE_KEY\" ]; then
        echo '::error:: CLERK_PUBLISHABLE_KEY missing from .env.staging — aborting before wiping staging'
        exit 1
    fi
    VITE_LANDING_PAGE=\$(grep -m1 '^LANDING_PAGE=' .env.staging | cut -d= -f2- || true)
    docker compose -p staging -f deploy/docker-compose.staging.yml down -v
    rm -rf ./data
    mkdir -p ./data
    VITE_CLERK_PUBLISHABLE_KEY=\"\$VITE_CLERK_PUBLISHABLE_KEY\" VITE_LANDING_PAGE=\"\$VITE_LANDING_PAGE\" docker compose -p staging -f deploy/docker-compose.staging.yml up -d --build
"
echo "==> Staging reset to a clean slate. Create a test account through the UI to reseed."
