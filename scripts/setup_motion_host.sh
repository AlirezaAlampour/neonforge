#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
uv lock --check --offline
bridge_ipv4="$(docker network inspect ai-net --format '{{(index .IPAM.Config 0).Gateway}}')"
if [[ ! "$bridge_ipv4" =~ ^172\.([1][6-9]|2[0-9]|3[0-1])\.[0-9]+\.[0-9]+$ ]]; then
  echo 'Refusing a non-private Docker bridge address.' >&2
  exit 1
fi
test -x /usr/lib/systemd/systemd-socket-proxyd
unit_dir="$HOME/.config/systemd/user"
mkdir -p "$unit_dir"
sed "s/@BRIDGE_IPV4@/$bridge_ipv4/g" deploy/neonforge-h3-bridge.socket > "$unit_dir/neonforge-h3-bridge.socket"
install -m 0644 deploy/neonforge-h3-bridge.service "$unit_dir/neonforge-h3-bridge.service"
uv_bin="$(command -v uv)"
sed -e "s|@PROJECT_DIR@|$project_dir|g" -e "s|@UV_BIN@|$uv_bin|g" -e "s|@USER_BIN@|$HOME/.local/bin|g" deploy/neonforge-motion-host.service > "$unit_dir/neonforge-motion-host.service"
systemctl --user daemon-reload
systemctl --user enable --now neonforge-h3-bridge.socket
systemctl --user enable --now neonforge-motion-host.service
network_id="$(docker network inspect ai-net --format '{{.Id}}')"
bridge_device="br-${network_id:0:12}"
printf 'If UFW blocks containers, the narrow required rule is: sudo ufw allow in on %s from 172.18.0.0/16 to %s port 18189 proto tcp\n' "$bridge_device" "$bridge_ipv4"
curl -fsS --max-time 10 "http://$bridge_ipv4:18189/queue"
printf '\nPrivate H3 route: http://%s:18189 (existing H3, no Trellis port change)\n' "$bridge_ipv4"
