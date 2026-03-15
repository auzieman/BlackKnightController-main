FROM alpine:3.19

RUN apk add --no-cache dnsmasq

EXPOSE 67/udp
EXPOSE 69/udp
EXPOSE 4011/udp

CMD ["dnsmasq", "--keep-in-foreground", "--log-facility=-"]
