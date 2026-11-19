#!/usr/bin/env bash
# Test de coupure réseau Alloy -> Loki (à lancer en root sur le serveur web).
# 1. bloque le flux vers Loki (règle iptables OUTPUT temporaire, retirée à la sortie) ;
# 2. génère N requêtes pendant la coupure ;
# 3. vérifie qu'Alloy réessaie sans rien perdre ;
# 4. rétablit le réseau et vérifie que les N lignes arrivent, sans doublon.
# Usage : ./outage_test.sh [durée_coupure_s=300] [requêtes=200]
set -euo pipefail

LOKI_IP=192.168.23.24
LOKI=https://${LOKI_IP}:3100
PKI=/etc/alloy/pki
DURATION=${1:-300}
N=${2:-200}
MARK="/test/outage-$(date +%s)"
RULE=(OUTPUT -d "$LOKI_IP" -p tcp --dport 3100 -j REJECT --reject-with tcp-reset)

cleanup() { iptables -D "${RULE[@]}" 2>/dev/null || true; }
trap cleanup EXIT

metric() { curl -s 127.0.0.1:12345/metrics | awk -v m="$1" '$1 ~ "^"m"[{ ]" {s+=$2} END {printf "%d", s}'; }
count() {
  curl -s --cacert $PKI/ca.crt --cert $PKI/client.crt --key $PKI/client.key -G "$LOKI/loki/api/v1/query" \
    --data-urlencode "query=sum(count_over_time({job=\"nginx-access\"} |= \"$MARK\" [2h]))" \
    | python3 -c 'import sys,json; r=json.load(sys.stdin)["data"]["result"]; print(int(float(r[0]["value"][1])) if r else 0)'
}

echo "[1] Coupure du flux vers Loki pendant ${DURATION}s"
iptables -I "${RULE[@]}"
DROPPED_BEFORE=$(metric loki_write_dropped_entries_total)

echo "[2] Génération de $N requêtes (${MARK}) pendant la coupure"
for i in $(seq 1 "$N"); do
  curl -s -o /dev/null -A "outage-test" "http://127.0.0.1:8088${MARK}" || true
  sleep "$(awk -v d="$DURATION" -v n="$N" 'BEGIN{printf "%.3f", d/n/2}')"
done
sleep $(( DURATION / 2 ))
echo "    tentatives de renvoi (loki_write_batch_retries_total) : $(metric loki_write_batch_retries_total)"
echo "    lignes présentes localement : $(grep -c "\"uri\":\"${MARK}\"" /var/log/nginx/access_json.log)"

echo "[3] Rétablissement du réseau"
cleanup
for t in $(seq 1 60); do
  C=$(count); [ "$C" -ge "$N" ] && break; sleep 5
done
DROPPED_AFTER=$(metric loki_write_dropped_entries_total)
echo "    lignes reçues par Loki : $C / $N"
echo "    lignes abandonnées par Alloy pendant le test : $(( DROPPED_AFTER - DROPPED_BEFORE ))"
if [ "$C" -eq "$N" ] && [ "$DROPPED_AFTER" -eq "$DROPPED_BEFORE" ]; then
  echo "PASS : aucune perte, aucun doublon"
else
  echo "FAIL"; exit 1
fi
