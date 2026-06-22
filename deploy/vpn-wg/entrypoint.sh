#!/bin/sh
set -e

echo "[vpn-wg] Starting WireGuard..."
wg-quick up /etc/wireguard/wg0.conf || { echo "[vpn-wg] WireGuard failed!"; exit 1; }
echo "[vpn-wg] WireGuard up"

# Set SOCKS5 proxy user password from env
if [ -n "${SOCKS_USER}" ] && [ -n "${SOCKS_PASS}" ]; then
    echo "${SOCKS_USER}:${SOCKS_PASS}" | chpasswd
    echo "[vpn-wg] SOCKS5 credentials set"
else
    echo "[vpn-wg] WARNING: SOCKS_USER/SOCKS_PASS not set - proxy will have no auth!"
fi

echo "[vpn-wg] Starting Dante SOCKS5 on port 1081..."
sockd -f /etc/dante.conf
