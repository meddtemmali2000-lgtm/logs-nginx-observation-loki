# Procédure de sauvegarde et de restauration

## 1. Ce qu'il faut sauvegarder

| Élément | Emplacement | Criticité | Fréquence |
|---|---|---|---|
| **CA interne + clés** | VM centrale `/root/loki-pki/` (`ca.key` !) | **Critique** : sans `ca.key`, impossible d'ajouter un serveur web sans réémettre tous les certificats | À chaque ajout de serveur ; copie chiffrée hors VM |
| Configuration Loki | `/etc/loki/`, `/etc/systemd/system/loki.service.d/` | Haute | À chaque modification |
| Pare-feu Loki | `/usr/local/sbin/loki-firewall.sh`, `/etc/sysconfig/loki-firewall`, `/etc/systemd/system/loki-firewall.service` | Haute | À chaque modification |
| Datasource + certificat Grafana | `/etc/grafana/provisioning/datasources/loki-nginx.yaml`, `/etc/grafana/pki/` | Haute | À chaque modification |
| Dashboard | `grafana/dashboards/nginx-analytics-loki.json` (dépôt) + base Grafana existante | Moyenne | Export après modification dans l'interface |
| **Sel visiteurs** | Serveurs web `/etc/default/alloy` (`VISITOR_SALT`) | Haute : le changer rompt la continuité du comptage des visiteurs | Une fois (identique sur tous les serveurs) |
| Configuration Alloy / Nginx | `/etc/alloy/`, `/etc/nginx/conf.d/10-json-analytics-log.conf`, `/etc/logrotate.d/nginx` | Moyenne (reproductible depuis le dépôt) | À chaque modification |
| Données Loki | `/var/lib/loki/` | Faible à moyenne : rétention de 14 jours, données d'analyse | Selon le besoin (voir §3) |

Les fichiers de configuration de référence sont dans ce dépôt : la restauration d'une configuration revient à réappliquer `02-installation.md`.

## 2. Sauvegarde de la configuration (VM centrale)

```bash
TS=$(date +%Y%m%d-%H%M)
sudo tar czf /root/loki-config-$TS.tar.gz \
  /etc/loki /etc/systemd/system/loki.service.d \
  /usr/local/sbin/loki-firewall.sh /etc/sysconfig/loki-firewall /etc/systemd/system/loki-firewall.service \
  /etc/grafana/provisioning/datasources/loki-nginx.yaml /etc/grafana/pki \
  /root/loki-pki
# Chiffrement avant de sortir l'archive de la VM (elle contient la clé privée de la CA)
sudo openssl enc -aes-256-cbc -pbkdf2 -salt -in /root/loki-config-$TS.tar.gz -out /root/loki-config-$TS.tar.gz.enc
sudo shred -u /root/loki-config-$TS.tar.gz
```

Serveur web :

```bash
TS=$(date +%Y%m%d-%H%M)
tar czf /root/alloy-config-$TS.tar.gz /etc/alloy /etc/default/alloy /etc/systemd/system/alloy.service.d \
  /etc/nginx/conf.d/10-json-analytics-log.conf /etc/logrotate.d/nginx
chmod 600 /root/alloy-config-$TS.tar.gz
```

## 3. Sauvegarde des données Loki

**Méthode A — Sauvegarde de la VM par Proxmox (recommandée)** : la sauvegarde existante de la VM (vzdump/PBS) inclut `/var/lib/loki`. En mode *snapshot*, l'état est cohérent au niveau du disque ; au redémarrage, Loki rejoue son WAL.

**Méthode B — Archive à froid de `/var/lib/loki`** (cohérente, quelques minutes d'arrêt) :

```bash
sudo systemctl stop loki          # les serveurs web conservent et renverront les lignes (retries infinis)
sudo tar czf /backup/loki-data-$(date +%Y%m%d).tar.gz -C /var/lib loki
sudo systemctl start loki
```

**Aucune perte pendant l'arrêt** : Alloy réessaie indéfiniment et les lignes restent dans les fichiers Nginx. Ce comportement a été testé (voir `07-tests.md`, coupure de Loki : 1 300 lignes sur 1 300 reçues, sans doublon). L'arrêt doit rester inférieur à 7 jours (`reject_old_samples_max_age`).

Volume indicatif : environ 2 Mo/jour au trafic actuel ; environ 75–100 Mo/jour pour 1 million de requêtes/jour.

## 4. Restauration

### 4.1 Configuration Loki / pare-feu / Grafana
```bash
sudo openssl enc -d -aes-256-cbc -pbkdf2 -in loki-config-<TS>.tar.gz.enc -out /root/restore.tar.gz
sudo tar xzf /root/restore.tar.gz -C /          # chemins absolus d'origine
sudo shred -u /root/restore.tar.gz
sudo chown -R root:loki /etc/loki && sudo chmod 0640 /etc/loki/pki/loki.key /etc/loki/config.yml
sudo chown -R root:grafana /etc/grafana/pki && sudo chmod 0640 /etc/grafana/pki/client-grafana.key
sudo systemctl daemon-reload
sudo systemctl restart loki-firewall loki
sudo systemctl restart grafana-server           # si la datasource a été restaurée
```

### 4.2 Données Loki (méthode B)
```bash
sudo systemctl stop loki
sudo mv /var/lib/loki /var/lib/loki.old
sudo tar xzf /backup/loki-data-<date>.tar.gz -C /var/lib
sudo chown -R loki:loki /var/lib/loki
sudo systemctl start loki
# après contrôle : sudo rm -rf /var/lib/loki.old
```
Les données reçues entre la sauvegarde et la restauration sont perdues, sauf celles encore présentes dans les fichiers Nginx locaux. Les données plus anciennes que la rétention (14 jours) sont supprimées par le compacteur au démarrage.

### 4.3 Reconstruction complète de la VM centrale
1. Réinstaller selon `02-installation.md`, étapes 2 à 4, **en restaurant `/root/loki-pki`** au lieu d'exécuter `gen-certs.sh`. Les certificats des serveurs web restent alors valides.
2. Restaurer `/var/lib/loki` si l'historique est nécessaire.
3. Les serveurs web renvoient automatiquement ce qu'ils n'ont pas pu transmettre pendant l'indisponibilité (dans la limite de 7 jours).

### 4.4 Serveur web
```bash
tar xzf alloy-config-<TS>.tar.gz -C /
chown -R root:alloy /etc/alloy && chmod 0640 /etc/alloy/pki/client.key /etc/default/alloy
nginx -t && systemctl reload nginx
systemctl restart alloy
```
**Attention aux doublons** : si le fichier de positions d'Alloy (`/var/lib/alloy/data`) est perdu, Alloy relit `access_json.log` depuis le début. Pour l'éviter lors d'une reconstruction, ajouter temporairement `tail_from_end = true` dans les deux blocs `loki.source.file`, démarrer Alloy, puis retirer l'option.

## 5. Contrôle après restauration
Appliquer la vérification rapide de `05-verification-depannage.md` §1, puis `tests/run_validation.py`.
