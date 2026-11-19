# Requêtes LogQL utilisées par le dashboard

> Fichier généré à partir de `grafana/dashboards/nginx-analytics-loki.json` (source : `grafana/build_dashboard.py`).

## Principes d'optimisation appliqués

1. **Sélecteur de flux minimal** : `{job="nginx-access", site=~"$site", …}`. Les 5 labels sont de faible cardinalité, donc l'index TSDB réduit la lecture aux seuls flux utiles.
2. **Filtre de ligne avant parsing** : ``|~ `"status":"5[0-9]{2}"` `` agit sur le texte brut, sans décoder le JSON. C'est l'opération la moins coûteuse de LogQL. Utilisé pour toutes les classes de statut.
3. **Parsing JSON ciblé** : `| json uri="uri"` extrait uniquement le champ nécessaire, au lieu de `| json` qui extrait tous les champs.
4. **`unwrap` + `__error__=""`** : les valeurs non numériques (`upstream_response_time` vide ou liste `0.01, 0.02` en cas de retry) sont écartées proprement.
5. **Requêtes instantanées sur `[$__range]`** pour les statistiques et tableaux : un seul point calculé (et mis en cache), au lieu d'une série temporelle.
6. **Exclusion des 4xx** dans les tableaux par URL (sections C et D) : protège Loki contre l'explosion de cardinalité provoquée par les scans de vulnérabilités.
7. **Seuil de représentativité** `and on (uri) (… >= $min_requests)` : le P95 n'est calculé que pour les URLs ayant assez de requêtes.
8. **Visiteurs** : `count(sum by (visitor_id) (…))` compte au moment de la requête des empreintes déjà calculées par Alloy, sans label à forte cardinalité dans l'index.

Variables du dashboard : `$site`, `$environment`, `$application`, `$hostname` (labels Loki, multi-sélection), `$min_requests` (volume minimum : liste 1, 3, 5, 10, 20, 50, 100 ; 3 par défaut), `$ds` (datasource).

## Requêtes par panneau


### A. Vue générale du trafic

**Requêtes (période)** (stat)

- `A` (instantanée) :

  ```logql
  sum(count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} [$__range]))
  ```

**Requêtes / seconde (moy.)** (stat)

- `A` (instantanée) :

  ```logql
  sum(count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} [$__range])) / $__range_s
  ```

**Visiteurs uniques estimés** (stat)

- `A` (instantanée) :

  ```logql
  count(sum by (visitor_id) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} | json visitor_id="visitor_id" [$__range])))
  ```

**Serveurs web actifs** (stat)

- `A` (instantanée) :

  ```logql
  count(count by (hostname) (count_over_time({job=~"nginx-access|nginx-error", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} [$__range])))
  ```

**Requêtes (24 dernières heures)** (stat)

- `A` (instantanée) :

  ```logql
  sum(count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} [24h]))
  ```

**Temps de réponse P95** (stat)

- `A` (instantanée) :

  ```logql
  quantile_over_time(0.95, {job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} | json request_time="request_time" | unwrap request_time | __error__="" [$__range]) by (job)
  ```

**Évolution du trafic par heure** (timeseries)

- `A` (plage, légende Requêtes / heure) :

  ```logql
  sum(count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} [1h]))
  ```

**Requêtes par seconde par serveur** (timeseries)

- `A` (plage, légende {{hostname}}) :

  ```logql
  sum by (hostname) (rate({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} [$__auto]))
  ```


### B. Répartition des codes HTTP

**2xx — Succès** (stat)

- `A` (instantanée) :

  ```logql
  sum(count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"2[0-9]{2}"` [$__range]))
  ```

**3xx — Redirections** (stat)

- `A` (instantanée) :

  ```logql
  sum(count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"3[0-9]{2}"` [$__range]))
  ```

**4xx — Erreurs client** (stat)

- `A` (instantanée) :

  ```logql
  sum(count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"4[0-9]{2}"` [$__range]))
  ```

**5xx — Erreurs serveur** (stat)

- `A` (instantanée) :

  ```logql
  sum(count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"5[0-9]{2}"` [$__range]))
  ```

**Répartition par code HTTP** (piechart)

- `A` (instantanée, légende {{status}}) :

  ```logql
  sum by (status) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} | json status="status" [$__range]))
  ```

**Évolution par classe de statut** (timeseries)

- `A` (plage, légende 2xx) :

  ```logql
  sum(count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"2[0-9]{2}"` [$__auto]))
  ```
- `B` (plage, légende 3xx) :

  ```logql
  sum(count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"3[0-9]{2}"` [$__auto]))
  ```
- `C` (plage, légende 4xx) :

  ```logql
  sum(count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"4[0-9]{2}"` [$__auto]))
  ```
- `D` (plage, légende 5xx) :

  ```logql
  sum(count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"5[0-9]{2}"` [$__auto]))
  ```

**Évolution des erreurs (4xx / 5xx) par code** (timeseries)

- `A` (plage, légende {{status}}) :

  ```logql
  sum by (status) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"[45][0-9]{2}"` | json status="status" [$__auto]))
  ```


### C. Top 20 des URLs les plus consultées

**Top 20 des URLs les plus consultées** (table)

- `A` (instantanée) :

  ```logql
  topk(20, sum by (uri) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} !~ `"status":"4[0-9]{2}"` | json uri="uri" [$__range])))
  ```
- `B` (instantanée) :

  ```logql
  100 * topk(20, sum by (uri) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} !~ `"status":"4[0-9]{2}"` | json uri="uri" [$__range]))) / ignoring(uri) group_left sum(count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} [$__range]))
  ```


### D. Top 20 des URLs les plus lentes (classement par P95)

**Top 20 des URLs les plus lentes** (table)

- `A` (instantanée) :

  ```logql
  sum by (uri) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} !~ `"status":"4[0-9]{2}"` | json uri="uri" [$__range])) >= $min_requests
  ```
- `B` (instantanée) :

  ```logql
  avg_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} !~ `"status":"4[0-9]{2}"` | json uri="uri", request_time="request_time" | unwrap request_time | __error__="" [$__range]) by (uri) and on (uri) (sum by (uri) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} !~ `"status":"4[0-9]{2}"` | json uri="uri" [$__range])) >= $min_requests)
  ```
- `C` (instantanée) :

  ```logql
  quantile_over_time(0.95, {job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} !~ `"status":"4[0-9]{2}"` | json uri="uri", request_time="request_time" | unwrap request_time | __error__="" [$__range]) by (uri) and on (uri) (sum by (uri) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} !~ `"status":"4[0-9]{2}"` | json uri="uri" [$__range])) >= $min_requests)
  ```
- `D` (instantanée) :

  ```logql
  max_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} !~ `"status":"4[0-9]{2}"` | json uri="uri", request_time="request_time" | unwrap request_time | __error__="" [$__range]) by (uri) and on (uri) (sum by (uri) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} !~ `"status":"4[0-9]{2}"` | json uri="uri" [$__range])) >= $min_requests)
  ```
- `E` (instantanée) :

  ```logql
  quantile_over_time(0.95, {job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} !~ `"status":"4[0-9]{2}"` | json uri="uri", upstream_response_time="upstream_response_time" | upstream_response_time =~ "^[0-9.]+$" | unwrap upstream_response_time | __error__="" [$__range]) by (uri) and on (uri) (sum by (uri) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} !~ `"status":"4[0-9]{2}"` | json uri="uri" [$__range])) >= $min_requests)
  ```

**Temps de réponse : total vs serveur applicatif** (timeseries)

- `A` (plage, légende P95 total (request_time)) :

  ```logql
  quantile_over_time(0.95, {job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} | json request_time="request_time" | unwrap request_time | __error__="" [$__auto]) by (job)
  ```
- `B` (plage, légende P95 upstream) :

  ```logql
  quantile_over_time(0.95, {job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} | json upstream_response_time="upstream_response_time" | upstream_response_time =~ "^[0-9.]+$" | unwrap upstream_response_time | __error__="" [$__auto]) by (job)
  ```
- `C` (plage, légende Moyenne totale) :

  ```logql
  avg_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} | json request_time="request_time" | unwrap request_time | __error__="" [$__auto]) by (job)
  ```


### E. Analyse des visiteurs

**Visiteurs uniques estimés** (stat)

- `A` (instantanée) :

  ```logql
  count(sum by (visitor_id) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} | json visitor_id="visitor_id" [$__range])))
  ```

**Visiteurs uniques estimés par heure** (timeseries)

- `A` (plage, légende Visiteurs / heure) :

  ```logql
  count(sum by (visitor_id) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} | json visitor_id="visitor_id" [1h])))
  ```

**Top 20 des réseaux clients les plus actifs (IP tronquées)** (table)

- `A` (instantanée) :

  ```logql
  topk(20, sum by (remote_addr) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} | json remote_addr="remote_addr" [$__range])))
  ```

**Navigateurs (User-Agent)** (piechart)

- `A` (instantanée, légende {{ua_family}}) :

  ```logql
  sum by (ua_family) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} | json ua_family="ua_family" [$__range]))
  ```

**Méthodes HTTP** (piechart)

- `A` (instantanée, légende {{request_method}}) :

  ```logql
  sum by (request_method) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} | json request_method="request_method" [$__range]))
  ```


### F. Analyse des erreurs

**URLs en erreur 404** (table)

- `A` (instantanée) :

  ```logql
  topk(20, sum by (uri) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"404"` | json uri="uri" [$__range])))
  ```

**URLs en erreur 500** (table)

- `A` (instantanée) :

  ```logql
  topk(20, sum by (uri) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"500"` | json uri="uri" [$__range])))
  ```

**Erreurs 502 / 503** (table)

- `A` (instantanée) :

  ```logql
  topk(20, sum by (uri, status) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"50[23]"` | json uri="uri", status="status" [$__range])))
  ```

**Erreurs par serveur web** (table)

- `A` (instantanée) :

  ```logql
  sum by (hostname, status) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"[45][0-9]{2}"` | json status="status" [$__range]))
  ```

**404 par réseau client (détection de scans)** (table)

- `A` (instantanée) :

  ```logql
  topk(20, sum by (remote_addr) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"404"` | json remote_addr="remote_addr" [$__range])))
  ```

**Évolution des erreurs 5xx par serveur** (timeseries)

- `A` (plage, légende {{hostname}}) :

  ```logql
  sum by (hostname) (count_over_time({job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"5[0-9]{2}"` [$__auto]))
  ```

**Dernières requêtes en erreur serveur (5xx)** (logs)

- `A` (plage) :

  ```logql
  {job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"} |~ `"status":"5[0-9]{2}"` | json | line_format "{{.status}} {{.request_method}} {{.uri}} {{.request_time}}s upstream={{.upstream_response_time}} id={{.request_id}}"
  ```

**Journal d'erreurs Nginx (error.log)** (logs)

- `A` (plage) :

  ```logql
  {job="nginx-error", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"}
  ```

