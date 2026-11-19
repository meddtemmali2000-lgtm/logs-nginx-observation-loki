# Référence des paramètres et choix de configuration

Toutes les clés ci-dessous ont été vérifiées dans la documentation officielle des versions installées :
- Alloy **v1.21.3** : `docs/sources/reference/components/loki/{loki.source.file,loki.process,loki.write}.md` ;
- Loki **v3.7.10** : `docs/sources/shared/configuration.md`.

Elles ont aussi été exécutées sur un banc de test local (mêmes binaires ; voir `07-tests.md`) : `loki -verify-config` OK, `alloy fmt` OK, pipeline complet testé.

## 1. Paramètres configurables demandés (exigence §4)

| Besoin | Fichier | Paramètre | Valeur | Effet / compromis |
|---|---|---|---|---|
| Délai d'envoi des batches | `config.alloy` → `loki.write.endpoint` | `batch_wait` | `5s` | Un batch part au plus tard après ce délai. Plus long = moins de requêtes HTTPS et meilleure compression, mais plus de latence dans Grafana. Pour une bande passante très contrainte : `30s`. |
| Taille maximale des batches | idem | `batch_size` | `1MiB` | Taille non compressée maximale d'un batch. Un batch part dès qu'il atteint cette taille. |
| Limite de débit | `config.alloy` → `loki.process` | `stage.limit { rate, burst, drop = false }` | `500` lignes/s, rafale `2000` | Au-delà, la **lecture du fichier ralentit** (backpressure) : aucune ligne n'est jetée, elle est envoyée plus tard. Alloy n'offre **pas** de limite en octets/s. Pour une limite stricte en kbit/s, voir §4 (`tc`). |
| Taille maximale du buffer local | `/etc/logrotate.d/nginx` | `rotate 7`, `maxsize 500M` | 7 jours | Le buffer local, ce sont les fichiers Nginx eux-mêmes : en cas de coupure, Alloy reprend depuis sa position. |
| Durée de conservation des logs locaux | idem | `daily` + `rotate 7` | 7 jours | Aligné sur `reject_old_samples_max_age: 168h` de Loki : au-delà de 7 jours, Loki refuserait les lignes de toute façon. |
| Comportement en cas de perte de connexion | `config.alloy` → `loki.write.endpoint` | `max_backoff_retries = 0`, `min_backoff_period = "1s"`, `max_backoff_period = "5m"`, `remote_timeout = "30s"` | retries infinis | La documentation indique : « Set to `0` to retry indefinitely ». Backoff exponentiel de 1 s à 5 min entre deux essais. Pendant la coupure, la file en mémoire se remplit puis bloque la lecture ; les lignes non lues restent dans les fichiers. |
| Rétention centrale | `loki/config.yml` → `limits_config` | `retention_period` | `336h` (14 jours) | Appliquée par le compacteur (`compactor.retention_enabled: true`). |

## 2. Alloy — pipeline et justification

### `loki.source.file`
| Argument | Valeur | Raison |
|---|---|---|
| `targets` | `access_json.log` et `error.log` avec labels `job`, `site`, `environment`, `application`, `hostname` | Seuls labels autorisés (cardinalité fixe). `hostname = constants.hostname`. |
| `file_match { enabled = true, sync_period = "10s" }` | — | Redécouverte automatique du fichier après rotation, ou s'il n'existe pas encore au démarrage. |
| `on_positions_file_error` | `"restart_from_end"` | Si le fichier de positions est corrompu, on reprend en fin de fichier : on évite de **retransmettre** tout le fichier, ce qui créerait des doublons et fausserait les statistiques. |
| `tail_from_end` | défaut `false` | Le fichier JSON est neuf : rien d'ancien à ignorer, et pas de risque de perdre les premières lignes. |

### `loki.process "nginx_access"` (dans l'ordre)
1. `stage.label_drop { values = ["filename"] }` : supprime le label `filename` ajouté automatiquement par `loki.source.file` (non autorisé).
2. `stage.json` + `drop_malformed = true` : extraction des champs. Nginx `escape=json` garantit un JSON valide ; une ligne illisible est comptée dans `loki_process_dropped_lines_total` plutôt que transmise vide.
3. `stage.drop` (statiques) : `source = "uri,status"`, `expression = "(?i)\.(css|js|…)\;[23][0-9]{2}$"`. La suppression n'a lieu que si l'extension est statique **et** le code est 2xx/3xx : **toutes les 4xx/5xx sont conservées**. Testé : `app.js` 200 et `logo.png` 304 supprimés, `old.css` 404 conservé.
4. `stage.drop` (sondes) : le User-Agent `NPM Manager-upstream-check` est supprimé s'il reçoit un 2xx/3xx (sonde de santé de Nginx Proxy Manager toutes les 12 min, qui fausserait les visiteurs). Ses échecs restent visibles.
5. `stage.timestamp { source = "timestamp", format = "RFC3339" }` : l'horodatage Loki est celui de Nginx, et non l'heure de lecture. Indispensable après une coupure.
6. `stage.template` → `visitor_id` = `trunc 16 (Hash (IP|User-Agent) sel)` : empreinte SHA3-256 salée, calculée **avant** troncature de l'IP. Le sel (`VISITOR_SALT`) ne quitte jamais le serveur web.
7. `stage.template` → `remote_addr` tronquée : `a.b.c.0` (IPv4 /24), `a:b:c::` (IPv6 /48), sinon `masked`.
8. `stage.template` → `uri` : les segments d'au moins 32 caractères `[A-Za-z0-9_~.-]` (jetons, UUID) sont remplacés par `/:token`.
9. `stage.template` → `ua_family` : Chrome / Edge / Firefox / Safari / Opera / Internet Explorer / Robot / Outil/Script / Autre / Inconnu. Calculé une fois ici pour éviter des expressions régulières coûteuses à chaque requête Grafana.
10. `stage.template` → ligne finale (`dict … | toJson`) + `stage.output` : on n'envoie que les champs utiles, sans le `timestamp` (déjà stocké par Loki, soit environ 35 octets de moins par ligne).
11. `stage.limit` : voir §1.

Pas de `stage.sampling` : aucun échantillonnage, les comptages restent exacts.

### `loki.process "nginx_error"`
- Horodatage extrait de `2006/01/02 15:04:05` (heure locale du serveur, `location = "Etc/UTC"`, conforme à `timedatectl`).
- `stage.replace` : suppression des query strings dans `request:`, `referrer:`, `upstream:` et troncature de l'IP `client:`. Testé : `?token=SECRET123` et `?session=XYZ` disparaissent.

### `loki.write`
| Argument | Valeur | Raison |
|---|---|---|
| `url` | `sys.env("LOKI_URL")` | Défini dans `/etc/default/alloy`. |
| `tls_config { ca_file, cert_file, key_file, min_version = "TLS12" }` | — | TLS mutuel : Loki exige un certificat client signé par la CA interne. |
| Compression | snappy (native, non désactivable) | Documentation `loki.write` : « uses snappy for compression ». Mesuré : ×4,5. |
| `retry_on_http_429` | `true` | Si Loki limite le débit, on réessaie au lieu de perdre les lignes. |

Fonctionnalités **non activées** car expérimentales en v1.21.3 (elles exigent `--stability.level=experimental`) :
- bloc `wal` (buffer disque entre `loki.process` et l'envoi) ;
- bloc `queue_config` (taille de la file d'envoi).

Le comportement par défaut a été testé sans perte, y compris coupure + rotation et rotation pendant une lecture bloquée (voir `07-tests.md`).

## 3. Loki — paramètres clés

| Section | Clé | Valeur | Raison |
|---|---|---|---|
| `auth_enabled` | — | `false` | Mono-tenant. L'authentification est assurée par le certificat client (mTLS). |
| `server` | `http_listen_address` / `http_listen_port` | `0.0.0.0` / `3100` | Accessible depuis les serveurs web, filtré par iptables (chaîne `LOKI_INGRESS`). |
| `server` | `grpc_listen_address` | `127.0.0.1` | gRPC interne au monolithe, jamais exposé. |
| `server.http_tls_config` | `client_auth_type: RequireAndVerifyClientCert` + `client_ca_file` | — | Connexion refusée sans certificat valide (testé : `alert certificate required`). |
| `server` | `tls_min_version` | `VersionTLS12` | |
| `common` | `path_prefix`, stockage `filesystem` | `/var/lib/loki` | Stockage local, sans MinIO. |
| `schema_config` | `tsdb` + `v13`, index `24h` | à partir du `2026-11-01` | Schéma recommandé pour Loki 3.x. |
| `ingester` | `chunk_target_size: 1572864`, `chunk_idle_period: 1h`, `max_chunk_age: 2h` | — | Chunks plus pleins pour un faible volume (moins de fichiers). |
| `compactor` | `retention_enabled: true`, `delete_request_store: filesystem`, `retention_delete_delay: 2h` | — | Rétention effective + API de suppression (utile pour purger les lignes de test). |
| `limits_config` | `retention_period: 336h` | 14 jours | Choix validé. |
| `limits_config` | `reject_old_samples_max_age: 168h` | 7 jours | Fenêtre maximale de rattrapage après une coupure. |
| `limits_config` | `ingestion_rate_mb: 8`, `ingestion_burst_size_mb: 16`, `per_stream_rate_limit: 8MB` | — | Marge pour le rattrapage après coupure (évite des 429 inutiles). |
| `limits_config` | `discover_service_name: []`, `discover_log_levels: false` | — | Aucun label ou métadonnée ajouté implicitement : seuls les 5 labels autorisés. Vérifié : `/labels` renvoie exactement `application, environment, hostname, job, site`. |
| `limits_config` | `max_query_series: 50000` | — | Voir §5. |
| `limits_config` | `max_query_parallelism: 8`, `tsdb_max_query_parallelism: 32`, `split_queries_by_interval: 1h`, `query_timeout: 2m` | — | Bornes CPU/RAM sur une VM partagée. |
| `query_range.results_cache.embedded_cache` | `max_size_mb: 200` | — | Cache des résultats : les rafraîchissements du dashboard ne recalculent que la fin de la période. |
| `pattern_ingester.enabled` | `false` | — | Inutile ici, économise CPU et RAM. |
| `analytics.reporting_enabled` | `false` | — | VM hors ligne, pas de télémétrie sortante. |

Le service systemd (`override.conf`) plafonne Loki à 2 Go de RAM (`MemoryLimit`, cgroup v1 sur AlmaLinux 8) et 2 vCPU, avec `GOMEMLIMIT=1800MiB`.

## 4. Option : limite stricte de bande passante (kbit/s)

Alloy n'a pas de paramètre de débit en octets. Si un plafond réseau strict est exigé, on utilise `tc` (iproute2, déjà présent) sur le serveur web. Exemple : 256 kbit/s vers Loki uniquement, le reste du trafic n'étant pas limité :

```bash
DEV=eth0
tc qdisc add dev $DEV root handle 1: htb default 10
tc class add dev $DEV parent 1: classid 1:10 htb rate 10gbit
tc class add dev $DEV parent 1: classid 1:20 htb rate 256kbit ceil 256kbit
tc filter add dev $DEV parent 1: protocol ip prio 1 u32 match ip dst 192.168.23.24/32 match ip dport 3100 0xffff flowid 1:20
# retrait : tc qdisc del dev $DEV root
```

Avec `drop = false` sur `stage.limit` et des retries infinis, un débit plafonné ne provoque **aucune perte** : Alloy accumule simplement du retard de lecture.

## 5. Limite connue : scans de vulnérabilités et cardinalité des URLs

Le 07/08/2026, un scanner (gobuster/ffuf, `172.20.0.86`) a généré 330 000 requêtes, presque toutes sur des URLs 404 uniques. Les requêtes « par URL » (`sum by (uri)`) produisent alors une série par URL distincte. Testé avec 200 000 URLs uniques :
- avec `max_query_series` à 5 000 (défaut) puis à 100 000 : erreur « maximum number of series reached » ;
- la mémoire de Loki monte à environ 720 Mo.

Mesures retenues :
- les tableaux **Top 20 consultées** et **Top 20 lentes** excluent les 4xx (filtre de ligne `!~ "status":"4xx"`), qui sont analysées en section F. Testé : réponse en 0,08 s malgré le scan ;
- `max_query_series: 50000` (compromis mémoire) ;
- le tableau **URLs en erreur 404** peut afficher l'erreur sur une période qui contient un scan massif : réduire la période. Le tableau **404 par réseau client** identifie la source du scan dans tous les cas ;
- option : `approx_topk` (Loki 3.7) résout ce cas, mais la fonctionnalité est documentée comme **expérimentale** (`limits_config.shard_aggregations: approx_topk`). Elle n'est donc pas activée.
