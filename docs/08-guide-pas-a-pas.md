# Guide complet pas à pas — Collecte des logs Nginx avec Alloy, Loki et Grafana

Ce guide explique **comment refaire vous-même toute l'installation, à la main**, et surtout **pourquoi** chaque élément existe. Il suit exactement ce qui a été installé le 13/11/2026 sur :

- la **VM frontend** `192.168.206.112` (Debian 13, Nginx), qui a accès à Internet ;
- la **VM centrale** `192.168.23.24` (AlmaLinux 8, Grafana 12.0.1), qui n'a **pas** accès à Internet.

Tous les fichiers cités se trouvent dans ce dépôt. Les chemins comme `nginx/conf.d/...` sont relatifs à sa racine.

---

## Sommaire

1. [Comprendre l'architecture](#1-comprendre-larchitecture)
2. [Rôle de chaque outil et où l'installer](#2-rôle-de-chaque-outil-et-où-linstaller)
3. [Préparation](#3-préparation)
4. [VM centrale — Pare-feu du port 3100](#4-vm-centrale--pare-feu-du-port-3100)
5. [VM centrale — Certificats (PKI et TLS mutuel)](#5-vm-centrale--certificats-pki-et-tls-mutuel)
6. [VM centrale — Installation de Loki](#6-vm-centrale--installation-de-loki)
7. [VM centrale — Grafana (datasource + dashboard)](#7-vm-centrale--grafana-datasource--dashboard)
8. [Serveur web — logrotate](#8-serveur-web--logrotate)
9. [Serveur web — Format de log JSON dans Nginx](#9-serveur-web--format-de-log-json-dans-nginx)
10. [Serveur web — Installation d'Alloy](#10-serveur-web--installation-dalloy)
11. [Serveur web — La configuration Alloy expliquée bloc par bloc](#11-serveur-web--la-configuration-alloy-expliquée-bloc-par-bloc)
12. [Vérification de bout en bout](#12-vérification-de-bout-en-bout)
13. [Le langage LogQL expliqué](#13-le-langage-logql-expliqué)
14. [Le dashboard expliqué panneau par panneau](#14-le-dashboard-expliqué-panneau-par-panneau)
15. [Ajouter un nouveau site (jusqu'à 35 sites)](#15-ajouter-un-nouveau-site-jusquà-35-sites)
16. [Exploitation au quotidien](#16-exploitation-au-quotidien)
17. [Glossaire](#17-glossaire)

---

## 1. Comprendre l'architecture

### 1.1 Le parcours d'une requête HTTP, de bout en bout

```
 Navigateur du visiteur
        │  GET /page?token=abc
        ▼
 Nginx Proxy Manager (192.168.206.40)      ← reverse proxy : ajoute X-Forwarded-For
        │
        ▼
 Nginx (VM frontend 192.168.206.112)
        │  écrit 1 ligne JSON dans /var/log/nginx/access_json.log
        │  (sans la query string "?token=abc", sans cookie, sans mot de passe)
        ▼
 Grafana Alloy (même VM frontend)
        │  1. lit le fichier (comme "tail -f") et retient sa position sur disque
        │  2. supprime les fichiers statiques réussis (.css, .js, .png… en 2xx/3xx)
        │  3. tronque l'IP (/24), calcule une empreinte visiteur anonyme
        │  4. regroupe les lignes en lots (5 s ou 1 Mio) et les compresse (snappy)
        │  5. envoie en HTTPS avec un certificat client (TLS mutuel)
        ▼
 Pare-feu iptables (VM centrale) : port 3100 ouvert UNIQUEMENT aux serveurs web
        ▼
 Grafana Loki (VM centrale 192.168.23.24, port 3100)
        │  stocke les lignes compressées dans /var/lib/loki, 14 jours
        ▼
 Grafana (même VM centrale, port 3000)
        │  interroge Loki en LogQL (avec son propre certificat client)
        ▼
 Dashboard « Nginx — Analyse du trafic (Loki) »
```

### 1.2 Les trois idées clés

1. **On ne transporte que le strict nécessaire.** Alloy filtre, allège et compresse **avant** l'envoi. Mesure réelle : environ 90 octets par requête sur le réseau, contre environ 400 octets pour la ligne brute.
2. **Loki n'indexe pas le contenu des logs.** Il indexe seulement quelques *labels* (étiquettes) : `site`, `environment`, `hostname`, `application`, `job`. L'URL, le code HTTP et le temps de réponse restent dans le texte de la ligne et sont extraits **au moment de la requête**. C'est pourquoi Loki est léger, mais il faut écrire les requêtes correctement (voir §13).
3. **Rien n'est perdu en cas de coupure.** Le fichier de log Nginx sert de tampon. Alloy mémorise sa position de lecture et reprend là où il s'était arrêté dès que Loki redevient joignable.

---

## 2. Rôle de chaque outil et où l'installer

| Outil | Version | Où | Rôle |
|---|---|---|---|
| **Nginx** | 1.26.3 (existant) | Chaque serveur web | Serveur web. On lui ajoute un **second** fichier de log au format JSON. |
| **logrotate** | 3.22 | Chaque serveur web | Fait « tourner » les logs chaque jour et garde 7 jours. C'est le **tampon local** en cas de coupure. Il n'était pas installé. |
| **Grafana Alloy** | 1.21.3 | Chaque serveur web | Agent de collecte : lecture, filtrage, anonymisation, lots, compression, envoi, reprise. Remplace Promtail. |
| **Grafana Loki** | 3.7.10 | VM centrale **uniquement** | Base de données de logs. Reçoit, stocke et répond aux requêtes LogQL. |
| **Grafana** | 12.0.1 (existant) | VM centrale | Interface : datasource Loki et dashboard. |
| **iptables** (service `loki-firewall`) | système | VM centrale | N'autorise que les serveurs web à joindre le port 3100. |
| **OpenSSL** (`pki/gen-certs.sh`) | système | VM centrale | Crée l'autorité de certification interne et les certificats du TLS mutuel. |
| **systemd** | système | Les deux VM | Démarre les services et limite leur mémoire et leur CPU. |

**Ce qu'on n'installe pas, et pourquoi :**
- Pas de Loki ni de Grafana sur les serveurs web : un seul point central.
- Pas de MinIO : Loki stocke sur le disque local (mode *filesystem*), ce qui suffit pour ce volume.
- Pas de Docker : la VM centrale n'a ni Docker ni Internet, et Grafana y est déjà installé en paquet natif. Des paquets RPM/DEB avec systemd sont plus simples à maintenir.
- Pas de Prometheus, Kafka ni Elasticsearch : aucun besoin démontré.

### 2.1 Flux réseau

| Source | Destination | Port | Pourquoi |
|---|---|---|---|
| Serveurs web (Alloy) | VM centrale | **TCP 3100** (HTTPS, TLS mutuel) | Envoi des logs |
| Grafana | Loki (même VM, `127.0.0.1`) | TCP 3100 | Requêtes du dashboard |
| Administrateurs | VM centrale | TCP 3000 | Interface Grafana |
| Serveurs web | `apt.grafana.com` | TCP 443 sortant | Installation d'Alloy |
| Loki | lui-même (`127.0.0.1`) | TCP 9096 (gRPC) | Communication interne, jamais exposée |
| Alloy | lui-même (`127.0.0.1`) | TCP 12345 | Interface de debug et métriques, locale uniquement |

---

## 3. Préparation

### 3.1 Vérifications sur la VM centrale

```bash
ssh m.temmali@192.168.23.24
cat /etc/os-release | head -2          # AlmaLinux 8
df -h /                                # au moins 20 Go libres pour /var/lib/loki
free -h                                # au moins 2 Go disponibles pour Loki
sudo ss -lntp | grep -E ':3100|:9096'  # rien ne doit écouter sur ces ports
chronyc tracking | grep Leap           # "Normal" attendu (voir remarque)
```

> **Horloge.** Loki classe les logs par heure. Une VM mal réglée produit des graphiques décalés. Sur la VM centrale, chrony n'est pas synchronisé (pas d'accès aux serveurs NTP), et le décalage mesuré était de +4,6 s. Loki le tolère, mais il faut demander l'ouverture du port **UDP 123** vers les NTP internes `192.168.173.5` et `192.168.173.6`, puis les déclarer dans `/etc/chrony.conf` (`server 192.168.173.5 iburst`).

### 3.2 Télécharger Loki pour la VM sans Internet

La VM centrale n'a pas Internet. On télécharge donc le paquet sur la VM frontend (ou sur votre PC), puis on le copie.

Sur la **VM frontend** (qui a Internet) :

```bash
cd /tmp
wget https://github.com/grafana/loki/releases/download/v3.7.10/loki-3.7.10-1.x86_64.rpm
sha256sum loki-3.7.10-1.x86_64.rpm
# doit afficher : 900dcd39fa6365046fb9f371501a1f9a2067a404c9182e38ac2eb666a1542510
```

> **Pourquoi vérifier l'empreinte SHA-256 ?** Pour être sûr que le fichier n'a été ni corrompu ni modifié pendant le transfert. Si l'empreinte diffère, ne l'installez pas.

Copiez ensuite le RPM et les dossiers `loki/`, `pki/` et `grafana/` du dépôt vers la VM centrale, en passant par votre PC :

```bash
# depuis votre PC, dans le dossier du dépôt
scp root@192.168.206.112:/tmp/loki-3.7.10-1.x86_64.rpm offline-packages/
ssh m.temmali@192.168.23.24 'mkdir -p ~/loki-deploy'
scp -r offline-packages/loki-3.7.10-1.x86_64.rpm loki pki grafana m.temmali@192.168.23.24:~/loki-deploy/
```

> Si vous éditez les fichiers sous Windows, convertissez les fins de ligne avant de les exécuter : `sed -i 's/\r$//' ~/loki-deploy/loki/firewall/* ~/loki-deploy/pki/*.sh`

---

## 4. VM centrale — Pare-feu du port 3100

### 4.1 Pourquoi d'abord le pare-feu ?

Le paquet RPM de Loki **démarre Loki tout de suite** à l'installation, avec sa configuration par défaut : sans TLS, sans authentification, sur toutes les interfaces. Si le pare-feu n'est pas déjà en place, n'importe quelle machine du réseau pourrait écrire dans Loki pendant quelques minutes.

### 4.2 Ce que fait le script `loki/firewall/loki-firewall.sh`

Il crée une **chaîne iptables dédiée** nommée `LOKI_INGRESS`, puis il redirige vers elle **uniquement** le trafic TCP à destination du port 3100. Les autres services de la VM (MySQL, Grafana, SSH…) ne sont pas touchés.

```
INPUT ──(tcp dport 3100 ?)──► LOKI_INGRESS
                                 ├─ venant de lo (la VM elle-même)  → ACCEPT  (Grafana)
                                 ├─ venant de 192.168.206.112       → ACCEPT  (serveur web)
                                 └─ tout le reste                   → DROP
```

| Ligne du script | Effet |
|---|---|
| `iptables -N LOKI_INGRESS` ou `-F` | Crée la chaîne, ou la vide si elle existe déjà |
| `-A LOKI_INGRESS -i lo -j ACCEPT` | Autorise les connexions locales (Grafana → Loki) |
| boucle `-s $src -j ACCEPT` | Autorise chaque IP listée dans `/etc/sysconfig/loki-firewall` |
| `-A LOKI_INGRESS -j DROP` | Bloque tout le reste |
| `-I INPUT 1 -p tcp --dport 3100 -j LOKI_INGRESS` | Envoie le trafic du port 3100 vers la chaîne, en première position |
| `stop` | Retire proprement le saut et la chaîne |

Le fichier `loki/firewall/loki-firewall.sysconfig` contient la liste des IP autorisées :

```bash
LOKI_ALLOWED_SOURCES="192.168.206.112"
```

Le service systemd `loki-firewall.service` lance le script au démarrage de la VM, **avant** Loki (`Before=loki.service`).

### 4.3 Installation manuelle

```bash
cd ~/loki-deploy
sudo install -m 0750 loki/firewall/loki-firewall.sh        /usr/local/sbin/loki-firewall.sh
sudo install -m 0644 loki/firewall/loki-firewall.sysconfig /etc/sysconfig/loki-firewall
sudo install -m 0644 loki/firewall/loki-firewall.service   /etc/systemd/system/loki-firewall.service
sudo systemctl daemon-reload                 # systemd relit ses fichiers d'unités
sudo systemctl enable --now loki-firewall    # active au démarrage + démarre maintenant
```

> `install -m 0750` copie le fichier **et** fixe ses droits en une seule commande. 0750 signifie : lecture, écriture et exécution pour root, lecture et exécution pour le groupe, rien pour les autres.

### 4.4 Vérification

```bash
sudo iptables -L LOKI_INGRESS -n -v
# ACCEPT lo / ACCEPT 192.168.206.112 / DROP
sudo iptables -L INPUT -n --line-numbers | head -3
# ligne 1 : LOKI_INGRESS tcp dpt:3100
```

---

## 5. VM centrale — Certificats (PKI et TLS mutuel)

### 5.1 Le principe du TLS mutuel (mTLS)

Avec le HTTPS classique, seul le **serveur** prouve son identité. Avec le **TLS mutuel**, le **client** doit aussi présenter un certificat signé par une autorité que le serveur reconnaît. Loki refuse donc toute connexion qui n'a pas de certificat valide, même si elle passait le pare-feu.

```
              Autorité de certification interne (ca.crt + ca.key)
                     │ signe
     ┌───────────────┼──────────────────────────┐
  loki.crt      client-AH-NOC-FRONT-TEST.crt   client-grafana.crt
  (serveur)     (Alloy du serveur web)         (Grafana)
```

| Fichier | Rôle | Où il va | Secret ? |
|---|---|---|---|
| `ca.crt` | Certificat de l'autorité, pour vérifier les autres | Loki, Grafana, chaque Alloy | Non |
| `ca.key` | Clé de l'autorité, sert à signer de nouveaux certificats | **Reste dans `/root/loki-pki`**, à sauvegarder | **Oui, très sensible** |
| `loki.crt` / `loki.key` | Identité du serveur Loki (valable pour `192.168.23.24`, `127.0.0.1`, `central`, `localhost`) | `/etc/loki/pki/` | La clé, oui |
| `client-<serveur>.crt/.key` | Identité de l'Alloy d'un serveur web | `/etc/alloy/pki/` de ce serveur | La clé, oui |
| `client-grafana.crt/.key` | Identité de Grafana auprès de Loki | `/etc/grafana/pki/` | La clé, oui |

### 5.2 Le script `pki/gen-certs.sh`

Utilisation : `gen-certs.sh <dossier> <IP de Loki> <nom DNS de Loki> <client1> <client2> ...`

1. S'il n'y a pas encore de `ca.key`, il crée l'autorité (clé RSA 4096 bits, 10 ans), avec les extensions `keyUsage` et identifiants de clé exigées par les clients stricts.
2. Il crée `loki.crt` (usage *serverAuth*, 825 jours) s'il n'existe pas.
3. Il crée un certificat *clientAuth* pour chaque nom passé en argument, s'il n'existe pas.
4. Il **réutilise** toujours l'autorité existante. On peut donc le relancer plus tard pour ajouter un serveur web.

```bash
sudo bash pki/gen-certs.sh /root/loki-pki 192.168.23.24 central AH-NOC-FRONT-TEST grafana
sudo ls -l /root/loki-pki
```

> **Note.** L'autorité installée le 13/11/2026 a été créée par une version antérieure du script, sans l'extension `keyUsage`. Alloy, Loki et Grafana l'acceptent, mais Python 3.13+ la refuse en mode strict. Les nouvelles autorités générées par le script actuel sont conformes.

> **Expiration.** Les certificats sont valables 825 jours (jusqu'en février 2029). Pour vérifier une date : `openssl x509 -in fichier.crt -noout -enddate`.

---

## 6. VM centrale — Installation de Loki

### 6.1 Installer le paquet

```bash
cd ~/loki-deploy
sha256sum loki-3.7.10-1.x86_64.rpm         # revérifier l'empreinte
sudo rpm -ivh loki-3.7.10-1.x86_64.rpm     # installe /usr/bin/loki, l'utilisateur loki, le service
sudo systemctl stop loki                   # arrête le Loki démarré par le RPM avec la config par défaut
sudo rm -rf /tmp/loki                      # supprime les données de ce démarrage par défaut
sudo cp -a /etc/loki/config.yml /etc/loki/config.yml.rpm-orig   # sauvegarde la config d'origine
```

### 6.2 Installer les certificats du serveur Loki

```bash
sudo install -d -m 0750 -o root -g loki /etc/loki/pki
sudo install -m 0644 -o root -g loki /root/loki-pki/ca.crt   /etc/loki/pki/ca.crt
sudo install -m 0644 -o root -g loki /root/loki-pki/loki.crt /etc/loki/pki/loki.crt
sudo install -m 0640 -o root -g loki /root/loki-pki/loki.key /etc/loki/pki/loki.key
```

La clé est en `0640 root:loki` : seul l'utilisateur `loki` peut la lire, en plus de root.

### 6.3 La configuration `loki/config.yml` expliquée

| Section / clé | Valeur | Explication |
|---|---|---|
| `auth_enabled` | `false` | Un seul « locataire » (tenant). L'authentification est assurée par le TLS mutuel et le pare-feu. |
| `server.http_listen_port` | `3100` | Port d'écoute HTTP(S) pour l'envoi et les requêtes. |
| `server.grpc_listen_address` | `127.0.0.1` | Le port interne gRPC 9096 n'est jamais accessible depuis le réseau. |
| `server.http_server_read/write_timeout` | `2m` | Laisse le temps aux requêtes lourdes du dashboard. |
| `server.tls_min_version` | `VersionTLS12` | Refuse les versions TLS obsolètes. |
| `server.http_tls_config.cert_file/key_file` | `/etc/loki/pki/loki.*` | Identité TLS du serveur. |
| `server.http_tls_config.client_auth_type` | `RequireAndVerifyClientCert` | **Certificat client obligatoire**, et il doit être signé par `client_ca_file`. |
| `common.path_prefix` | `/var/lib/loki` | Dossier de toutes les données. |
| `common.replication_factor` | `1` | Une seule instance, donc pas de réplication. |
| `common.ring.kvstore.store` | `inmemory` | Pas de cluster, donc pas besoin de Consul ou etcd. |
| `common.storage.filesystem` | `chunks`, `rules` | Stockage sur disque local (pas de S3 ni MinIO). |
| `schema_config` | `tsdb`, `v13`, index par 24h, depuis `2026-11-01` | Format de stockage recommandé pour Loki 3.x. **Ne jamais modifier une ligne existante** : pour changer de format, on ajoute une nouvelle ligne avec une date future. |
| `ingester.chunk_target_size` | 1,5 Mo | Taille visée des blocs compressés : des blocs plus gros donnent moins de fichiers et une meilleure compression. |
| `ingester.chunk_idle_period` / `max_chunk_age` | `1h` / `2h` | Un bloc est écrit sur disque après 1 h sans nouvelle ligne, ou au plus tard après 2 h. |
| `compactor.retention_enabled` | `true` | Active la suppression automatique des données anciennes. |
| `compactor.retention_delete_delay` | `2h` | Délai de sécurité avant la suppression physique. |
| `compactor.delete_request_store` | `filesystem` | Permet aussi les suppressions à la demande (API `delete`). |
| `limits_config.retention_period` | `336h` | **Rétention de 14 jours.** |
| `reject_old_samples_max_age` | `168h` | Refuse les lignes de plus de 7 jours. C'est cohérent avec les 7 jours de logrotate. |
| `ingestion_rate_mb` / `ingestion_burst_size_mb` | 8 / 16 Mo/s | Débit maximal accepté, avec de la marge pour la reprise après une coupure. |
| `max_label_names_per_series` | `8` | Garde-fou contre l'ajout accidentel de labels. |
| `discover_service_name: []` / `discover_log_levels: false` | — | Empêche Loki d'ajouter des labels automatiques (`service_name`, `level`). |
| `max_query_series` | `50000` | Limite le nombre de séries (par exemple d'URLs différentes) qu'une requête peut agréger. Protège la RAM en cas de scan de vulnérabilités. |
| `split_queries_by_interval` | `1h` | Découpe une requête longue en morceaux d'une heure, traités en parallèle et mis en cache. |
| `query_timeout` | `2m` | Durée maximale d'une requête. |
| `results_cache.embedded_cache` | 200 Mo | Cache en mémoire des résultats, pour accélérer les rafraîchissements du dashboard. |
| `pattern_ingester.enabled` | `false` | Fonction inutile ici. |
| `analytics.reporting_enabled` | `false` | Pas de télémétrie envoyée à Grafana Labs. |

### 6.4 Les limites systemd `loki/systemd/loki.service.d/override.conf`

Un fichier *override* complète le service fourni par le RPM **sans le modifier**. Ainsi, une mise à jour du paquet ne l'écrase pas.

| Directive | Explication |
|---|---|
| `After=` / `Requires=loki-firewall.service` | Loki ne démarre qu'après le pare-feu. |
| `MemoryLimit=2G` (cgroup v1) / `MemoryMax=2G` | Loki ne peut pas dépasser 2 Go de RAM. La VM est en production. |
| `CPUQuota=200%` | 2 cœurs au maximum. |
| `GOMEMLIMIT=1800MiB` | Loki (écrit en Go) libère sa mémoire avant d'atteindre la limite. |
| `LimitNOFILE=65536` | Nombre de fichiers ouverts suffisant. |
| `NoNewPrivileges`, `ProtectSystem=full`, `ProtectHome`, `PrivateTmp`, `ReadWritePaths=/var/lib/loki` | Durcissement : Loki ne peut écrire que dans son dossier de données. |

### 6.5 Installation, vérification, démarrage

```bash
sudo install -m 0640 -o root -g loki loki/config.yml /etc/loki/config.yml
sudo install -d -m 0750 -o loki -g loki /var/lib/loki
sudo install -d /etc/systemd/system/loki.service.d
sudo install -m 0644 loki/systemd/loki.service.d/override.conf /etc/systemd/system/loki.service.d/override.conf

sudo -u loki /usr/bin/loki -config.file=/etc/loki/config.yml -verify-config
# attendu : msg="config is valid"   ← TOUJOURS vérifier avant de démarrer

sudo systemctl daemon-reload
sudo systemctl enable --now loki
```

### 6.6 Contrôles (attendre environ 30 s)

```bash
P=/root/loki-pki
# Avec le certificat client de Grafana : doit répondre "ready"
sudo curl -s --cacert $P/ca.crt --cert $P/client-grafana.crt --key $P/client-grafana.key https://127.0.0.1:3100/ready
# Sans certificat client : doit échouer (code 000 = connexion refusée par TLS)
sudo curl -s -o /dev/null -w "%{http_code}\n" --cacert $P/ca.crt https://127.0.0.1:3100/ready
# Ports : 3100 sur toutes les interfaces, 9096 sur 127.0.0.1 seulement
sudo ss -lntp | grep -E ':3100|:9096'
# Journal : pas d'erreur répétée
sudo journalctl -u loki --since "-5 min" --no-pager | grep -i error
```

> Juste après le démarrage, `Ingester not ready: waiting for 15s` et une erreur `empty ring` sont normales. Elles disparaissent en quelques secondes.

---

## 7. VM centrale — Grafana (datasource + dashboard)

### 7.1 Certificat client de Grafana

```bash
sudo install -d -m 0750 -o root -g grafana /etc/grafana/pki
sudo install -m 0644 -o root -g grafana /root/loki-pki/ca.crt             /etc/grafana/pki/ca.crt
sudo install -m 0644 -o root -g grafana /root/loki-pki/client-grafana.crt /etc/grafana/pki/client-grafana.crt
sudo install -m 0640 -o root -g grafana /root/loki-pki/client-grafana.key /etc/grafana/pki/client-grafana.key
```

### 7.2 La datasource « provisionnée »

Une datasource *provisionnée* est déclarée dans un fichier YAML que Grafana lit au démarrage, au lieu d'être créée à la main dans l'interface. Elle est reproductible et ne peut pas être modifiée par erreur dans l'interface (`editable: false`).

`grafana/provisioning/datasources/loki-nginx.yaml` :

| Clé | Explication |
|---|---|
| `name: Loki-Nginx`, `uid: loki-nginx` | Nom affiché et identifiant fixe, utilisé par le dashboard. |
| `url: https://127.0.0.1:3100` | Loki est sur la même VM. |
| `access: proxy` | C'est le serveur Grafana qui interroge Loki, pas le navigateur de l'utilisateur. Loki reste donc invisible des postes. |
| `tlsAuth: true` | Grafana présente un certificat client (mTLS). |
| `tlsAuthWithCACert: true` | Grafana vérifie le certificat de Loki avec notre autorité. |
| `maxLines: 1000`, `timeout: 120` | Nombre maximal de lignes affichées dans un panneau de logs, et délai maximal d'une requête. |
| `$__file{...}` | Grafana lit le contenu du fichier au démarrage. Les clés n'apparaissent donc pas dans le YAML. |

```bash
sudo install -m 0640 -o root -g grafana grafana/provisioning/datasources/loki-nginx.yaml /etc/grafana/provisioning/datasources/loki-nginx.yaml
sudo systemctl restart grafana-server       # environ 10 s d'indisponibilité de Grafana
```

Vérifiez dans Grafana : **Connections → Data sources → Loki-Nginx → Test**. Le message attendu est « Data source successfully connected ».

> L'ancienne datasource `loki` (vers `http://localhost:3100`) n'a pas été modifiée. N'utilisez que `Loki-Nginx`.

### 7.3 Importer le dashboard (interface web)

1. **Dashboards → New → New folder**, nom `Nginx`.
2. **Dashboards → New → Import → Upload dashboard JSON file**, puis choisissez `grafana/dashboards/nginx-analytics-loki.json`.
3. Choisissez le dossier `Nginx`, puis **Import**.

Le fichier JSON est **généré** par `grafana/build_dashboard.py`. Si vous modifiez une requête, modifiez ce script, lancez `python3 grafana/build_dashboard.py`, puis réimportez le fichier en écrasant l'ancien (même uid `nginx-loki-analytics`).

### 7.4 Droits d'accès

- Dossier `Nginx` → **Permissions** : donnez *View* aux équipes qui consultent et *Edit* aux seuls administrateurs du dashboard.
- Gardez `[auth.anonymous] enabled = false` dans `/etc/grafana/grafana.ini`, et utilisez des comptes nominatifs.

---

## 8. Serveur web — logrotate

### 8.1 Pourquoi c'est indispensable

Sans rotation, le fichier de log grossit sans fin. Sur le frontend, `access.log` faisait déjà 100 Mo, car le paquet `logrotate` n'était pas installé. La rotation définit aussi le **tampon local** : si Loki est injoignable, les lignes attendent dans les fichiers. Avec `rotate 7`, on garde 7 jours.

### 8.2 Le fichier `nginx/logrotate-nginx` expliqué

| Directive | Effet |
|---|---|
| `daily` | Rotation chaque jour (`access_json.log` devient `access_json.log.1`, etc.). |
| `rotate 7` | Garde 7 anciens fichiers, soit 7 jours, aligné sur les 7 jours acceptés par Loki. |
| `maxsize 500M` | Rotation anticipée si un fichier dépasse 500 Mo (par exemple pendant un scan). |
| `compress` + `delaycompress` | Compresse les anciens fichiers, sauf le plus récent (`.1`), qui reste lisible. Alloy peut ainsi finir de le lire. |
| `create 0640 www-data adm` | Le nouveau fichier est lisible par le groupe `adm`, dont l'utilisateur `alloy` fait partie. |
| `postrotate invoke-rc.d nginx rotate` | Demande à Nginx de rouvrir ses fichiers (signal USR1), sans coupure. |

### 8.3 Installation

```bash
apt-get update && apt-get install -y logrotate
cp -a /etc/logrotate.d/nginx /root/logrotate-nginx.orig       # sauvegarde
install -m 0644 nginx/logrotate-nginx /etc/logrotate.d/nginx
logrotate -d /etc/logrotate.d/nginx      # -d = simulation, ne modifie rien
systemctl list-timers logrotate.timer    # le passage quotidien est programmé
```

---

## 9. Serveur web — Format de log JSON dans Nginx

### 9.1 Pourquoi du JSON ?

Le format `combined` par défaut est un texte libre, difficile à découper sans ambiguïté (guillemets dans le User-Agent, champs vides…). En JSON, chaque champ est nommé. Avec `escape=json`, Nginx échappe lui-même les caractères spéciaux, si bien que la ligne est **toujours** un JSON valide. Alloy et Loki la lisent ensuite sans regex fragile.

### 9.2 Le fichier `nginx/conf.d/10-json-analytics-log.conf` expliqué

**a) La vraie IP du visiteur derrière le reverse proxy**

```nginx
set_real_ip_from 192.168.206.40;     # on fait confiance à ce proxy (Nginx Proxy Manager)
real_ip_header   X-Forwarded-For;    # l'IP réelle est dans cet en-tête
real_ip_recursive on;                # prend la dernière IP non fiable de la chaîne
```

Sans ces lignes, toutes les requêtes sembleraient venir du proxy, et le comptage des visiteurs serait faux. Pour ajouter un proxy, ajoutez une ligne `set_real_ip_from`.

**b) Le chemin sans query string**

```nginx
map $request_uri $request_path_noquery {
    "~^(?<rp_path>[^?]*)"  $rp_path;   # tout ce qui précède le "?"
    default                "-";
}
```

`/login?user=bob&password=secret` est enregistré comme `/login`. Les mots de passe, jetons et adresses e-mail passés dans l'URL ne sont **jamais** écrits sur disque. On n'utilise pas `$uri`, car Nginx le modifie lors des redirections internes (`try_files`).

**c) Le format JSON**

| Champ | Variable Nginx | Sens |
|---|---|---|
| `timestamp` | `$time_iso8601` | Date et heure de la requête (ex. `2026-11-13T18:56:18+00:00`) |
| `server_name` | `$server_name` | Site (bloc `server`) qui a répondu |
| `remote_addr` | `$remote_addr` | IP du client, corrigée par *realip* (elle sera tronquée par Alloy) |
| `request_method` | `$request_method` | GET, POST… |
| `uri` | `$request_path_noquery` | Chemin sans paramètres |
| `status` | `$status` | Code HTTP (200, 404, 500…) |
| `body_bytes_sent` | `$body_bytes_sent` | Taille de la réponse envoyée |
| `request_time` | `$request_time` | **Temps total** en secondes, de la réception à l'envoi du dernier octet |
| `upstream_response_time` | `$upstream_response_time` | Temps de réponse de l'application derrière Nginx (vide `""` si Nginx a servi seul) |
| `http_user_agent` | `$http_user_agent` | Navigateur ou outil |
| `request_id` | `$request_id` | Identifiant unique de la requête, utile pour retrouver une erreur |

Ce qui n'est **jamais** journalisé : cookies, en-tête `Authorization`, corps des requêtes, referer, query string.

**d) Le fichier de sortie**

```nginx
access_log /var/log/nginx/access_json.log json_analytics;
```

Ce fichier s'ajoute à l'`access.log` existant, qui n'est pas modifié.

### 9.3 Installation et contrôle

```bash
install -m 0644 nginx/conf.d/10-json-analytics-log.conf /etc/nginx/conf.d/
nginx -t                      # TOUJOURS tester avant de recharger
systemctl reload nginx        # rechargement sans coupure
chown www-data:adm /var/log/nginx/access_json.log && chmod 0640 /var/log/nginx/access_json.log

curl -sk "https://127.0.0.1/?password=secret" -o /dev/null
tail -n 1 /var/log/nginx/access_json.log | python3 -m json.tool   # JSON valide
grep -c secret /var/log/nginx/access_json.log                     # 0 attendu
```

---

## 10. Serveur web — Installation d'Alloy

### 10.1 Ajouter le dépôt officiel Grafana et installer

```bash
apt-get install -y gpg wget
mkdir -p /etc/apt/keyrings
wget -O /etc/apt/keyrings/grafana.asc https://apt.grafana.com/gpg-full.key   # clé de signature
chmod 644 /etc/apt/keyrings/grafana.asc
echo "deb [signed-by=/etc/apt/keyrings/grafana.asc] https://apt.grafana.com stable main" > /etc/apt/sources.list.d/grafana.list
apt-get update
apt-get install -y alloy=1.21.3-1     # version précise, testée
apt-mark hold alloy                   # empêche une mise à jour automatique non testée
alloy --version
id alloy                              # doit contenir le groupe adm (lecture de /var/log/nginx)
```

Le paquet installe : le binaire `/usr/bin/alloy`, le service `alloy.service`, l'utilisateur `alloy`, le fichier `/etc/default/alloy`, et le dossier de données `/var/lib/alloy/data`, qui contient le **fichier de positions**.

### 10.2 Certificat client

Les fichiers `ca.crt`, `client-<HOSTNAME>.crt` et `client-<HOSTNAME>.key` sont générés sur la VM centrale (§5) et copiés sur le serveur web, sans les laisser traîner :

```bash
# depuis votre PC : copie directe de la VM centrale vers le serveur web
ssh m.temmali@192.168.23.24 'sudo tar -C /root/loki-pki -cf - ca.crt client-AH-NOC-FRONT-TEST.crt client-AH-NOC-FRONT-TEST.key' \
 | ssh root@192.168.206.112 'umask 077; mkdir -p /root/pki-in && tar -C /root/pki-in -xf -'

# sur le serveur web
cd /root/pki-in
install -d -m 0750 -o root -g alloy /etc/alloy/pki
install -m 0644 -o root -g alloy ca.crt                        /etc/alloy/pki/ca.crt
install -m 0644 -o root -g alloy client-AH-NOC-FRONT-TEST.crt  /etc/alloy/pki/client.crt
install -m 0640 -o root -g alloy client-AH-NOC-FRONT-TEST.key  /etc/alloy/pki/client.key
shred -u client-AH-NOC-FRONT-TEST.key && cd / && rm -rf /root/pki-in   # efface la copie de la clé
```

### 10.3 Le fichier `/etc/default/alloy` (`alloy/default-alloy`)

| Variable | Rôle |
|---|---|
| `CONFIG_FILE` | Chemin de la configuration Alloy. |
| `CUSTOM_ARGS="--server.http.listen-addr=127.0.0.1:12345 --disable-reporting"` | L'interface de debug n'écoute qu'en local, et aucune statistique n'est envoyée à Grafana Labs. |
| `LOKI_URL` | Adresse d'envoi : `https://192.168.23.24:3100/loki/api/v1/push`. |
| `VISITOR_SALT` | Secret utilisé pour anonymiser les visiteurs. **Il doit être identique sur tous les serveurs web**, sinon un visiteur qui passe par deux serveurs serait compté deux fois. Il est généré **une seule fois** avec `openssl rand -hex 32`, puis conservé en lieu sûr. |
| `RESTART_ON_UPGRADE=true` | Redémarre Alloy après une mise à jour du paquet. |

### 10.4 Les limites systemd `alloy/systemd/alloy.service.d/override.conf`

`MemoryMax=256M`, `CPUQuota=50%` et `GOMEMLIMIT=200MiB` : Alloy ne peut jamais pénaliser le serveur web. En pratique, il consomme environ 75 Mo et 0,5 % d'un cœur.

### 10.5 Installation et démarrage

```bash
install -m 0640 -o root -g alloy alloy/config.alloy  /etc/alloy/config.alloy
install -m 0640 -o root -g alloy alloy/default-alloy /etc/default/alloy
SALT=$(openssl rand -hex 32)        # 1er serveur uniquement ; ensuite, réutiliser le même sel
sed -i "s/^VISITOR_SALT=.*/VISITOR_SALT=\"$SALT\"/" /etc/default/alloy
install -d /etc/systemd/system/alloy.service.d
install -m 0644 alloy/systemd/alloy.service.d/override.conf /etc/systemd/system/alloy.service.d/

alloy fmt /etc/alloy/config.alloy > /dev/null && echo "syntaxe OK"   # vérifie la syntaxe
systemctl daemon-reload
systemctl enable --now alloy
curl -s 127.0.0.1:12345/-/ready     # "Alloy is ready."
```

> Au premier démarrage, Alloy lit `error.log` depuis le début. Loki refuse les lignes de plus de 7 jours, ce qui produit un message `status=400 ... timestamp too old`. C'est normal et cela n'arrive qu'une fois.

---

## 11. Serveur web — La configuration Alloy expliquée bloc par bloc

Alloy fonctionne comme une **chaîne de composants** reliés entre eux. Chaque composant transmet ses lignes au suivant via `forward_to` :

```
loki.source.file "nginx_access" ──► loki.process "nginx_access" ──┐
                                                                   ├──► loki.write "central" ──► Loki
loki.source.file "nginx_error"  ──► loki.process "nginx_error"  ──┘
```

### 11.1 `loki.source.file` — lire les fichiers

```alloy
loki.source.file "nginx_access" {
  targets = [{
    __path__    = "/var/log/nginx/access_json.log",   // fichier à lire
    job         = "nginx-access",                     // ┐
    site        = "noc",                              // │ les 5 LABELS Loki :
    environment = "test",                             // │ étiquettes fixes qui servent
    application = "noc-dashboard",                    // │ à filtrer dans Grafana
    hostname    = constants.hostname,                 // ┘ (nom de la machine, automatique)
  }]
  forward_to = [loki.process.nginx_access.receiver]
  file_match { enabled = true, sync_period = "10s" }   // revérifie le fichier toutes les 10 s
  on_positions_file_error = "restart_from_end"
}
```

- Alloy lit le fichier comme `tail -f` et enregistre sa **position** (l'octet atteint) dans `/var/lib/alloy/data`. Après un redémarrage ou une coupure, il reprend exactement à cette position, sans perte ni doublon.
- `file_match` : si logrotate remplace le fichier, Alloy le redécouvre en moins de 10 s. Il termine aussi la lecture de l'ancien fichier.
- `on_positions_file_error = "restart_from_end"` : si le fichier de positions est corrompu, Alloy repart de la fin plutôt que de tout relire, ce qui créerait des doublons.
- **Pourquoi si peu de labels ?** Chaque combinaison unique de labels crée un « flux » dans Loki. Avec l'IP ou l'URL en label, il y aurait des millions de flux, et Loki deviendrait lent et gourmand. Ici, il y a un flux par serveur et par fichier.

Le second bloc `nginx_error` fait la même chose pour `error.log`, avec `job = "nginx-error"`.

### 11.2 `loki.process "nginx_access"` — transformer chaque ligne

Les étapes (*stages*) s'exécutent **dans l'ordre**, pour chaque ligne :

| # | Stage | Ce qu'il fait | Pourquoi |
|---|---|---|---|
| 1 | `stage.label_drop { values = ["filename"] }` | Retire le label `filename` ajouté automatiquement | Garder uniquement nos 5 labels |
| 2 | `stage.json { expressions = {...}, drop_malformed = true }` | Extrait les 11 champs du JSON dans une mémoire temporaire. Une ligne illisible est écartée et comptée. | Pouvoir filtrer et transformer les champs |
| 3 | `stage.drop` (statiques) | Si `uri;status` correspond à `.css/.js/.png/...;2xx ou 3xx`, la ligne est **supprimée** | Les fichiers statiques réussis n'apportent rien. Les **404/500 sur des statiques sont conservés**. |
| 4 | `stage.drop` (sondes) | Supprime les vérifications de santé `NPM Manager-upstream-check` réussies | Elles fausseraient les visiteurs et le trafic |
| 5 | `stage.timestamp { source = "timestamp", format = "RFC3339" }` | L'heure de la ligne dans Loki devient celle de Nginx | Après une coupure, les lignes renvoyées gardent leur vraie heure |
| 6 | `stage.template` → `visitor_id` | `Hash(IP + "|" + User-Agent, sel)`, tronqué à 16 caractères | **Visiteur unique anonyme** : on peut compter les visiteurs sans stocker leur IP. Calculé **avant** la troncature de l'IP. |
| 7 | `stage.template` → `remote_addr` | `203.0.113.45` devient `203.0.113.0` ; une IPv6 est réduite à ses 3 premiers blocs ; sinon `masked` | Pas d'IP complète stockée (décision RGPD) |
| 8 | `stage.template` → `uri` | Tout segment de 32 caractères ou plus (jeton, UUID) devient `/:token` | Évite de stocker des jetons et de multiplier les URLs uniques |
| 9 | `stage.template` → `ua_family` | Classe le User-Agent : Chrome, Firefox, Edge, Safari, Opera, Robot, Outil/Script… | Calculé une fois ici, pas à chaque affichage du dashboard |
| 10 | `stage.template` → `output_line` + `stage.output` | Reconstruit une ligne JSON compacte avec seulement les champs utiles, sans le timestamp, que Loki stocke déjà | Environ 35 octets économisés par ligne |
| 11 | `stage.limit { rate = 500, burst = 2000, drop = false }` | Au plus 500 lignes/s, avec des pointes à 2 000 | Protège Loki et le réseau lors d'un scan. `drop = false` : **ralentit sans rien jeter** (*backpressure*). |

Exemple de ligne réellement stockée dans Loki :

```json
{"body_bytes_sent":"7449","http_user_agent":"curl/8.14.1","remote_addr":"127.0.0.0",
 "request_id":"b46c0911a32caae68b7f305f0ef2d410","request_method":"GET","request_time":"0.004",
 "server_name":"192.168.206.112","status":"200","ua_family":"Outil/Script",
 "upstream_response_time":"0.004","uri":"/","visitor_id":"d795a106b41d96b1"}
```

### 11.3 `loki.process "nginx_error"` — les logs d'erreur

`error.log` est du texte libre. Le traitement :
- `stage.regex` extrait la date en début de ligne, puis `stage.timestamp` l'utilise comme heure de la ligne ;
- trois `stage.replace` **effacent** les query strings dans `request: "GET /x?..."`, `referrer:` et `upstream:`, et remplacent le dernier octet de l'IP client par `0` ;
- `stage.limit` est fixé à 100 lignes/s.

### 11.4 `loki.write "central"` — envoyer

| Paramètre | Valeur | Explication |
|---|---|---|
| `url` | `sys.env("LOKI_URL")` | Lu dans `/etc/default/alloy` |
| `batch_wait` | `5s` | Un lot part au plus tard 5 s après la première ligne… |
| `batch_size` | `1MiB` | … ou dès qu'il atteint 1 Mio. **Plus le lot est gros, moins il y a de requêtes et meilleure est la compression.** |
| (compression) | snappy | Automatique et non désactivable : environ ×4,5 sur nos lignes |
| `remote_timeout` | `30s` | Délai maximal d'un envoi |
| `min_backoff_period` / `max_backoff_period` | `1s` / `5m` | En cas d'échec, nouvel essai après 1 s, puis 2 s, 4 s… jusqu'à 5 min entre deux essais |
| `max_backoff_retries` | `0` | **Essais infinis** : on n'abandonne jamais un lot à cause d'une coupure |
| `retry_on_http_429` | `true` | Si Loki répond « trop de requêtes », on réessaie au lieu de jeter |
| `tls_config` | CA, certificat, clé, TLS ≥ 1.2 | TLS mutuel vers Loki |

**Que se passe-t-il pendant une coupure réseau ?** Le lot en cours est réessayé indéfiniment. La file d'envoi se remplit, puis Alloy **arrête de lire** le fichier, sans rien jeter. Les lignes restent dans `access_json.log`. Au rétablissement, Alloy envoie le lot en attente puis reprend la lecture à sa position. Le test réel de 5 minutes de coupure a donné 200/200 lignes reçues, sans doublon.

**Réglage de la bande passante :**

| Objectif | Réglage |
|---|---|
| Encore moins de requêtes réseau | `batch_wait = "30s"` : lots plus gros et meilleure compression, mais 30 s de retard d'affichage |
| Plafonner le volume en cas de rafale | Baisser `stage.limit rate` |
| Plafond strict en kbit/s | Alloy ne le propose pas. Il faut une règle `tc` (Linux) sur le port 3100, voir `docs/03-parametres.md`. |

Après toute modification : `alloy fmt /etc/alloy/config.alloy && systemctl reload alloy`.

---

## 12. Vérification de bout en bout

### 12.1 Sur le serveur web

```bash
systemctl status alloy --no-pager
journalctl -u alloy --since "-10 min" --no-pager | grep -iE "level=(error|warn)"
curl -s 127.0.0.1:12345/metrics | grep -E "^loki_write_(sent_entries|sent_bytes|dropped_entries)_total|^loki_process_dropped_lines_total"
```

| Métrique Alloy | Sens | Valeur saine |
|---|---|---|
| `loki_source_file_read_lines_total` | Lignes lues dans les fichiers | Augmente avec le trafic |
| `loki_process_dropped_lines_total{reason=...}` | Lignes volontairement supprimées (`static_asset_ok`, `healthcheck_ok`) | Augmente : c'est le filtrage |
| `loki_write_sent_entries_total` | Lignes envoyées à Loki | Augmente |
| `loki_write_sent_bytes_total` | Octets envoyés, avant compression | — |
| `loki_write_dropped_entries_total` | Lignes **perdues** côté envoi | **0** (hors rejet unique des vieilles lignes au 1er démarrage) |
| `loki_write_batch_retries_total` | Nouveaux essais d'envoi | 0 en temps normal ; augmente pendant une coupure |

Pour voir l'interface d'Alloy (graphe des composants) depuis votre PC :

```bash
ssh -L 12345:127.0.0.1:12345 root@192.168.206.112
```

Puis ouvrez http://localhost:12345.

### 12.2 Dans Grafana

**Explore → Loki-Nginx**, puis la requête `{job="nginx-access"}` : les dernières lignes doivent apparaître en moins de 10 s.

### 12.3 Tests automatiques

Voir `docs/07-tests.md` : site de test, `run_validation.py` (25 contrôles), `outage_test.sh` (coupure réseau), `bandwidth_measure.sh` (bande passante).

---

## 13. Le langage LogQL expliqué

LogQL est le langage de requête de Loki. Une requête se lit **de gauche à droite**, comme un pipeline Unix.

### 13.1 Les briques de base

```
{job="nginx-access", site=~"$site"}  |~ `"status":"5[0-9]{2}"`  | json uri="uri"  | unwrap request_time
└──────── 1. sélecteur ───────────┘  └── 2. filtre de ligne ──┘  └─ 3. parseur ─┘  └── 4. valeur numérique ──┘
```

1. **Sélecteur de flux** `{label="valeur"}` : choisit les flux par leurs labels. C'est la seule partie indexée, donc très rapide. `=~` signifie « correspond à l'expression régulière ». `$site` est une variable du dashboard ; sa valeur « All » vaut `.+`, c'est-à-dire tout.
2. **Filtre de ligne** `|=` (contient), `!=` (ne contient pas), `|~` (regex), `!~` (pas la regex) : élimine des lignes par simple recherche de texte, **avant** toute analyse. C'est très peu coûteux, d'où son usage systématique pour filtrer les codes HTTP.
3. **Parseur** `| json uri="uri"` : extrait seulement le champ demandé du JSON, ce qui est plus rapide que `| json` qui extrait tout.
4. **`| unwrap champ`** : transforme un champ en nombre, pour calculer des moyennes ou des percentiles. `| __error__=""` écarte les lignes où la conversion a échoué (champ vide par exemple).

### 13.2 Les fonctions sur une fenêtre de temps `[durée]`

| Fonction | Résultat |
|---|---|
| `count_over_time(... [1h])` | Nombre de lignes dans la fenêtre |
| `rate(... [5m])` | Lignes par seconde |
| `avg_over_time(... unwrap x [..])` | Moyenne de x |
| `quantile_over_time(0.95, ... unwrap x [..])` | **P95** : 95 % des valeurs sont en dessous |
| `max_over_time(... unwrap x [..])` | Valeur maximale |

### 13.3 Les agrégations

| Opérateur | Rôle |
|---|---|
| `sum(...)` | Additionne tout |
| `sum by (uri) (...)` | Additionne **par URL** : une ligne de résultat par URL |
| `topk(20, ...)` | Garde les 20 plus grandes valeurs |
| `count(...)` | Compte le nombre de séries (sert aux visiteurs uniques) |
| `A and on (uri) B` | Garde les URLs de A **qui existent aussi** dans B (sert au filtre de volume minimum) |
| `A / ignoring(uri) group_left B` | Divise chaque URL de A par le total B (sert aux pourcentages) |

### 13.4 Les variables de temps de Grafana

| Variable | Sens |
|---|---|
| `$__range` | Durée de la période choisie en haut à droite (ex. `24h`) |
| `$__range_s` | La même durée, en secondes |
| `$__auto` | Pas automatique adapté à la largeur du graphique |

**Requête « instant » ou « range » :** une requête *instant* donne **un seul résultat** pour toute la période (chiffres, tableaux, camemberts). Une requête *range* donne **une courbe** (graphiques temporels).

---

## 14. Le dashboard expliqué panneau par panneau

Dans les requêtes ci-dessous, `SEL` désigne le sélecteur commun :

```
{job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"}
```

### 14.0 Les variables (en haut du dashboard)

| Variable | Rôle |
|---|---|
| Source Loki | Datasource utilisée (`Loki-Nginx`) |
| Site / Environnement / Application / Serveur web | Filtres sur les labels, avec choix multiple. « All » inclut tout. |
| Volume minimum (URLs lentes) | Nombre minimal de requêtes pour qu'une URL figure dans le classement des lenteurs (1, 3, 5, 10, 20, 50, 100 ; 3 par défaut) |

### 14.1 Section A — Vue générale

| Panneau | Requête | Explication |
|---|---|---|
| Requêtes (période) | `sum(count_over_time(SEL [$__range]))` | Nombre total de requêtes sur la période |
| Requêtes / seconde | `... / $__range_s` | Moyenne par seconde |
| Visiteurs uniques estimés | `count(sum by (visitor_id) (count_over_time(SEL \| json visitor_id="visitor_id" [$__range])))` | `sum by (visitor_id)` crée une série par visiteur, puis `count` compte ces séries |
| Serveurs web actifs | `count(count by (hostname) (...))` | Nombre de serveurs ayant envoyé au moins une ligne |
| Requêtes (24 dernières heures) | `[24h]` fixe | Indépendant de la période choisie |
| Temps de réponse P95 | `quantile_over_time(0.95, SEL \| json request_time=... \| unwrap request_time ...)` | 95 % des requêtes ont été servies plus vite que cette valeur |
| Évolution du trafic par heure | `sum(count_over_time(SEL [1h]))` en *range*, pas de 1 h | Histogramme horaire |
| Requêtes par seconde par serveur | `sum by (hostname) (rate(SEL [$__auto]))` | Une courbe par serveur |

### 14.2 Section B — Codes HTTP

| Panneau | Requête | Explication |
|---|---|---|
| 2xx / 3xx / 4xx / 5xx | `sum(count_over_time(SEL \|~ \`"status":"2[0-9]{2}"\` [$__range]))` | Filtre de **texte** sur `"status":"2xx"` : aucun parsing JSON, donc très rapide |
| Répartition par code HTTP | `sum by (status) (count_over_time(SEL \| json status="status" [$__range]))` | Une part du camembert par code exact |
| Évolution par classe de statut | 4 requêtes *range* (2xx, 3xx, 4xx, 5xx) empilées | Évolution dans le temps |
| Évolution des erreurs par code | `sum by (status)` sur les lignes `[45]xx` | Détail des erreurs (404, 500, 502…) |

### 14.3 Section C — Top 20 des URLs les plus consultées

```
A : topk(20, sum by (uri) (count_over_time(SEL !~ `"status":"4[0-9]{2}"` | json uri="uri" [$__range])))
B : 100 * A / ignoring(uri) group_left sum(count_over_time(SEL [$__range]))
```

- `!~ "status":"4xx"` **exclut les erreurs client 4xx** du classement. Un scanner génère des milliers d'URLs inventées en 404, qui pollueraient le top. Les 404 sont analysées en section F.
- A donne le nombre de requêtes par URL ; B donne le pourcentage du trafic total.
- **Transformations Grafana** appliquées au tableau, dans l'ordre :
  1. `merge` fusionne les résultats de A et B sur la colonne URL ;
  2. `sortBy` trie par nombre de requêtes, décroissant ;
  3. `calculateField` (index) puis `calculateField` (+1) produit la colonne **Position** (1, 2, 3…) ;
  4. `organize` renomme et ordonne les colonnes : Position, URL, Requêtes, %.

### 14.4 Section D — Top 20 des URLs les plus lentes

Cinq requêtes, fusionnées dans un seul tableau :

```
A (Nombre)      : sum by (uri) (count_over_time(SEL !~ 4xx | json uri="uri" [$__range])) >= $min_requests
B (Moyenne)     : avg_over_time(SEL !~ 4xx | json uri, request_time | unwrap request_time [$__range]) by (uri)  and on (uri) (A)
C (P95)         : quantile_over_time(0.95, ...même chose...) by (uri)                                         and on (uri) (A)
D (Max)         : max_over_time(...) by (uri)                                                                and on (uri) (A)
E (Upstream P95): quantile_over_time(0.95, ... | upstream_response_time =~ "^[0-9.]+$" | unwrap upstream_response_time ...) and on (uri) (A)
```

- **Le classement se fait sur le P95 (C)**, trié du plus lent au plus rapide, puis limité à 20 lignes. Le P95 représente mieux l'expérience des utilisateurs que la moyenne, qui est faussée par quelques requêtes très rapides ou très lentes.
- **`and on (uri) (A)`** : une URL n'est classée que si elle a au moins *Volume minimum* requêtes. Une URL appelée une seule fois en 3 s ne doit pas être en tête du classement.
- **Upstream P95** : temps de l'application seule. S'il est proche du P95 total, la lenteur vient de l'application. S'il est faible ou vide, elle vient de Nginx, du réseau du client, ou d'un contenu servi directement par Nginx.
- `upstream_response_time =~ "^[0-9.]+$"` ignore les requêtes sans backend, où le champ est vide.
- Couleurs : vert sous 0,5 s, jaune de 0,5 à 1 s, orange de 1 à 2 s, rouge à partir de 2 s.
- Si le tableau est vide, aucune URL n'atteint le volume minimum : baissez la variable ou élargissez la période.

Le graphique « Temps de réponse : total vs serveur applicatif » montre P95 total, P95 upstream et moyenne dans le temps.

### 14.5 Section E — Visiteurs

| Panneau | Requête / logique |
|---|---|
| Visiteurs uniques estimés | Nombre de `visitor_id` distincts (voir §14.1) |
| Visiteurs par heure | Même calcul sur des fenêtres `[1h]` |
| Top 20 des réseaux clients | `topk(20, sum by (remote_addr) (...))`. Les IP étant tronquées, on voit des **réseaux /24** (ex. `10.88.1.0`), pas des personnes. |
| Navigateurs | `sum by (ua_family)` : famille calculée par Alloy |
| Méthodes HTTP | `sum by (request_method)` |

> Une empreinte visiteur correspond à un couple (IP, navigateur). Plusieurs personnes derrière une même IP d'entreprise peuvent compter pour un seul visiteur, et un mobile qui change d'IP compte plusieurs fois. C'est une **estimation**.

### 14.6 Section F — Erreurs

| Panneau | Requête / logique |
|---|---|
| URLs en erreur 404 / 500 | `topk(20, sum by (uri) (count_over_time(SEL \|~ "status":"404" \| json uri [...])))` |
| Erreurs 502 / 503 | Même logique, par URL **et** par code. Un 502 signifie que l'application ne répond pas ; un 503, qu'elle est indisponible. |
| Erreurs par serveur web | `sum by (hostname, status)` sur les 4xx/5xx |
| 404 par réseau client | `topk(20, sum by (remote_addr) ...)` sur les 404 : **repère les scanners** |
| Évolution des 5xx par serveur | Courbe par serveur |
| Dernières requêtes en erreur 5xx | Les lignes elles-mêmes, mises en forme par `line_format` (code, méthode, URL, temps, `request_id`). Le `request_id` permet de retrouver la requête dans les logs de l'application. |
| Journal d'erreurs Nginx | `{job="nginx-error", ...}` : contenu brut de `error.log`, nettoyé par Alloy |

> Pendant un scan massif (plus de 50 000 URLs 404 différentes), le tableau des 404 peut afficher *maximum number of series reached*. Réduisez alors la période ; le tableau « 404 par réseau client » reste utilisable.

---

## 15. Ajouter un nouveau site (jusqu'à 35 sites)

Pour chaque nouveau serveur web, faites dans l'ordre :

**Sur la VM centrale :**

```bash
cd ~/loki-deploy
sudo bash pki/gen-certs.sh /root/loki-pki 192.168.23.24 central <HOSTNAME_DU_SERVEUR>   # réutilise l'autorité
sudoedit /etc/sysconfig/loki-firewall      # ajouter l'IP : LOKI_ALLOWED_SOURCES="192.168.206.112 <IP>"
sudo systemctl restart loki-firewall
sudo iptables -L LOKI_INGRESS -n           # vérifier la nouvelle ligne ACCEPT
```

**Sur le nouveau serveur web :** refaites les §8, 9, 10 et 11, en adaptant :
- les lignes `set_real_ip_from` à son reverse proxy ;
- dans `config.alloy`, les labels `site`, `environment` et `application` des deux blocs `loki.source.file` ;
- **le même `VISITOR_SALT`** que les autres serveurs. Ne régénérez pas le sel : copiez la ligne depuis `/etc/default/alloy` d'un serveur existant.

**Contrôle :** dans le dashboard, la variable *Site* propose la nouvelle valeur, et *Serveurs web actifs* augmente de 1.

**Conseils pour 35 sites :**
- Automatisez ces étapes (script ou Ansible) pour éviter les oublis.
- Choisissez des noms de `site` courts et stables. Changer un label crée un nouveau flux, et l'historique reste sous l'ancien nom.
- Les sites distants doivent joindre le port 3100 par un VPN ou un lien privé. **Loki ne doit jamais être exposé sur Internet.**
- Ordre de grandeur : 35 sites à 100 000 requêtes/jour chacun, c'est environ 30 kbit/s en moyenne vers Loki et environ 2 Go de disque sur 14 jours.

---

## 16. Exploitation au quotidien

| Besoin | Commande |
|---|---|
| État de Loki | `sudo systemctl status loki` ; `sudo journalctl -u loki -f` |
| État d'Alloy | `systemctl status alloy` ; `journalctl -u alloy -f` |
| Espace disque de Loki | `sudo du -sh /var/lib/loki` |
| Recharger Alloy après modification | `alloy fmt /etc/alloy/config.alloy && systemctl reload alloy` |
| Vérifier la config de Loki | `sudo -u loki loki -config.file=/etc/loki/config.yml -verify-config` |
| Modifier la rétention | `retention_period` dans `/etc/loki/config.yml`, puis `sudo systemctl restart loki` |
| Supprimer des données | API `delete`, voir `docs/05-verification-depannage.md` §4 |
| Mettre à jour Alloy | `apt-mark unhold alloy && apt-get install alloy=<version> && apt-mark hold alloy`, après un test |
| Sauvegarder | `docs/06-sauvegarde-restauration.md` : **`/root/loki-pki/ca.key` et le `VISITOR_SALT` sont irremplaçables** |

**Dépannage rapide :**

| Symptôme | Où regarder |
|---|---|
| Dashboard vide | Période et variables ; puis Explore `{job="nginx-access"}` ; puis `journalctl -u alloy` |
| `x509` ou `certificate` dans les journaux | Certificat expiré, mauvais nom ou mauvaise autorité : `openssl x509 -in ... -noout -subject -enddate` |
| `connection refused` / `timeout` dans Alloy | Pare-feu (`/etc/sysconfig/loki-firewall`) ou Loki arrêté |
| `status=400 ... too old` | Lignes de plus de 7 jours, normal après une longue coupure |
| `status=429` | Débit trop élevé ; Alloy réessaie tout seul |
| Tableau « URLs lentes » vide | Volume minimum trop élevé pour la période |
| `maximum number of series reached` | Période trop longue pendant un scan : réduire la période |

Le guide détaillé de dépannage se trouve dans `docs/05-verification-depannage.md`.

---

## 17. Glossaire

| Terme | Définition |
|---|---|
| **Label** | Étiquette indexée d'un flux Loki (`site`, `hostname`…). Il faut en garder peu, avec des valeurs stables. |
| **Flux (stream)** | Ensemble des lignes qui ont exactement les mêmes labels. |
| **Chunk** | Bloc compressé de lignes d'un flux, stocké sur disque. |
| **Cardinalité** | Nombre de valeurs différentes. Un label à forte cardinalité (IP, URL) ralentit Loki. |
| **Batch (lot)** | Groupe de lignes envoyées en une seule requête HTTP. |
| **snappy** | Algorithme de compression rapide utilisé par Alloy pour l'envoi. |
| **Backpressure** | Quand l'aval est saturé ou coupé, l'amont ralentit au lieu de jeter des données. |
| **Fichier de positions** | Fichier où Alloy note jusqu'où il a lu chaque log, pour reprendre sans perte ni doublon. |
| **TLS mutuel (mTLS)** | Le client et le serveur prouvent tous les deux leur identité par certificat. |
| **PKI / CA** | Infrastructure de certificats / autorité qui signe les certificats. |
| **P95** | 95ᵉ percentile : 95 % des valeurs sont inférieures. |
| **Upstream** | Application située derrière Nginx (Next.js, Uvicorn…). |
| **Provisioning** | Configuration de Grafana par fichiers plutôt que par l'interface. |
| **Override systemd** | Fichier `*.service.d/override.conf` qui modifie un service sans toucher au fichier du paquet. |
| **Rétention** | Durée de conservation des données (14 jours dans Loki, 7 jours en local sur les serveurs web). |
