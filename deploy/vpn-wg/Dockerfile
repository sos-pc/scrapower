FROM alpine:3.21
RUN apk add --no-cache wireguard-tools dante-server curl ip6tables linux-pam shadow

# Create SOCKS5 proxy user (password set at runtime via env)
RUN useradd -m -s /sbin/nologin scrapower

COPY entrypoint.sh /
COPY dante.conf /etc/dante.conf
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
