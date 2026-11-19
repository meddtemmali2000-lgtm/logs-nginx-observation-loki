# Sécurité — ce qui n'est PAS dans ce dépôt

Ce dépôt est **public**. Les éléments suivants restent hors Git et sur les serveurs uniquement :

| Élément | Emplacement typique |
|---|---|
| Clé de l'autorité CA (`ca.key`) | `/root/loki-pki/ca.key` |
| Clés privées clients / serveur | `/etc/loki/pki/`, `/etc/alloy/pki/`, `/etc/grafana/pki/` |
| `VISITOR_SALT` | `/etc/default/alloy` |
| Identifiants Garage S3 | `/etc/loki/garage.env` |
| `rpc_secret` / tokens admin Garage | `/etc/garage.toml` |
| Paquet RPM Loki | téléchargé hors ligne, non versionné |

Les fichiers de config du dépôt utilisent des **placeholders** ou des variables d'environnement (`${GARAGE_ACCESS_KEY_ID}`, `VISITOR_SALT=...`).

Si vous forkez ce projet : régénérez **toujours** une nouvelle CA, de nouveaux certificats, un nouveau sel visiteurs et de nouvelles clés Garage.
