# Analyse centralisée des logs Nginx — Alloy + Loki + Grafana (+ Garage S3)

Plateforme légère : **Grafana Alloy 1.21.3** sur chaque serveur web (lecture, filtrage, batch, compression, reprise) → **Loki 3.7.10** monolithique en mTLS sur la VM centrale → stockage objet **Garage S3** → **Grafana 12.0.1**.

> **Dépôt public :** aucun secret n'est versionné (certificats, `VISITOR_SALT`, clés Garage, RPM). Voir [`SECURITY.md`](SECURITY.md).

| VM | Rôle | Ajouté |
|---|---|---|
| `192.168.206.112` (Debian 13, Nginx) | Serveur web | Format de log JSON, logrotate, Alloy |
| `192.168.23.24` (AlmaLinux 8, hors ligne) | Centrale | Loki (RPM), règle iptables port 3100, datasource + dashboard Grafana |

## Documents (à lire dans l'ordre)

| Fichier | Contenu |
|---|---|
| `docs/01-analyse-architecture.md` | État des lieux, architecture, ressources, flux et ports, estimation du trafic, risques, décisions |
| `docs/02-installation.md` | Procédure d'installation pas à pas, puis retour arrière |
| `docs/03-parametres.md` | Paramètres configurables (batch, débit, buffer, rétention, coupure) |
| `docs/04-requetes-logql.md` | Toutes les requêtes LogQL du dashboard |
| `docs/05-verification-depannage.md` | Vérifications et dépannage par symptôme |
| `docs/06-sauvegarde-restauration.md` | Sauvegarde et restauration |
| `docs/07-tests.md` | Résultats des tests au banc et plan de tests après installation |
| `docs/08-guide-pas-a-pas.md` | **Guide complet pour tout refaire à la main** : rôle de chaque outil, chaque fichier et chaque requête expliqués |
| `docs/09-garage-s3.md` | Garage S3 sur `172.18.40.129` : bascule du stockage Loki (test) |
| `docs/10-documentation-complete.md` / `.pdf` | **Documentation débutant complète** (architecture illustrée, glossaire, A→Z) |

## Arborescence

```
nginx/      log_format JSON (sans query string, IP réelle derrière NPM) + logrotate
alloy/      config.alloy, /etc/default/alloy, limites systemd
loki/       config.yml, limites systemd, pare-feu iptables (port 3100)
pki/        gen-certs.sh : CA interne, certificat serveur Loki, certificats clients
grafana/    datasource provisionnée, dashboard JSON (généré par build_dashboard.py)
tests/      générateur de logs, site de test Nginx, validation, coupure réseau, bande passante
offline-packages/  RPM Loki 3.7.10 + SHA256SUMS officiels (VM centrale sans Internet)
```

Le dashboard se régénère avec `python3 grafana/build_dashboard.py`.
