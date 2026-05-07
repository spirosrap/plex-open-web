#!/usr/bin/env bash
set -euo pipefail

APP_PORT="${APP_PORT:-5055}"
PORTS="{ 443, ${APP_PORT} }"
MARK_VALUE="0x6d6f6c65"
CONNMARK_VALUE="0x00000f41"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "tailscale is not installed" >&2
  exit 1
fi

if ! command -v nft >/dev/null 2>&1; then
  echo "nft is not installed" >&2
  exit 1
fi

sudo tailscale serve --bg --yes "${APP_PORT}"

sudo install -d -m 0755 /usr/local/sbin
sudo tee /usr/local/sbin/codex-tailscale-web-exclude.sh >/dev/null <<SCRIPT
#!/usr/bin/env bash
set -euo pipefail
/usr/sbin/nft delete table inet codex_tailscale_web_exclude 2>/dev/null || true
/usr/sbin/nft -f - <<'NFT'
table inet codex_tailscale_web_exclude {
  chain input {
    type filter hook input priority -101; policy accept;
    ip saddr 100.64.0.0/10 tcp dport ${PORTS} ct mark set ${CONNMARK_VALUE} meta mark set ${MARK_VALUE}
  }
  chain output {
    type route hook output priority dstnat; policy accept;
    ip daddr 100.64.0.0/10 tcp sport ${PORTS} ct mark set ${CONNMARK_VALUE} meta mark set ${MARK_VALUE}
    ip daddr 100.64.0.0/10 tcp dport ${PORTS} ct mark set ${CONNMARK_VALUE} meta mark set ${MARK_VALUE}
  }
}
NFT
SCRIPT
sudo chmod 0755 /usr/local/sbin/codex-tailscale-web-exclude.sh
sudo /usr/local/sbin/codex-tailscale-web-exclude.sh

sudo tee /etc/systemd/system/codex-tailscale-web-exclude.service >/dev/null <<'SERVICE'
[Unit]
Description=Keep Plex Open Web reachable over Tailscale while Mullvad is active
After=network-online.target tailscaled.service mullvad-daemon.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/codex-tailscale-web-exclude.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable --now codex-tailscale-web-exclude.service
tailscale serve status
