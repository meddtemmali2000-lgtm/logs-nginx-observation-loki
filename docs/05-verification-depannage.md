# Procédure de vérification et de dépannage

Variables utilisées ci-dessous :

```bash
# VM centrale
P=/root/loki-pki; C="--cacert $P/ca.crt --cert $P/client-grafana.crt --key $P/client-grafana.key"
# Serveur web
P=/etc/alloy/pki; C="--cacert $P/ca.crt --cert $P/client.crt --key $P/client.key"
LOKI=https://192.168.23.24:3100
```

## 1. Vérification rapide (5 minutes)

| # | Où | Commande | Résultat attendu |
|---|---|---|---|
| 1 | Web | `tail -n1 /var/log/nginx/access_json.log \| python3 -m json.tool` | JSON valide, `uri` sans `?` |
| 2 | Web | `systemctl is-active alloy` | `active` |
| 3 | Web | `curl -s 127.0.0.1:12345/-/ready` | `Alloy is ready.` |
| 4 | Web | `curl -s 127.0.0.1:12345/metrics \| grep -E '^loki_write_(sent_entries_total\|dropped_entries_total\|batch_retries_total)'` | `sent` augmente ; `dropped` = 0 |
| 5 | Web | `curl -s $C $LOKI/ready` | `ready` |
| 6 | Centrale | `sudo systemctl is-active loki loki-firewall` | `active` ×2 |
| 7 | Centrale | `sudo curl -s $C https://127.0.0.1:3100/loki/api/v1/labels` | `application, environment, hostname, job, site` uniquement |
| 8 | Centrale | `sudo curl -s $C -G https://127.0.0.1:3100/loki/api/v1/query --data-urlencode 'query=sum(count_over_time({job="nginx-access"}[5m]))'` | valeur > 0 si trafic |
| 9 | Grafana | *Data sources → Loki-Nginx → Test* | « Data source successfully connected » |

Interface de débogage d'Alloy (graphe des composants, santé, positions de lecture) : `ssh -L 12345:127.0.0.1:12345 root@192.168.206.112` puis ouvrir `http://localhost:12345`.

## 2. Métriques Alloy utiles (`curl -s 127.0.0.1:12345/metrics`)

| Métrique | Signification |
|---|---|
| `loki_source_file_read_lines_total` | Lignes lues par fichier |
| `loki_source_file_file_bytes_total` / `loki_source_file_read_bytes_total` | Taille du fichier / position lue : un écart persistant indique un retard de lecture (backpressure, coupure) |
| `loki_process_dropped_lines_total{reason=…}` | Lignes filtrées volontairement (`static_asset_ok`, `healthcheck_ok`) ou JSON invalide |
| `loki_write_sent_entries_total` / `loki_write_sent_bytes_total` | Lignes / octets (non compressés) envoyés |
| `loki_write_request_size_bytes_sum` | Octets réellement envoyés (compressés snappy) |
| `loki_write_batch_retries_total` | Renvois (coupure réseau, Loki indisponible, 429) |
| `loki_write_dropped_entries_total{reason=…}` | **Doit rester à 0** : lignes perdues |

## 3. Dépannage par symptôme

### Aucune donnée dans Grafana
1. Le fichier JSON est-il alimenté ? `ls -l /var/log/nginx/access_json.log`. S'il est vide : `nginx -T | grep -n json_analytics` (la directive `access_log … json_analytics` doit être présente au niveau `http`). Attention : un `access_log off;` dans un bloc `server` désactive aussi ce log.
2. Alloy lit-il le fichier ? `curl -s 127.0.0.1:12345/metrics | grep read_lines_total`. Si l'accès est refusé : `id alloy` (groupe `adm`) et `ls -l /var/log/nginx/` (mode `0640 www-data:adm`).
3. Alloy envoie-t-il ? `journalctl -u alloy -n 50 --no-pager | grep -i error` :
   - `x509: certificate signed by unknown authority` : mauvais `ca.crt` ;
   - `remote error: tls: certificate required` / `bad certificate` : certificat client absent ou non signé par la CA ;
   - `connection refused` / `i/o timeout` : Loki arrêté, ou pare-feu (`sudo iptables -L LOKI_INGRESS -n -v` sur la VM centrale : l'IP du serveur web est-elle autorisée ?) ;
   - `server returned HTTP status 400 … entry too far behind` : horloge ou coupure de plus de 7 jours.
4. Période sélectionnée dans Grafana trop courte ou variables (`Site`, `Serveur web`) mal positionnées.

### Loki ne démarre pas
```bash
sudo journalctl -u loki -n 100 --no-pager
sudo -u loki /usr/bin/loki -config.file=/etc/loki/config.yml -verify-config
sudo ls -ld /var/lib/loki /etc/loki/pki/*      # propriétaire loki / lisible par loki
```
- `permission denied` sur `/var/lib/loki` : `sudo chown -R loki:loki /var/lib/loki`.
- `open /etc/loki/pki/loki.key: permission denied` : mode `0640 root:loki`.
- Arrêt par le noyau (OOM) : `journalctl -k | grep -i oom`. Augmenter `MemoryLimit`, ou réduire `max_query_series` et `max_query_parallelism`.

### Erreurs dans les panneaux Grafana
| Message | Cause | Action |
|---|---|---|
| `maximum number of series (50000) reached` | Trop d'URLs ou de visiteurs distincts sur la période (scan) | Réduire la période. Voir `03-parametres.md` §5 |
| `query timed out` | Période très longue | Réduire la période ; `query_timeout` |
| Visiteurs = 1 ou très faible | `remote_addr` = IP du proxy (X-Forwarded-For absent ou proxy non déclaré) | Ajouter l'IP du proxy dans `set_real_ip_from` |
| Upstream P95 vide | URL servie sans backend (fichier, `return`) | Normal |

### Retard de collecte
`loki_source_file_file_bytes_total - loki_source_file_read_bytes_total` grandit : Alloy est limité (`stage.limit`), Loki est lent ou indisponible (`batch_retries_total` augmente), ou un `tc` est actif. Le retard se résorbe seul tant que la rotation locale (7 jours) n'a pas supprimé les fichiers.

### Rejets côté Loki
```bash
sudo journalctl -u loki --since "-1h" --no-pager | grep -E "rate limit|too far behind|out of order|max streams"
```
- `ingestion rate limit exceeded` (429) : Alloy réessaie (`retry_on_http_429 = true`). Augmenter `ingestion_rate_mb` si c'est durable.
- `entry too far behind` : horloge du serveur web ou coupure de plus de 7 jours.

## 4. Purger des données de test

Les lignes du site de test portent `"server_name":"loki-test"`. API de suppression (active car `retention_enabled: true`) :

```bash
sudo curl -s $C -X POST -G https://127.0.0.1:3100/loki/api/v1/delete \
  --data-urlencode 'query={job="nginx-access"} |= `"server_name":"loki-test"`' \
  --data-urlencode "start=$(date -d '-1 day' +%s)" --data-urlencode "end=$(date +%s)"
sudo curl -s $C https://127.0.0.1:3100/loki/api/v1/delete      # suivi des demandes
```
La suppression est effective après `retention_delete_delay` (2 h) et le passage du compacteur.
