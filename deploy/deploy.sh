#!/bin/bash
# Deploy the current main branch to the VM and restart the stack.
# Usage: ./deploy/deploy.sh <ssh-host>
# Example: ./deploy/deploy.sh oci-finance-dashboard   (using an entry in ~/.ssh/config)

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <ssh-host>"
    exit 1
fi

HOST="$1"
REMOTE_DIR='$HOME/FinanceDashboard'

echo "==> Deploying to $HOST"
ssh "$HOST" "
    set -euo pipefail
    cd $REMOTE_DIR
    git checkout main
    git pull --ff-only origin main
    VERSION=\$(grep -m1 '^version = ' pyproject.toml | sed -E 's/version = \"(.*)\"/\1/')
    if [ -z \"\$VERSION\" ]; then
        echo \"::error:: could not read version from pyproject.toml — aborting rather than deploying the docker-compose.yml 'dev' fallback tag\"
        exit 1
    fi
    # docker-compose.yml's build.args reads this from the shell env, not
    # from .env.docker directly (Compose's own interpolation never reads a
    # file that isn't literally named .env) — so pull it out here instead.
    VITE_CLERK_PUBLISHABLE_KEY=\$(grep -m1 '^CLERK_PUBLISHABLE_KEY=' .env.docker | cut -d= -f2-)
    if [ -z \"\$VITE_CLERK_PUBLISHABLE_KEY\" ]; then
        echo \"::error:: CLERK_PUBLISHABLE_KEY missing from .env.docker — aborting rather than building a frontend with no Clerk key\"
        exit 1
    fi
    echo \"==> Building image tagged \$VERSION\"
    APP_VERSION=\"\$VERSION\" VITE_CLERK_PUBLISHABLE_KEY=\"\$VITE_CLERK_PUBLISHABLE_KEY\" docker compose -f deploy/docker-compose.yml up -d --build
    # Host-wide prune: this VM only ever runs this one app's stack, so
    # cleaning up every dangling image on the host is safe here — not a
    # shared/multi-tenant machine where that would risk another stack's cache.
    docker image prune -f
"
echo "==> Done. Tail logs with: ssh $HOST 'cd $REMOTE_DIR && docker compose -f deploy/docker-compose.yml logs -f'"
