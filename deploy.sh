#!/bin/bash
# Deploy the current main branch to the VM and restart the stack.
# Usage: ./deploy.sh <ssh-host>
# Example: ./deploy.sh oci-finance-dashboard   (using an entry in ~/.ssh/config)

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <ssh-host>"
    exit 1
fi

HOST="$1"
REMOTE_DIR="~/FinanceDashboard"

echo "==> Deploying to $HOST"
ssh "$HOST" "
    set -e
    cd $REMOTE_DIR
    git pull
    docker compose up -d --build
    docker image prune -f
"
echo "==> Done. Tail logs with: ssh $HOST 'cd $REMOTE_DIR && docker compose logs -f'"
