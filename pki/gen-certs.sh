#!/usr/bin/env bash
# Génère une autorité de certification (CA) interne et les certificats du TLS mutuel :
#   - loki.crt/key        : certificat serveur de Loki (SAN = IP/nom de la VM centrale)
#   - client-<nom>.crt/key: un certificat client par serveur web (Alloy) + un pour Grafana
#
# À exécuter sur la VM centrale (hors ligne, openssl suffit), en root :
#   ./gen-certs.sh /root/loki-pki 192.168.23.24 central AH-NOC-FRONT-TEST grafana
# Pour ajouter un serveur web plus tard, relancer avec son nom : la CA existante est réutilisée.
set -euo pipefail

OUT=${1:?répertoire de sortie}
LOKI_IP=${2:?IP du serveur Loki}
LOKI_DNS=${3:?nom DNS du serveur Loki}
shift 3
CLIENTS=("$@")
DAYS_CA=3650
DAYS_CERT=825

umask 077
mkdir -p "$OUT"
cd "$OUT"

if [[ ! -f ca.key ]]; then
  openssl genrsa -out ca.key 4096
  # Extensions explicites : les clients à vérification X.509 stricte (OpenSSL 3, Python >= 3.13)
  # exigent keyUsage sur la CA et l'Authority Key Identifier sur les certificats émis.
  cat > ca.cnf <<'EOF'
[req]
distinguished_name = dn
prompt = no
[dn]
O = NOC
CN = NOC Loki internal CA
[v3_ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
EOF
  openssl req -x509 -new -key ca.key -sha256 -days "$DAYS_CA" \
    -config ca.cnf -extensions v3_ca -out ca.crt
  rm -f ca.cnf
fi

sign() { # $1=nom $2=extensions
  openssl genrsa -out "$1.key" 2048
  openssl req -new -key "$1.key" -subj "/O=NOC/CN=$1" -out "$1.csr"
  printf '%bsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n' "$2" > "$1.ext"
  openssl x509 -req -in "$1.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
    -days "$DAYS_CERT" -sha256 -extfile "$1.ext" -out "$1.crt"
  rm -f "$1.csr" "$1.ext"
}

if [[ ! -f loki.crt ]]; then
  sign loki "basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:${LOKI_IP},IP:127.0.0.1,DNS:${LOKI_DNS},DNS:localhost\n"
fi

for c in "${CLIENTS[@]}"; do
  [[ -f "client-$c.crt" ]] && continue
  sign "client-$c" "basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=clientAuth\n"
done

chmod 600 ./*.key
chmod 644 ./*.crt
ls -l
