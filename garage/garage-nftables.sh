#!/usr/bin/env bash
# Filtrage nftables du port S3 3900 : uniquement Loki (192.168.23.24) + localhost.
set -euo pipefail
TABLE=inet
FAMILY_TABLE=garage_filter
ALLOWED=${GARAGE_ALLOWED_SOURCES:-192.168.23.24}

case "${1:-start}" in
  start)
    nft delete table "$TABLE" "$FAMILY_TABLE" 2>/dev/null || true
    nft add table "$TABLE" "$FAMILY_TABLE"
    nft add chain "$TABLE" "$FAMILY_TABLE" input '{ type filter hook input priority 0; policy accept; }'
    nft add rule "$TABLE" "$FAMILY_TABLE" input tcp dport 3900 iif lo accept
    for src in $ALLOWED; do
      nft add rule "$TABLE" "$FAMILY_TABLE" input tcp dport 3900 ip saddr "$src" accept
    done
    nft add rule "$TABLE" "$FAMILY_TABLE" input tcp dport 3900 drop
    ;;
  stop)
    nft delete table "$TABLE" "$FAMILY_TABLE" 2>/dev/null || true
    ;;
  *)
    echo "usage: $0 start|stop" >&2
    exit 2
    ;;
esac
