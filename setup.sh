#!/usr/bin/env bash
# Run on shiraishi after first deploy
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
    echo "DISCORD_TOKEN=" > .env
fi
chmod 600 .env

mkdir -p ~/.config/systemd/user
cp yochan-bot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable yochan-bot
echo "Put the token in ~/yochan-bot/.env, then: systemctl --user start yochan-bot"
