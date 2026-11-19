# Tests de validation

## 1. Tests déjà réalisés (avant toute installation)

### 1.1 Banc local (poste d'administration, WSL)
Mêmes binaires que la production : Loki **3.7.10**, Alloy **1.21.3**, Grafana **12.0.1**. Mêmes fichiers de configuration (seuls les chemins diffèrent), TLS mutuel actif. Logs simulés par `tests/gen_nginx_json_logs.py` (distribution connue).

| Test | Résultat |
|---|---|
| `loki -verify-config` / `alloy fmt` | OK / OK |
| Connexion sans certificat client | Refusée (`tlsv13 alert certificate required`) |
| Labels présents dans Loki | Exactement `application, environment, hostname, job, site` |
| Filtrage : 500 `.js` 200 + 100 `.png` 304 + 20 sondes du proxy | 620 lignes supprimées ; `old.css` 404 **conservé** |
| Comptage par URL (`/` = 310, `/api/devices` = 200, `/missing` = 60…) | Identique aux valeurs générées |
| Temps moyen de `/slow` | 2,470 s (attendu 2,47) |
| Filtre de volume minimum (≥ 5) | `/rare-slow` (2 requêtes) et `/reset/:token` (3) exclus du classement P95 |
| Visiteurs uniques | 121 = 120 visiteurs simulés + 1 outil curl |
| IP tronquée, jeton d'URL masqué, `ua_family` | `10.20.0.0`, `/reset/:token`, `Chrome` |
| `error.log` : `?token=SECRET123`, `?session=XYZ`, IP client | Supprimés / tronquée |
| **Bande passante** (10 160 lignes) | Nginx : 4,03 Mo → envoyé : **0,48 Mo en 3 requêtes HTTPS** (compression snappy ×4,5 ; ~93 octets/ligne sur le réseau) |
| **Coupure de Loki + rotation du fichier pendant la coupure** | 1 300 lignes attendues, **1 300 reçues**, 0 doublon, 6 renvois |
| Rotation pendant une lecture bloquée (backpressure) | 101/101 lignes (ancien fichier terminé + nouveau fichier) |
| Limitation de débit (`stage.limit` 500/s, `drop = false`) | Rattrapage à 500 lignes/s, 0 ligne perdue |
| Scan simulé de 200 000 URLs uniques | Tableaux par URL hors 4xx : OK en 0,08 s ; tableau 404 : erreur de limite (documentée) |
| Dashboard rendu dans Grafana 12.0.1 | 40 panneaux OK (capture : `docs/img/dashboard-lab.png`) |

### 1.2 Nginx réel de la VM frontend (instance temporaire isolée, supprimée après le test)
`nginx -t` OK sur Nginx 1.26.3. Requêtes avec mot de passe en query string, cookie, en-tête `Authorization: Bearer`, corps POST et guillemets dans le User-Agent :
- JSON valide sur toutes les lignes, guillemets échappés ;
- `/login?user=bob&password=S3cr3t&token=abc` journalisé `/login` ;
- **0 occurrence** des secrets dans le log ;
- `X-Forwarded-For` pris en compte (`203.0.113.45`) depuis un proxy déclaré ;
- `upstream_response_time` vide (`""`) sans backend : géré dans les requêtes (`=~ "^[0-9.]+$"`).

## 2. Tests à réaliser après installation (exigence §10)

Préparation sur le serveur web :

```bash
cd /root/loki-deploy/tests
install -m 0644 nginx-loki-test-site.conf /etc/nginx/sites-enabled/zz-loki-test
nginx -t && systemctl reload nginx
python3 slow_backend.py &          # backend lent sur 127.0.0.1:8089
```

| Scénario §10 | Méthode | Critère de réussite |
|---|---|---|
| Générer des 200 / 404 / 500 | `python3 run_validation.py` | Comptes exacts par code (98 / 25 / 10, plus 502 = 5 et 503 = 5) |
| URL lente, temps moyen et P95 | idem (`/test/slow`, 20 requêtes de 0,1 à 2,0 s) | Moyenne 1,05 s ±0,05 ; P95 1,905 s ±0,05 ; max 2,0 s |
| Classement des URLs lentes | idem | `/test/slow` en tête (P95, ≥ 5 requêtes) |
| Nombre de requêtes par URL | idem | Exact pour 9 URLs ; `app.js` 200 absent (filtré) |
| Visiteurs uniques estimés | idem (25 IP `10.99.0.x` via X-Forwarded-For) | 25 visiteurs, un seul réseau `10.99.0.0` |
| Données sensibles | idem (`?ms=…&token=SECRET`) | Absentes des lignes stockées |
| Coupure réseau Alloy → Loki | `./outage_test.sh 300 200` (bloque le port 3100 pendant 5 min) | 200/200 lignes reçues après rétablissement, `dropped` = 0 |
| Coupure longue + rotation | `./outage_test.sh 3600 500`, puis `logrotate -f /etc/logrotate.d/nginx` pendant la coupure | Idem |
| Bande passante avant / après | `./bandwidth_measure.sh 3600` | Rapport : octets Nginx bruts vs octets réels sur le réseau (TLS inclus, compteur iptables) |
| Dashboard | Vérification visuelle de chaque section sur `now-1h` | Valeurs cohérentes avec `run_validation.py` |

Après les tests :

```bash
rm /etc/nginx/sites-enabled/zz-loki-test && nginx -t && systemctl reload nginx
kill %1                                    # slow_backend.py
```
Purger ensuite les lignes de test dans Loki (`05-verification-depannage.md` §4).

### Résultats obtenus sur les VM (13/11/2026, après installation)

| Test | Résultat |
|---|---|
| `run_validation.py` | **25/25 contrôles réussis** : comptes par URL et par code exacts ; moyenne 1,052 s, P95 1,907 s, max 2,002 s, P95 upstream 1,907 s ; `/test/slow` classée en tête ; 25 visiteurs ; IP tronquées ; `token=SECRET` absent |
| `outage_test.sh 300 200` | Coupure de 5 min : 8 renvois, **200/200 lignes reçues**, 0 abandonnée, 0 doublon |
| `bandwidth_measure.sh 150` (3 000 requêtes, dont 30 % de statiques) | Nginx : 1 186 741 octets → envoyé : 2 090 lignes, 171 044 octets snappy en 1 requête → **178 205 octets sur le réseau (TLS inclus), soit 85 % de réduction** (~85 octets/ligne) |
| Labels dans Loki | `application, environment, hostname, job, site` uniquement |

Les lignes de test ont été purgées (API delete, effective après 2 h).

### Mesure « avant optimisation »
`bandwidth_measure.sh` compare, sur la même période :
1. **Sans optimisation** : volume brut écrit par Nginx, soit ce qu'un envoi ligne à ligne, non compressé et non filtré, transmettrait (borne basse, sans compter le surcoût HTTP/TLS par ligne) ;
2. **Avec optimisation** : octets réellement émis vers Loki (TLS + HTTP + protobuf snappy), mesurés par un compteur iptables sans action.

Pour mesurer aussi l'effet du seul batching, on peut lancer la mesure une première fois avec `batch_wait = "100ms"` et `batch_size = "1KiB"` dans `config.alloy` (`systemctl reload alloy`), puis avec les valeurs retenues.
