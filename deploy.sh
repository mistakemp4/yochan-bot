#!/usr/bin/env bash
# Run locally, pushes code + media to shiraishi
set -euo pipefail
cd "$(dirname "$0")"

HOST=seal@shiraishi

rsync -av --delete \
    --exclude .venv --exclude .env --exclude HANDOFF.md --exclude __pycache__ --exclude .git \
    ./ "$HOST:yochan-bot/"
ssh "$HOST" 'systemctl --user is-enabled --quiet yochan-bot && systemctl --user restart yochan-bot || echo "not set up yet, run ~/yochan-bot/setup.sh"'
