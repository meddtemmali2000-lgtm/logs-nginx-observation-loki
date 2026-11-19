#!/usr/bin/env bash
# /usr/local/sbin/loki-firewall.sh — filtrage ciblé du port 3100 (Loki) sur la VM centrale.
# Ne modifie AUCUNE autre règle : une chaîne dédiée LOKI_INGRESS est créée et seul le
# trafic TCP/3100 y est envoyé. Les autres services de la VM ne sont pas concernés.
#   loki-firewall.sh start  : (re)crée la chaîne et le saut depuis INPUT
#   loki-firewall.sh stop   : supprime le saut et la chaîne
# Sources autorisées : LOKI_ALLOWED_SOURCES (séparées par des espaces), lues depuis
# /etc/sysconfig/loki-firewall.
set -euo pipefail

CHAIN=LOKI_INGRESS
PORT=3100
ALLOWED=${LOKI_ALLOWED_SOURCES:?LOKI_ALLOWED_SOURCES non défini}

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
