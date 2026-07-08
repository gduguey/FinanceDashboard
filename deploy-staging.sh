#!/bin/bash
# Deploy a branch to the staging stack — a separate clone directory, a
# separate docker-compose project, on the same VM as production.
# Usage: ./deploy-staging.sh <ssh-host> [branch]
# Example: ./deploy-staging.sh oci-finance-dashboard gduguey/some-feature
#          (branch defaults to whatever's currently checked out if omitted)

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <ssh-host> [branch]"
    exit 1
fi

HOST="$1"
BRANCH="${2:-}"
REMOTE_DIR="~/FinanceDashboard-staging"
REPO_URL="https://github.com/gduguey/FinanceDashboard.git"

echo "==> Deploying to staging on $HOST"
ssh "$HOST" "
    set -e
    if [ ! -d $REMOTE_DIR/.git ]; then
        echo 'No staging clone yet — cloning fresh.'
        git clone ${BRANCH:+-b $BRANCH} $REPO_URL $REMOTE_DIR
    else
        cd $REMOTE_DIR
        git fetch origin
        ${BRANCH:+git checkout $BRANCH}
        git pull
    fi
    cd $REMOTE_DIR
    if [ ! -f .env.staging ]; then
        echo 'No .env.staging found on the VM yet.'
        echo 'Copy .env.staging.example to .env.staging and fill in real values first — see docs/server-setup/staging.md.'
        exit 1
    fi
    docker compose -p staging -f docker-compose.staging.yml up -d --build
    docker image prune -f
"
echo "==> Done. Tail logs with: ssh $HOST 'cd $REMOTE_DIR && docker compose -p staging -f docker-compose.staging.yml logs -f'"
