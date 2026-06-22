FROM alpine:3.21
RUN apk add --no-cache wireguard-tools dante-server curl ip6tables
COPY entrypoint.sh /
COPY dante.conf /etc/dante.conf
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
