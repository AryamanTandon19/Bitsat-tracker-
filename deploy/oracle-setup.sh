#!/usr/bin/env bash
# VisionGuard — one-command deploy for a fresh Ubuntu server (Oracle Cloud etc.)
# Run it on the server with:
#   curl -fsSL https://raw.githubusercontent.com/AryamanTandon19/Bitsat-tracker-/claude/society-ai-watchdog-demo-hxshu9/deploy/oracle-setup.sh | bash
set -e

BRANCH="claude/society-ai-watchdog-demo-hxshu9"
REPO="https://github.com/AryamanTandon19/Bitsat-tracker-.git"

echo "==> Installing git + Docker (if needed)…"
sudo apt-get update -y
sudo apt-get install -y git curl
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi

echo "==> Getting the code…"
cd ~
if [ -d visionguard/.git ]; then
  cd visionguard
  git fetch origin "$BRANCH"
  git checkout "$BRANCH"
  git pull origin "$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO" visionguard
  cd visionguard
fi

echo "==> Building the app (first time takes ~10-20 min on a free server)…"
sudo docker build -t visionguard .

echo "==> Starting the app…"
sudo docker rm -f visionguard 2>/dev/null || true
sudo docker run -d --restart unless-stopped -p 80:8000 --name visionguard visionguard

IP=$(curl -s ifconfig.me || echo "<your-server-ip>")
echo ""
echo "======================================================"
echo "  VisionGuard is LIVE.  Open in a browser:"
echo "      http://$IP"
echo "  Login:  YC / 11012235   (or admin / password1101)"
echo "======================================================"
echo "  To see logs:      sudo docker logs -f visionguard"
echo "  To update later:  re-run this same command"
echo "======================================================"
