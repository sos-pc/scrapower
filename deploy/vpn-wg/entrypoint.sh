#!/bin/sh
set -e

echo "[vpn-wg] Starting WireGuard..."
wg-quick up /etc/wireguard/wg0.conf || { echo "[vpn-wg] WireGuard failed!"; exit 1; }
echo "[vpn-wg] WireGuard up"

# Fix: SOCKS5 reply traffic must bypass WireGuard routing table 51820.
# Without this mark, SYN-ACK packets from Dante go through wg0 instead of eth0
# and never reach external clients (because wg-quick routes all unmarked
# traffic via table 51820 which sends everything through the tunnel).
iptables -t mangle -A OUTPUT -p tcp --sport 1081 -j MARK --set-mark 0xca6c
echo "[vpn-wg] SOCKS5 reply routing fix applied"

# Set SOCKS5 proxy user password from env
if [ -n "${SOCKS_USER}" ] && [ -n "${SOCKS_PASS}" ]; then
    echo "${SOCKS_USER}:${SOCKS_PASS}" | chpasswd
    echo "[vpn-wg] SOCKS5 credentials set"
else
    echo "[vpn-wg] WARNING: SOCKS_USER/SOCKS_PASS not set - proxy will have no auth!"
fi

echo "[vpn-wg] Starting Dante SOCKS5 on port 1081..."
sockd -f /etc/dante.conf
