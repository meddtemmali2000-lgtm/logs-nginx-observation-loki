#!/usr/bin/env bash
# Filtrage du port S3 3900 : uniquement Loki (192.168.23.24) + localhost.
set -euo pipefail
CHAIN=GARAGE_S3
PORT=3900
ALLOWED=${GARAGE_ALLOWED_SOURCES:-192.168.23.24}

case "${1:-start}" in
  start)
    iptables -N "$CHAIN" 2>/dev/null || iptables -F "$CHAIN"
    iptables -A "$CHAIN" -i lo -j ACCEPT
    for src in $ALLOWED; do
      iptables -A "$CHAIN" -s "$src" -j ACCEPT
    done
    iptables -A "$CHAIN" -j DROP
    iptables -C INPUT -p tcp --dport "$PORT" -j "$CHAIN" 2>/dev/null \
      || iptables -I INPUT 1 -p tcp --dport "$PORT" -j "$CHAIN"
    ;;
  stop)
    while iptables -C INPUT -p tcp --dport "$PORT" -j "$CHAIN" 2>/dev/null; do
      iptables -D INPUT -p tcp --dport "$PORT" -j "$CHAIN"
    done
    iptables -F "$CHAIN" 2>/dev/null || true
    iptables -X "$CHAIN" 2>/dev/null || true
    ;;
  *)
    echo "usage: $0 start|stop" >&2
    exit 2
    ;;
esac
