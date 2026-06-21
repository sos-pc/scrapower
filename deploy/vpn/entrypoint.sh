#!/bin/sh
set -e

# Write VPN credentials from env vars
mkdir -p /dev/net
mknod /dev/net/tun c 10 200 2>/dev/null || true

if [ -n "$VPN_USER" ] && [ -n "$VPN_PASS" ]; then
    echo "$VPN_USER" > /etc/openvpn/auth.txt
    echo "$VPN_PASS" >> /etc/openvpn/auth.txt
    chmod 600 /etc/openvpn/auth.txt
    echo "VPN credentials configured"
else
    echo "WARNING: VPN_USER/VPN_PASS not set — VPN will fail"
fi

# Start OpenVPN
echo "Starting OpenVPN..."
openvpn --config /etc/openvpn/openvpn.ovpn --daemon

# Wait for VPN tunnel to come up
echo "Waiting for VPN connection..."
for i in $(seq 1 30); do
    if ip addr show tun0 >/dev/null 2>&1; then
        echo "VPN connected! (tun0 is up)"
        break
    fi
    sleep 2
done

if ! ip addr show tun0 >/dev/null 2>&1; then
    echo "ERROR: VPN failed to connect after 60s"
    exit 1
fi

# Verify VPN is routing
echo "VPN IP: $(curl -s --socks5 127.0.0.1:1080 https://ifconfig.me 2>/dev/null || echo 'check via proxy')"

# Start Dante SOCKS5 proxy
echo "Starting SOCKS5 proxy on :1080..."
sockd -f /etc/dante.conf -N 2

# Keep container alive
echo "VPN + SOCKS5 ready. Sleeping..."
while true; do sleep 3600; done
