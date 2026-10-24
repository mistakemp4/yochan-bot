#!/usr/bin/env bash
# Run locally, pushes code + media to shiraishi
set -euo pipefail
cd "$(dirname "$0")"

HOST=seal@shiraishi

rsync -av --delete \
    --exclude .venv --exclude .env --exclude state.json --exclude state.tmp --exclude HANDOFF.md --exclude README.md --exclude docs --exclude __pycache__ --exclude .git \
    ./ "$HOST:yochan-bot/"
# try-restart only restarts if already running
ssh "$HOST" 'systemctl --user is-enabled --quiet yochan-bot && systemctl --user try-restart yochan-bot || echo "not set up yet, run ~/yochan-bot/setup.sh"'
