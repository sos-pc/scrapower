FROM alpine:3.21

RUN apk add --no-cache openvpn dante-server curl

# OpenVPN config and certs
COPY openvpn.ovpn ca.crt client.crt client.key /etc/openvpn/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Dante SOCKS5 proxy config
COPY dante.conf /etc/dante.conf

EXPOSE 1080

ENTRYPOINT ["/entrypoint.sh"]
