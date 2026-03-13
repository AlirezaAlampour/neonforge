#!/usr/bin/env bash
# ============================================================================
# Generate a self-signed SSL certificate for local HTTPS
#
# This is needed because browsers block MediaRecorder (mic) on non-HTTPS
# origins. Two approaches:
#
#   1. (Easiest) Use next dev --experimental-https:
#        npm run dev:https
#
#   2. (Manual) Run this script, then point a reverse proxy at the certs:
#        ./scripts/generate-cert.sh
#
#   3. (Chrome flag) Add your DGX IP to:
#        chrome://flags/#unsafely-treat-insecure-origin-as-secure
#        e.g.  http://192.168.1.100:3000
#        Then relaunch Chrome.
# ============================================================================

set -euo pipefail

CERT_DIR="$(cd "$(dirname "$0")/.." && pwd)/certs"
mkdir -p "$CERT_DIR"

DGX_IP="${1:-}"

SAN="DNS:localhost,IP:127.0.0.1"
if [ -n "$DGX_IP" ]; then
  SAN="${SAN},IP:${DGX_IP}"
  echo "Including DGX IP: $DGX_IP"
fi

openssl req -x509 -newkey rsa:2048 \
  -keyout "$CERT_DIR/key.pem" \
  -out "$CERT_DIR/cert.pem" \
  -days 365 -nodes \
  -subj "/CN=NeonForge Console" \
  -addext "subjectAltName=${SAN}" 2>/dev/null

echo ""
echo "Certificates generated:"
echo "  $CERT_DIR/cert.pem"
echo "  $CERT_DIR/key.pem"
echo ""
echo "Quick options to enable mic recording:"
echo ""
echo "  Option A — Next.js dev (auto-HTTPS):"
echo "    npm run dev:https"
echo ""
echo "  Option B — Chrome flag (easiest for production HTTP):"
echo "    1. Open chrome://flags/#unsafely-treat-insecure-origin-as-secure"
echo "    2. Add: http://<DGX_IP>:3000"
echo "    3. Relaunch Chrome"
