#!/bin/bash
# Deploy a branch to the staging stack — a separate clone directory, a
# separate docker-compose project, on the same VM as production.
# Usage: ./deploy/deploy-staging.sh <ssh-host> [branch]
# Example: ./deploy/deploy-staging.sh oci-finance-dashboard gduguey/some-feature
#          (branch defaults to whatever's currently checked out if omitted)

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <ssh-host> [branch]"
    exit 1
fi

HOST="$1"
BRANCH="${2:-}"
REMOTE_DIR='$HOME/FinanceDashboard-staging'
REPO_URL="https://github.com/gduguey/FinanceDashboard.git"

# BRANCH is interpolated directly into the remote shell command string below
# (not passed as a separate argument), so it must be restricted to safe git
# ref characters before that happens — otherwise a value containing shell
# metacharacters would be re-parsed and executed by the remote shell.
if [ -n "$BRANCH" ] && ! [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
    echo "Error: branch name '$BRANCH' contains characters not allowed in a git ref" >&2
    exit 1
fi

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
    VERSION=\$(grep -m1 '^version = ' pyproject.toml | sed -E 's/version = \"(.*)\"/\1/')
    # Same gotcha and same fix as deploy.sh: docker-compose.staging.yml's
    # build.args reads this from the shell env, not from .env.staging
    # directly, since Compose's own interpolation never reads a file that
    # isn't literally named .env.
    VITE_CLERK_PUBLISHABLE_KEY=\$(grep -m1 '^CLERK_PUBLISHABLE_KEY=' .env.staging | cut -d= -f2-)
    if [ -z \"\$VITE_CLERK_PUBLISHABLE_KEY\" ]; then
        echo \"::error:: CLERK_PUBLISHABLE_KEY missing from .env.staging — aborting rather than building a frontend with no Clerk key\"
        exit 1
    fi
    echo \"==> Building image tagged \$VERSION\"
    APP_VERSION=\"\$VERSION\" VITE_CLERK_PUBLISHABLE_KEY=\"\$VITE_CLERK_PUBLISHABLE_KEY\" docker compose -p staging -f deploy/docker-compose.staging.yml up -d --build
    docker image prune -f
"
echo "==> Done. Tail logs with: ssh $HOST 'cd $REMOTE_DIR && docker compose -p staging -f deploy/docker-compose.staging.yml logs -f'"
