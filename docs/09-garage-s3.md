# Garage S3 comme stockage de Loki (test)

Date : 17/11/2026.

## Où est installé quoi

| Rôle | VM | Adresse |
|---|---|---|
| Garage v2.5.0 (stockage S3) | `zabbix-db` | `172.18.40.129` |
| Loki (client S3) | `central` | `192.168.23.24` |

> **Attention :** Garage tourne sur la même machine que PostgreSQL/Zabbix. Il est limité à 1 Go de RAM et 1 cœur (`garage.service`). Pour la production durable, préférez une VM dédiée.

## Ports Garage

| Port | Bind | Usage |
|---|---|---|
| **3900** | `0.0.0.0` | API S3 (filtrée : seulement `192.168.23.24` + localhost, nftables) |
| 3901 | `0.0.0.0` | RPC cluster (1 nœud) |
| 3902 | `127.0.0.1` | Site web S3 (local) |
| 3903 | `127.0.0.1` | API admin (local) |

## Buckets et clé

- Buckets : `loki-chunks`, `loki-ruler`
- Clé : `loki-key` (affichage : `garage key info loki-key --show-secret`)
- Identifiants Loki : `/etc/loki/garage.env` sur `192.168.23.24` (droits `0640 root:loki`)

## Commandes utiles

```bash
# Sur 172.18.40.129
systemctl status garage
garage status
garage bucket info loki-chunks

# Sur 192.168.23.24
sudo systemctl status loki
sudo journalctl -u loki -f
```

## Bascule effectuée

- Avant : `object_store: filesystem` (`/var/lib/loki`)
- Après : `object_store: s3` → Garage `http://172.18.40.129:3900`, région `garage`, path-style
- Loki démarre avec `-config.expand-env=true` et lit `/etc/loki/garage.env`
- Les ~160 Ko de données locales antérieures ne sont plus interrogées (test). Alloy continue d’envoyer les nouvelles lignes.

## Fichiers du dépôt

- `garage/garage.toml.template`, `garage/garage.service`, `garage/garage-nftables.sh`
- `loki/config.yml` (version S3)
