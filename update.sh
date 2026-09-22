#!/bin/bash
# Pulls the latest boat-dashboard code and restarts the systemd service that's already
# running it. Run this from an SSH session on the Pi itself:
#   ./update.sh        (after a one-time: chmod +x update.sh)
#   bash update.sh      (works either way, no chmod needed)
set -e
cd "$(dirname "$0")"

echo "Pulling latest code..."
git pull

echo "Restarting the boat-dashboard service..."
sudo systemctl restart boat-dashboard

sleep 1
sudo systemctl status boat-dashboard --no-pager
