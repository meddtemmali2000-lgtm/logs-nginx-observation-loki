#!/usr/bin/env bash
# Mesure de la bande passante de collecte sur une période (à lancer en root sur le serveur web).
# Compare, sur la même période :
#   - "sans optimisation" : octets bruts écrits par Nginx (ce qu'enverrait un transfert ligne à
#     ligne, non compressé et non filtré, du fichier access_json.log) ;
#   - "après filtrage"    : octets des lignes envoyées par Alloy, avant compression ;
#   - "sur le réseau"     : octets réellement émis vers Loki (TLS + HTTP + protobuf snappy),
#                           comptés par une règle iptables sans action (compteur seul).
# Usage : ./bandwidth_measure.sh [durée_s=600]
set -euo pipefail

LOKI_IP=192.168.23.24
DURATION=${1:-600}
LOG=/var/log/nginx/access_json.log
RULE=(OUTPUT -d "$LOKI_IP" -p tcp --dport 3100)

metric() { curl -s 127.0.0.1:12345/metrics | awk -v m="$1" '$1 ~ "^"m"[{ ]" {s+=$2} END {printf "%.0f", s}'; }
# Règle sans cible : la colonne "target" est vide, donc $3 = protocole.
wire() { iptables -L OUTPUT -v -x -n | awk -v ip="$LOKI_IP" '$3 == "tcp" && $0 ~ ip && /dpt:3100/ {print $2; exit}'; }

iptables -C "${RULE[@]}" 2>/dev/null || iptables -I "${RULE[@]}"
trap 'iptables -D "${RULE[@]}" 2>/dev/null || true' EXIT

S0=$(stat -c %s "$LOG"); L0=$(wc -l < "$LOG")
B0=$(metric loki_write_sent_bytes_total); E0=$(metric loki_write_sent_entries_total)
Q0=$(metric loki_write_request_size_bytes_sum); R0=$(metric loki_write_request_size_bytes_count)
W0=$(wire)
echo "Mesure pendant ${DURATION}s..."
sleep "$DURATION"
S1=$(stat -c %s "$LOG"); L1=$(wc -l < "$LOG")
B1=$(metric loki_write_sent_bytes_total); E1=$(metric loki_write_sent_entries_total)
Q1=$(metric loki_write_request_size_bytes_sum); R1=$(metric loki_write_request_size_bytes_count)
W1=$(wire)

RAW=$((S1 - S0)); LINES=$((L1 - L0)); SENT=$((B1 - B0)); ENT=$((E1 - E0))
REQ=$((Q1 - Q0)); NREQ=$((R1 - R0)); WIRE=$((W1 - W0))
pct() { awk -v a="$1" -v b="$2" 'BEGIN{ if (b>0) printf "%.1f %%", 100*(1-a/b); else print "n/a" }'; }
cat <<EOF
Lignes écrites par Nginx          : $LINES ($RAW octets)
Lignes envoyées par Alloy         : $ENT (filtrées : $((LINES - ENT)))
Octets des lignes avant compression: $SENT
Octets des requêtes (snappy)      : $REQ en $NREQ requêtes HTTP
Octets réels sur le réseau (TLS)  : $WIRE
Débit moyen sur le réseau         : $(awk -v w="$WIRE" -v d="$DURATION" 'BEGIN{printf "%.2f kbit/s", w*8/d/1000}')
Réduction vs envoi brut           : $(pct "$WIRE" "$RAW")
EOF
