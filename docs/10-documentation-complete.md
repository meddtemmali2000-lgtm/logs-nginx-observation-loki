# Documentation complète — Plateforme d'observation des logs Nginx

**Alloy + Loki + Grafana + Garage S3**

Version du document : 17 novembre 2026  
Statut : **installé et validé** sur les VM réelles  
Public : débutant — chaque terme technique est expliqué

---

## Sommaire

1. [À quoi sert ce projet ?](#1-à-quoi-sert-ce-projet-)
2. [Architecture complète (schéma + rôles)](#2-architecture-complète-schéma--rôles)
3. [Le pipeline : du clic au graphique](#3-le-pipeline--du-clic-au-graphique)
4. [Glossaire pour débutant](#4-glossaire-pour-débutant)
5. [Inventaire des machines](#5-inventaire-des-machines)
6. [Installation A → Z — Vue d'ensemble](#6-installation-a--z--vue-densemble)
7. [VM Garage `172.18.40.129`](#7-vm-garage-1721840129)
8. [VM centrale `192.168.23.24` (Loki + Grafana)](#8-vm-centrale-1921682324-loki--grafana)
9. [VM frontend `192.168.206.112` (Nginx + Alloy)](#9-vm-frontend-192168206112-nginx--alloy)
10. [Certificats TLS mutuel (PKI)](#10-certificats-tls-mutuel-pki)
11. [Configuration Nginx JSON](#11-configuration-nginx-json)
12. [Configuration Alloy (étape par étape)](#12-configuration-alloy-étape-par-étape)
13. [Configuration Loki + Garage](#13-configuration-loki--garage)
14. [Grafana : datasource et dashboard](#14-grafana--datasource-et-dashboard)
15. [Le langage LogQL (débutant)](#15-le-langage-logql-débutant)
16. [Le dashboard panneau par panneau](#16-le-dashboard-panneau-par-panneau)
17. [Vérifications et tests réalisés](#17-vérifications-et-tests-réalisés)
18. [Bande passante et passage à 35 sites](#18-bande-passante-et-passage-à-35-sites)
19. [Commandes du quotidien](#19-commandes-du-quotidien)
20. [Dépannage rapide](#20-dépannage-rapide)
21. [Ce qui a été volontairement exclu](#21-ce-qui-a-été-volontairement-exclu)

---

## 1. À quoi sert ce projet ?

Vous avez des sites web servis par **Nginx**. Chaque visite produit une ligne dans un fichier de log. Sans outil, ces fichiers restent sur chaque serveur : difficiles à lire, impossibles à croiser, et gourmands en disque.

Ce projet centralise ces logs pour répondre à des questions comme :

- Combien de requêtes aujourd'hui ?
- Quelles URLs sont les plus lentes (P95) ?
- Combien de visiteurs uniques estimés ?
- Où viennent les 404 / 500 ?
- Que se passe-t-il pendant un scan ?

**Principe :** chaque serveur web envoie ses logs vers **une seule** machine centrale. On visualise tout dans **Grafana**.

**Contraintes respectées :**

- Pas de Promtail (on utilise **Grafana Alloy**)
- Pas d'Elasticsearch, Kafka, Tempo, Prometheus (sauf besoin démontré)
- Optimisation de la **bande passante**
- Pas de mots de passe, cookies, tokens, query strings sensibles dans les logs
- Loki **jamais** exposé sur Internet
- IP complète **non** utilisée comme label Loki (tronquée en /24)

---

## 2. Architecture complète (schéma + rôles)

### 2.1 Schéma général

```
 ┌──────────────┐     X-Forwarded-For      ┌─────────────────────────────────────┐
 │  Navigateur  │ ───────────────────────► │  Nginx Proxy Manager                │
 │  du visiteur │                          │  192.168.206.40                     │
 └──────────────┘                          └──────────────────┬──────────────────┘
                                                              │
                                                              ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  VM FRONTEND — 192.168.206.112 (Debian 13) — AH-NOC-FRONT-TEST               │
 │                                                                              │
 │   ┌─────────────┐   écrit JSON    ┌──────────────────┐                       │
 │   │   NGINX     │ ──────────────► │ access_json.log  │                       │
 │   │  1.26.3     │                 │ + error.log      │                       │
 │   └─────────────┘                 └────────┬─────────┘                       │
 │                                            │ lit comme "tail -f"             │
 │                                            ▼                                 │
 │                                   ┌──────────────────┐                       │
 │                                   │  GRAFANA ALLOY   │                       │
 │                                   │     1.21.3       │                       │
 │                                   │ filtre / batch / │                       │
 │                                   │ snappy / mTLS    │                       │
 │                                   └────────┬─────────┘                       │
 └────────────────────────────────────────────┼─────────────────────────────────┘
                                              │ HTTPS :3100 (TLS mutuel)
                                              │ certificat client Alloy
                                              ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  VM CENTRALE — 192.168.23.24 (AlmaLinux 8) — PRODUCTION                      │
 │                                                                              │
 │   Pare-feu iptables : port 3100 ouvert SEULEMENT aux serveurs web            │
 │                                                                              │
 │   ┌─────────────┐  stocke chunks   ┌──────────────────┐                      │
 │   │ GRAFANA LOKI│ ───────────────► │  GARAGE S3       │◄── VM 172.18.40.129  │
 │   │   3.7.10    │   API :3900      │  (zabbix-db)     │    Garage v2.5.0     │
 │   └──────┬──────┘                  └──────────────────┘                      │
 │          │ LogQL (mTLS local)                                                │
 │          ▼                                                                   │
 │   ┌─────────────┐                                                            │
 │   │   GRAFANA   │  ◄── Administrateurs (port 3000)                           │
 │   │   12.0.1    │                                                            │
 │   │  Dashboard  │                                                            │
 │   └─────────────┘                                                            │
 └──────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Rôle de chaque technologie

| Technologie | Icône (rôle) | Version | Où | Rôle en une phrase |
|---|---|---|---|---|
| **Nginx** | Serveur web | 1.26.3 | Frontend | Reçoit les HTTP et écrit les logs JSON |
| **logrotate** | Rotation fichiers | 3.22 | Frontend | Fait « tourner » les logs (tampon 7 jours) |
| **Grafana Alloy** | Agent de collecte | 1.21.3 | Frontend | Lit, filtre, anonymise, compresse, envoie |
| **Grafana Loki** | Base de logs | 3.7.10 | Centrale | Stocke et répond aux requêtes LogQL |
| **Garage** | Stockage objet S3 | 2.5.0 | 172.18.40.129 | Disque distant où Loki pose ses chunks |
| **Grafana** | Interface | 12.0.1 | Centrale | Affiche le dashboard |
| **OpenSSL / PKI** | Certificats | système | Centrale | TLS mutuel (mTLS) |
| **iptables / nftables** | Pare-feu | système | Centrale + Garage | Limite qui peut parler à Loki / Garage |

### 2.3 Flux réseau et ports

| # | De | Vers | Port | Protocole | Pourquoi |
|---|---|---|---|---|---|
| 1 | Alloy (frontend) | Loki (centrale) | **3100** | HTTPS + mTLS | Envoi des logs |
| 2 | Grafana | Loki (même VM) | 3100 | HTTPS + mTLS | Requêtes du dashboard |
| 3 | Loki | Garage | **3900** | HTTP S3 | Stockage des chunks |
| 4 | Admin | Grafana | 3000 | HTTP(S) | Interface |
| 5 | Admin (tunnel SSH) | Alloy | 12345 | HTTP local | Debug Alloy |
| 6 | Loki interne | lui-même | 9096 | gRPC | Jamais exposé |

---

## 3. Le pipeline : du clic au graphique

Voici **exactement** ce qui se passe quand un visiteur ouvre une page :

### Étape A — Le navigateur

Le visiteur demande `https://monsite/page?token=abc`.

### Étape B — Le reverse proxy (Nginx Proxy Manager)

Adresse `192.168.206.40`. Il ajoute l'en-tête `X-Forwarded-For` avec la vraie IP du visiteur, puis transmet à Nginx du frontend.

### Étape C — Nginx écrit une ligne JSON

Nginx écrit dans `/var/log/nginx/access_json.log` **sans** la query string :

```json
{"timestamp":"...","uri":"/page","status":"200","request_time":"0.045", ...}
```

`?token=abc` n'est **jamais** écrit sur disque.

### Étape D — Alloy lit le fichier

Comme `tail -f`, Alloy lit la nouvelle ligne et mémorise sa **position** sur disque (pour reprendre après coupure).

### Étape E — Alloy transforme

1. Parse le JSON  
2. Supprime les fichiers statiques réussis (`.css`, `.js`, `.png`… en 2xx/3xx)  
3. Calcule `visitor_id` = empreinte anonyme (IP + User-Agent + sel)  
4. Tronque l'IP (`203.0.113.45` → `203.0.113.0`)  
5. Masque les jetons d'URL (`/:token`)  
6. Classe le navigateur (`ua_family`)  
7. Reconstruit une ligne JSON compacte  

### Étape F — Alloy envoie

Regroupe les lignes (**batch** 5 s ou 1 Mio), les compresse (**snappy** ≈ ×4,5), envoie en HTTPS avec **certificat client**.

### Étape G — Pare-feu + Loki

Le pare-feu n'accepte le port 3100 que depuis le frontend. Loki vérifie le certificat, accepte les lignes.

### Étape H — Garage

Loki garde un moment les données en mémoire, puis écrit des **chunks** (blocs compressés) dans Garage via l'API S3 (port 3900).

### Étape I — Grafana

Vous ouvrez le dashboard. Grafana pose des questions **LogQL** à Loki. Loki lit les chunks dans Garage et renvoie les chiffres / tableaux / courbes.

---

## 4. Glossaire pour débutant

| Terme | Explication simple |
|---|---|
| **Log** | Fichier texte où le serveur note chaque requête |
| **Nginx** | Logiciel qui sert les pages web |
| **Reverse proxy** | Serveur devant Nginx qui reçoit d'abord le trafic (ici NPM) |
| **JSON** | Format de texte structuré `{ "clé": "valeur" }` |
| **Query string** | Partie après `?` dans une URL (`?user=bob`) — souvent sensible |
| **Alloy** | Agent Grafana qui lit les logs et les envoie à Loki |
| **Loki** | Base de données spécialisée pour les logs (légère) |
| **Label** | Étiquette indexée d'un flux (`site`, `hostname`…). Il faut en garder **peu** |
| **Flux (stream)** | Ensemble de lignes ayant exactement les mêmes labels |
| **Chunk** | Bloc compressé de lignes stocké (dans Garage) |
| **Cardinalité** | Nombre de valeurs différentes. Trop élevée = Loki lent |
| **Batch** | Lot de plusieurs lignes envoyées en une seule requête HTTP |
| **snappy** | Compression rapide utilisée par Alloy |
| **Backpressure** | Quand Loki est saturé/coupé, Alloy **ralentit** sans jeter |
| **Positions** | Fichier où Alloy note « j'ai lu jusqu'ici » |
| **TLS / HTTPS** | Chiffrement du trafic réseau |
| **mTLS / TLS mutuel** | Le client **et** le serveur présentent un certificat |
| **PKI / CA** | Autorité qui signe les certificats (notre CA interne) |
| **Certificat** | Fichier d'identité numérique |
| **LogQL** | Langage de requête de Loki (comme SQL, mais pour les logs) |
| **P95** | 95ᵉ percentile : 95 % des valeurs sont en dessous |
| **Upstream** | Application derrière Nginx (Next.js, API…) |
| **Rétention** | Durée de conservation (14 jours dans Loki) |
| **S3** | API de stockage objet (Amazon). Garage la reproduit en gratuit/self-hosted |
| **Garage** | Stockage objet S3 open source (Deuxfleurs) |
| **Filesystem** | Stockage directement sur le disque local de la VM |
| **Datasource** | Connexion Grafana → Loki |
| **Dashboard** | Page de graphiques dans Grafana |
| **Provisioning** | Configurer Grafana par fichiers plutôt qu'à la main |
| **systemd** | Système Linux qui démarre/arrête les services |
| **Override** | Petit fichier qui modifie un service sans toucher au paquet |
| **iptables / nftables** | Pare-feu Linux |
| **SHA-256** | Empreinte pour vérifier qu'un fichier n'est pas corrompu |
| **logrotate** | Outil qui archive/compresse les vieux logs |
| **VISITOR_SALT** | Secret pour anonymiser les visiteurs (identique sur tous les serveurs) |

---

## 5. Inventaire des machines

| Machine | IP | OS | Rôle dans le projet | Internet |
|---|---|---|---|---|
| Frontend | `192.168.206.112` | Debian 13 | Nginx + Alloy + logrotate | Oui |
| Centrale | `192.168.23.24` | AlmaLinux 8 | Loki + Grafana | **Non** (hors ligne) |
| Garage | `172.18.40.129` | Debian 13 (`zabbix-db`) | Garage S3 | Selon réseau |
| Proxy | `192.168.206.40` | NPM | Reverse proxy (déjà existant) | — |

**Versions installées (constatées le 17/11/2026) :**

- Alloy **v1.21.3**
- Loki **3.7.10-1**
- Grafana **12.0.1**
- Garage **v2.5.0**
- Nginx **1.26.3**

**Décisions prises :**

| Sujet | Décision |
|---|---|
| IP clients | Tronquées `/24` + empreinte `visitor_id` |
| Rétention Loki | 14 jours |
| Pare-feu Loki | iptables ciblé port 3100 |
| Statiques | Supprimés sauf 4xx/5xx |
| Stockage Loki | Garage S3 (bascule depuis filesystem local) |

---

## 6. Installation A → Z — Vue d'ensemble

**Ordre impératif** (celui réellement suivi) :

```
1. Préparer / télécharger les paquets (Loki RPM hors ligne)
2. Installer Garage (stockage)          → 172.18.40.129
3. Pare-feu Loki (AVANT le RPM)        → 192.168.23.24
4. Certificats PKI                     → 192.168.23.24
5. Installer + configurer Loki         → 192.168.23.24
6. Datasource + dashboard Grafana      → 192.168.23.24
7. logrotate + format JSON Nginx       → 192.168.206.112
8. Installer + configurer Alloy        → 192.168.206.112
9. Tests de validation
```

> **Pourquoi le pare-feu avant Loki ?** Le RPM démarre Loki immédiatement **sans TLS**. Sans pare-feu, le port 3100 serait ouvert à tout le LAN pendant quelques minutes.

---

## 7. VM Garage `172.18.40.129`

### 7.1 Pourquoi Garage ?

Loki doit stocker des fichiers (chunks + index). Deux options :

| Option | Avantage | Inconvénient |
|---|---|---|
| Disque local de Loki | Simple | Si la VM Loki tombe, les données sont sur cette VM |
| **Garage S3** | Sépare stockage et moteur ; survit mieux | Un service de plus |

Nous avons basculé en **test** vers Garage.

> **Attention :** cette VM héberge aussi Zabbix/PostgreSQL. Garage est limité à 1 Go RAM / 1 cœur. Pour la production durable, une VM dédiée est préférable.

### 7.2 Installation manuelle de Garage

**1. Télécharger le binaire officiel v2.5.0** (depuis une machine avec Internet si besoin) :

```bash
# Empreinte attendue (à vérifier après téléchargement)
# Taille ≈ 66 Mo
curl -fL -o /usr/local/bin/garage \
  https://garagehq.deuxfleurs.fr/_releases/v2.5.0/x86_64-unknown-linux-musl/garage
chmod 755 /usr/local/bin/garage
garage --version   # garage v2.5.0
```

**2. Créer l'utilisateur et les dossiers :**

```bash
useradd --system --home /var/lib/garage --shell /usr/sbin/nologin garage
mkdir -p /var/lib/garage/{meta,data}
chown -R garage:garage /var/lib/garage
```

**3. Fichier `/etc/garage.toml` :**

```toml
metadata_dir = "/var/lib/garage/meta"
data_dir = "/var/lib/garage/data"
db_engine = "sqlite"
replication_factor = 1
rpc_bind_addr = "0.0.0.0:3901"
rpc_public_addr = "172.18.40.129:3901"
rpc_secret = "<openssl rand -hex 32>"

[s3_api]
s3_region = "garage"
api_bind_addr = "0.0.0.0:3900"
root_domain = ".s3.garage.local"
public_endpoint = "http://172.18.40.129:3900"

[s3_web]
bind_addr = "127.0.0.1:3902"
root_domain = ".web.garage.local"
index = "index.html"

[admin]
api_bind_addr = "127.0.0.1:3903"
admin_token = "<openssl rand -base64 32>"
metrics_token = "<openssl rand -base64 32>"
```

**4. Service systemd** (`garage/garage.service` du dépôt) : `garage server --single-node`, `MemoryMax=1G`, `CPUQuota=100%`.

```bash
systemctl daemon-reload
systemctl enable --now garage
garage status
```

**5. Créer buckets et clé pour Loki :**

```bash
garage bucket create loki-chunks
garage bucket create loki-ruler
garage key create loki-key
# Noter Key ID et Secret key !
garage bucket allow --read --write --owner loki-chunks --key loki-key
garage bucket allow --read --write --owner loki-ruler --key loki-key
```

**6. Pare-feu nftables** (port 3900 → seulement `192.168.23.24`) :

```bash
# Script : garage/garage-nftables.sh
GARAGE_ALLOWED_SOURCES="192.168.23.24" /usr/local/sbin/garage-firewall.sh start
systemctl enable garage-firewall
```

### 7.3 Ports Garage

| Port | Écoute | Usage |
|---|---|---|
| 3900 | toutes interfaces (filtré) | API S3 |
| 3901 | toutes interfaces | RPC cluster |
| 3902 | localhost | Web |
| 3903 | localhost | Admin |

---

## 8. VM centrale `192.168.23.24` (Loki + Grafana)

### 8.1 Préparation hors ligne

La VM n'a **pas** Internet. Sur une machine avec Internet :

```bash
wget https://github.com/grafana/loki/releases/download/v3.7.10/loki-3.7.10-1.x86_64.rpm
sha256sum loki-3.7.10-1.x86_64.rpm
# attendu : 900dcd39fa6365046fb9f371501a1f9a2067a404c9182e38ac2eb666a1542510
```

Transférer vers `~/loki-deploy/` avec les dossiers `loki/`, `pki/`, `grafana/` du dépôt.

### 8.2 Pare-feu du port 3100 (AVANT le RPM)

```bash
sudo install -m 0750 loki/firewall/loki-firewall.sh /usr/local/sbin/
sudo install -m 0644 loki/firewall/loki-firewall.sysconfig /etc/sysconfig/loki-firewall
sudo install -m 0644 loki/firewall/loki-firewall.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now loki-firewall
sudo iptables -L LOKI_INGRESS -n -v
```

Chaîne `LOKI_INGRESS` :

```
lo ACCEPT → 192.168.206.112 ACCEPT → DROP
```

### 8.3 Installer Loki

```bash
sudo rpm -ivh loki-3.7.10-1.x86_64.rpm
sudo systemctl stop loki          # stop immédiat (config par défaut sans TLS)
sudo rm -rf /tmp/loki
```

### 8.4 Certificats (voir §10)

```bash
sudo bash pki/gen-certs.sh /root/loki-pki 192.168.23.24 central AH-NOC-FRONT-TEST grafana
sudo install -d -m 0750 -o root -g loki /etc/loki/pki
sudo install -m 0644 -o root -g loki /root/loki-pki/ca.crt   /etc/loki/pki/
sudo install -m 0644 -o root -g loki /root/loki-pki/loki.crt /etc/loki/pki/
sudo install -m 0640 -o root -g loki /root/loki-pki/loki.key /etc/loki/pki/
```

### 8.5 Configuration Loki + Garage

Fichier `/etc/loki/config.yml` (version actuelle du dépôt) :

- Écoute HTTPS 3100 avec **RequireAndVerifyClientCert**
- gRPC seulement sur `127.0.0.1:9096`
- Stockage **S3** → Garage `http://172.18.40.129:3900`
- Schema `tsdb` / `v13`
- Rétention **336h** (14 jours)
- `max_query_series: 50000`
- Identifiants S3 via variables d'environnement

```bash
# /etc/loki/garage.env (0640 root:loki) — NE PAS committer dans Git
GARAGE_ACCESS_KEY_ID=GK...
GARAGE_SECRET_ACCESS_KEY=...
```

Override systemd :

```ini
[Service]
EnvironmentFile=/etc/loki/garage.env
ExecStart=
ExecStart=/usr/bin/loki -config.file=/etc/loki/config.yml -config.expand-env=true
MemoryLimit=2G
CPUQuota=200%
```

```bash
sudo -u loki bash -c 'set -a; . /etc/loki/garage.env; set +a; loki -config.file=/etc/loki/config.yml -config.expand-env=true -verify-config'
sudo systemctl daemon-reload
sudo systemctl enable --now loki
```

Contrôle :

```bash
P=/root/loki-pki
sudo curl -s --cacert $P/ca.crt --cert $P/client-grafana.crt --key $P/client-grafana.key https://127.0.0.1:3100/ready
# → ready
```

### 8.6 Grafana

```bash
sudo install -d -m 0750 -o root -g grafana /etc/grafana/pki
# copier ca.crt, client-grafana.crt, client-grafana.key
sudo install grafana/provisioning/datasources/loki-nginx.yaml \
  /etc/grafana/provisioning/datasources/
sudo systemctl restart grafana-server   # ~10 s d'indisponibilité
```

Importer le dashboard `grafana/dashboards/nginx-analytics-loki.json` dans le dossier **Nginx**.

URL : `http://192.168.23.24:3000/d/nginx-loki-analytics`

---

## 9. VM frontend `192.168.206.112` (Nginx + Alloy)

### 9.1 logrotate

```bash
apt-get update && apt-get install -y logrotate
install -m 0644 nginx/logrotate-nginx /etc/logrotate.d/nginx
# rotate 7 = 7 jours de tampon local (= reject_old_samples_max_age Loki)
```

### 9.2 Format JSON Nginx

```bash
install -m 0644 nginx/conf.d/10-json-analytics-log.conf /etc/nginx/conf.d/
nginx -t && systemctl reload nginx
chown www-data:adm /var/log/nginx/access_json.log
chmod 0640 /var/log/nginx/access_json.log
```

Points clés du fichier :

- `set_real_ip_from 192.168.206.40` → vraie IP derrière NPM  
- `map` pour enlever la query string  
- `log_format json_analytics escape=json`  
- Champs : timestamp, server_name, remote_addr, method, uri, status, bytes, request_time, upstream_response_time, user_agent, request_id  

### 9.3 Installer Alloy

```bash
apt-get install -y gpg wget
wget -O /etc/apt/keyrings/grafana.asc https://apt.grafana.com/gpg-full.key
echo "deb [signed-by=/etc/apt/keyrings/grafana.asc] https://apt.grafana.com stable main" \
  > /etc/apt/sources.list.d/grafana.list
apt-get update
apt-get install -y alloy=1.21.3-1
apt-mark hold alloy
```

### 9.4 Certificat client Alloy

Copier depuis `/root/loki-pki` de la centrale :

- `ca.crt` → `/etc/alloy/pki/ca.crt`
- `client-AH-NOC-FRONT-TEST.crt` → `/etc/alloy/pki/client.crt`
- `client-AH-NOC-FRONT-TEST.key` → `/etc/alloy/pki/client.key` (0640)

### 9.5 Configuration Alloy

```bash
install -m 0640 -o root -g alloy alloy/config.alloy /etc/alloy/config.alloy
install -m 0640 -o root -g alloy alloy/default-alloy /etc/default/alloy
SALT=$(openssl rand -hex 32)   # 1ère fois seulement ; ensuite réutiliser le même
sed -i "s/^VISITOR_SALT=.*/VISITOR_SALT=\"$SALT\"/" /etc/default/alloy
install -m 0644 alloy/systemd/alloy.service.d/override.conf /etc/systemd/system/alloy.service.d/
alloy fmt /etc/alloy/config.alloy > /dev/null
systemctl daemon-reload
systemctl enable --now alloy
curl -s 127.0.0.1:12345/-/ready   # Alloy is ready.
```

`/etc/default/alloy` contient notamment :

```bash
LOKI_URL="https://192.168.23.24:3100/loki/api/v1/push"
VISITOR_SALT="..."
CUSTOM_ARGS="--server.http.listen-addr=127.0.0.1:12345 --disable-reporting"
```

---

## 10. Certificats TLS mutuel (PKI)

### 10.1 Pourquoi mTLS ?

Avec HTTPS classique, seul le serveur prouve son identité. Avec **mTLS**, Alloy et Grafana doivent aussi présenter un certificat signé par notre CA. Sans certificat valide → connexion refusée.

```
              CA interne (ca.crt + ca.key)
                 │ signe
     ┌───────────┼────────────────┐
  loki.crt   client-frontend   client-grafana
  (serveur)  (Alloy)           (Grafana)
```

### 10.2 Génération

```bash
sudo bash pki/gen-certs.sh /root/loki-pki 192.168.23.24 central AH-NOC-FRONT-TEST grafana
```

| Fichier | Secret ? | Où |
|---|---|---|
| `ca.key` | **Oui, critique** | Reste sur la centrale, sauvegardé |
| `ca.crt` | Non | Loki, Alloy, Grafana |
| `loki.crt/key` | Clé oui | `/etc/loki/pki/` |
| `client-*.crt/key` | Clé oui | Chaque client |

Validité certificats : 825 jours. CA : 10 ans.

---

## 11. Configuration Nginx JSON

Fichier : `nginx/conf.d/10-json-analytics-log.conf`

| Élément | Rôle |
|---|---|
| `set_real_ip_from` | Faire confiance au proxy NPM |
| `real_ip_header X-Forwarded-For` | Lire la vraie IP |
| `map $request_uri` | Couper avant `?` |
| `escape=json` | JSON toujours valide |
| `access_log ... json_analytics` | Nouveau fichier à côté de l'ancien |

**Jamais journalisé :** cookies, Authorization, corps, referer, query string.

---

## 12. Configuration Alloy (étape par étape)

Chaîne :

```
loki.source.file → loki.process → loki.write → Loki
```

### 12.1 Lecture (`loki.source.file`)

- Fichiers : `access_json.log` et `error.log`
- Labels **uniquement** : `site`, `environment`, `hostname`, `application`, `job`
- Positions sur disque : `/var/lib/alloy/data`
- `on_positions_file_error = "restart_from_end"` (évite les doublons)

### 12.2 Traitement accès (`loki.process`)

| # | Stage | Effet |
|---|---|---|
| 1 | `label_drop` | Retire `filename` |
| 2 | `json` | Extrait les champs |
| 3 | `drop` | Statiques 2xx/3xx |
| 4 | `drop` | Sondes NPM réussies |
| 5 | `timestamp` | Heure = celle de Nginx |
| 6 | `template` | `visitor_id` (hash salé) |
| 7 | `template` | IP tronquée |
| 8 | `template` | Jetons URL → `/:token` |
| 9 | `template` | `ua_family` |
| 10 | `output` | Ligne JSON compacte |
| 11 | `limit` 500/s | Backpressure, `drop=false` |

### 12.3 Envoi (`loki.write`)

| Paramètre | Valeur | Sens |
|---|---|---|
| `batch_wait` | 5s | Lot au plus tard toutes les 5 s |
| `batch_size` | 1MiB | Ou dès 1 Mio |
| Compression | snappy | Automatique |
| `max_backoff_retries` | 0 | Essais **infinis** |
| TLS | mTLS | Certificats dans `/etc/alloy/pki/` |

**Pendant une coupure :** Alloy réessaie, bloque la lecture, ne jette rien. Les lignes restent dans les fichiers Nginx. Test réel : 200/200 lignes après 5 min de coupure.

---

## 13. Configuration Loki + Garage

### 13.1 Clés importantes de `loki/config.yml`

| Clé | Valeur | Sens |
|---|---|---|
| `client_auth_type` | `RequireAndVerifyClientCert` | mTLS obligatoire |
| `common.storage.s3` | endpoint Garage | Stockage distant |
| `s3forcepathstyle` | `true` | Obligatoire pour Garage |
| `region` | `garage` | Région S3 Garage |
| `schema v13` + `tsdb` | — | Format moderne |
| `retention_period` | `336h` | 14 jours |
| `reject_old_samples_max_age` | `168h` | Refuse > 7 jours |
| `max_query_series` | `50000` | Protège contre les scans |
| `delete_request_store` | `s3` | Suppressions via Garage |

### 13.2 Bascule filesystem → Garage

Avant : chunks dans `/var/lib/loki`  
Après : chunks dans le bucket `loki-chunks` sur Garage  

Les ~160 Ko d'historique local de test n'ont pas été migrés. Alloy continue d'envoyer les nouvelles lignes.

---

## 14. Grafana : datasource et dashboard

### 14.1 Datasource `Loki-Nginx`

- UID : `loki-nginx`
- URL : `https://127.0.0.1:3100`
- mTLS via fichiers dans `/etc/grafana/pki/`
- L'ancienne datasource `loki` n'a **pas** été modifiée

### 14.2 Dashboard

- UID : `nginx-loki-analytics`
- Dossier : **Nginx**
- Généré par `python3 grafana/build_dashboard.py`
- Variables : site, environment, application, hostname, **volume minimum** (défaut 3)

Sections :

- **A** Vue générale  
- **B** Codes HTTP  
- **C** Top 20 URLs consultées  
- **D** Top 20 URLs lentes (P95)  
- **E** Visiteurs  
- **F** Erreurs  

---

## 15. Le langage LogQL (débutant)

Une requête se lit de gauche à droite :

```
{job="nginx-access", site=~"$site"} |~ `"status":"5"` | json uri="uri"
└──── 1. labels (rapide) ────┘   └ 2. filtre texte ┘  └ 3. parse ┘
```

| Brique | Exemple | Sens |
|---|---|---|
| Sélecteur | `{job="nginx-access"}` | Choisir les flux |
| Filtre | `\|~ "status\":\"404"` | Garder / exclure des lignes |
| Parse | `\| json uri="uri"` | Extraire un champ |
| Unwrap | `\| unwrap request_time` | Champ → nombre |
| Agrégation | `sum by (uri) (...)` | Grouper |
| Top | `topk(20, ...)` | Les 20 plus grands |
| Stats | `quantile_over_time(0.95, ...)` | P95 |

Variables Grafana : `$__range` (période), `$__auto` (pas du graphique).

---

## 16. Le dashboard panneau par panneau

### Section D — URLs les plus lentes (le plus demandé)

Cinq colonnes fusionnées :

1. **Nombre** de requêtes (hors 4xx)  
2. **Temps moyen** (`request_time`)  
3. **P95** ← critère de classement  
4. **Max**  
5. **Upstream P95** (temps de l'application)  

Filtre : au moins `$min_requests` requêtes (défaut 3), sinon le P95 n'est pas représentatif.

Si le tableau est vide : baisser le volume minimum ou élargir la période.

---

## 17. Vérifications et tests réalisés

### Au banc (avant installation)

- mTLS refuse les clients sans certificat  
- Labels exacts : 5 seulement  
- Filtrage statiques / sondes  
- Comptages, moyenne, P95 exacts  
- Coupure + rotation : 0 perte  
- Bande passante ≈ 93 octets/ligne sur le réseau  

### Sur les VM (après installation)

| Test | Résultat |
|---|---|
| `run_validation.py` | **25/25** |
| Coupure 5 min (`outage_test.sh`) | 200/200, 0 perte |
| Bande passante 3000 req | **85 % de réduction** |
| Garage | objets présents dans `loki-chunks` |
| Dashboard | données visibles |

---

## 18. Bande passante et passage à 35 sites

Mesure réelle : ~85–93 octets/ligne sur le réseau (TLS inclus), vs ~400 octets bruts.

| Scénario 35 sites | Réseau vers Loki | Disque 14 j |
|---|---|---|
| 1 000 req/j/site | ~3 Mo/j | ~20 Mo |
| 100 000 req/j/site | ~310 Mo/j (~30 kbit/s) | ~2 Go |
| 1 M req/j/site | ~3 Go/j | ~20 Go |

Pour ajouter un site : nouveau certificat client, IP dans le pare-feu Loki, même `VISITOR_SALT`, labels adaptés.

---

## 19. Commandes du quotidien

```bash
# Frontend
systemctl status alloy nginx
journalctl -u alloy -f
curl -s 127.0.0.1:12345/metrics | grep loki_write_sent_entries

# Centrale
sudo systemctl status loki grafana-server
sudo curl -s --cacert /root/loki-pki/ca.crt \
  --cert /root/loki-pki/client-grafana.crt \
  --key /root/loki-pki/client-grafana.key https://127.0.0.1:3100/ready

# Garage
ssh root@172.18.40.129 'systemctl status garage; garage bucket info loki-chunks'
```

**À sauvegarder absolument :** `/root/loki-pki/ca.key` et `VISITOR_SALT`.

---

## 20. Dépannage rapide

| Symptôme | Cause probable | Action |
|---|---|---|
| Dashboard vide | Période / variables / Alloy arrêté | Explore `{job="nginx-access"}` |
| Erreur certificat | Certificat expiré / mauvaise CA | `openssl x509 -in ... -noout -enddate` |
| Alloy : connection refused | Pare-feu ou Loki down | `systemctl status loki` + iptables |
| `timestamp too old` | Lignes > 7 jours | Normal au 1er démarrage sur vieux error.log |
| URLs lentes vides | Volume minimum trop haut | Baisser à 1 ou 3 |
| Garage injoignable | nftables / service | `garage status` ; ping 3900 depuis centrale |

---

## 21. Ce qui a été volontairement exclu

- **Promtail** → remplacé par Alloy  
- **MinIO** → remplacé par Garage (MinIO Community n'est plus maintenu)  
- **Docker Compose** sur la centrale → RPM/systemd (VM hors ligne + Grafana déjà natif)  
- **Elasticsearch / Kafka / Tempo** → aucun besoin démontré  
- **IP complète en label** → cardinalité + confidentialité  
- **Query strings** → sécurité  

---

## Annexes — Fichiers du dépôt

| Chemin | Contenu |
|---|---|
| `docs/01` … `09` | Analyses, install, paramètres, LogQL, tests, guide, Garage |
| `nginx/` | Format JSON + logrotate |
| `alloy/` | config.alloy + default + systemd |
| `loki/` | config.yml + firewall + systemd |
| `garage/` | service + nftables + template toml |
| `pki/gen-certs.sh` | Génération CA + certificats |
| `grafana/` | datasource + dashboard + générateur |
| `tests/` | Validation bout-en-bout |

**Dashboard production :**  
http://192.168.23.24:3000/d/nginx-loki-analytics

---

*Fin du document — Plateforme opérationnelle au 17/11/2026.*
