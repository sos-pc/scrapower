#!/bin/sh
set -e

echo "[vpn-wg] Starting WireGuard..."
wg-quick up /etc/wireguard/wg0.conf || { echo "[vpn-wg] WireGuard failed!"; exit 1; }
echo "[vpn-wg] WireGuard up"

echo "[vpn-wg] Starting Dante SOCKS5 on port 1081..."
sockd -f /etc/dante.conf
