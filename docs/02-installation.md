# Phase 3 — Procédure d'installation

> À exécuter **après validation** des configurations. Chaque étape se termine par un contrôle.
> Ordre impératif : pare-feu → Loki → Grafana → Nginx → Alloy.

Arborescence du dépôt utilisée ci-dessous (`$REPO` = ce dossier sur le poste d'administration) :

| Fichier du dépôt | Destination |
|---|---|
| `offline-packages/loki-3.7.10-1.x86_64.rpm` | VM centrale (transfert hors ligne) |
| `loki/config.yml` | `/etc/loki/config.yml` |
| `loki/systemd/loki.service.d/override.conf` | `/etc/systemd/system/loki.service.d/override.conf` |
| `loki/firewall/loki-firewall.sh` / `.service` / `.sysconfig` | `/usr/local/sbin/`, `/etc/systemd/system/`, `/etc/sysconfig/loki-firewall` |
| `pki/gen-certs.sh` | VM centrale, `/root/loki-pki` |
| `grafana/provisioning/datasources/loki-nginx.yaml` | `/etc/grafana/provisioning/datasources/` |
| `grafana/dashboards/nginx-analytics-loki.json` | Import dans Grafana |
| `nginx/conf.d/10-json-analytics-log.conf` | `/etc/nginx/conf.d/` (serveur web) |
| `nginx/logrotate-nginx` | `/etc/logrotate.d/nginx` (serveur web) |
| `alloy/config.alloy`, `alloy/default-alloy` | `/etc/alloy/config.alloy`, `/etc/default/alloy` |
| `alloy/systemd/alloy.service.d/override.conf` | `/etc/systemd/system/alloy.service.d/override.conf` |

---

## Étape 0 — Prérequis à régler avant de commencer

1. **Horloge de la VM centrale** : `chronyc tracking` indique `Leap status: Not synchronised` (pool Internet injoignable ; les NTP internes `192.168.173.5/.6` utilisés par le frontend ne répondent pas depuis la VM centrale ; décalage mesuré le 13/11/2026 : +4,6 s, toléré par Loki). Loki refuse les entrées horodatées dans le futur, et un décalage fausse les graphiques. Pointer chrony vers le serveur NTP interne, puis vérifier :
   ```bash
   chronyc sources -v
   chronyc tracking | grep -E "Leap|System time"   # attendu : Leap status : Normal
   ```
2. **Espace disque** : prévoir 20 Go pour `/var/lib/loki`. Un volume logique (LV) dédié est recommandé si le groupe de volumes a de l'espace libre (`sudo vgs`). Dans ce cas, créer et monter le LV sur `/var/lib/loki` avant l'étape 2.
3. **Fenêtre de maintenance Grafana** : l'étape 4 redémarre `grafana-server` (environ 10 s d'indisponibilité).

---

## Étape 1 — VM centrale : transfert hors ligne

Depuis le poste d'administration :

```bash
ssh m.temmali@192.168.23.24 'mkdir -p ~/loki-deploy'
scp -r "$REPO"/offline-packages/loki-3.7.10-1.x86_64.rpm "$REPO"/loki "$REPO"/pki "$REPO"/grafana \
    m.temmali@192.168.23.24:~/loki-deploy/
```

Sur la VM centrale :

```bash
cd ~/loki-deploy
sha256sum loki-3.7.10-1.x86_64.rpm
# attendu : 900dcd39fa6365046fb9f371501a1f9a2067a404c9182e38ac2eb666a1542510
# (empreinte publiée par GitHub pour cet asset de la release v3.7.10)
```

## Étape 2 — VM centrale : pare-feu du port 3100 (avant Loki)

Le post-install du RPM **démarre immédiatement Loki** avec sa configuration par défaut : HTTP sans TLS, ouvert sur toutes les interfaces. Le filtrage doit donc être actif avant l'installation.

```bash
sudo install -m 0750 loki/firewall/loki-firewall.sh /usr/local/sbin/loki-firewall.sh
sudo install -m 0644 loki/firewall/loki-firewall.sysconfig /etc/sysconfig/loki-firewall
sudo install -m 0644 loki/firewall/loki-firewall.service /etc/systemd/system/loki-firewall.service
sudo systemctl daemon-reload
sudo systemctl enable --now loki-firewall
sudo iptables -L LOKI_INGRESS -n -v      # lo ACCEPT, 192.168.206.112 ACCEPT, DROP
sudo iptables -L INPUT -n --line-numbers | head -3   # saut vers LOKI_INGRESS en tête, tcp dpt:3100
```

Seul le trafic TCP/3100 passe par la chaîne `LOKI_INGRESS` : les autres services de la VM ne sont pas concernés.

## Étape 3 — VM centrale : installation et configuration de Loki

```bash
# 3.1 Installation (aucune dépendance externe)
sudo rpm -ivh loki-3.7.10-1.x86_64.rpm
sudo systemctl stop loki
sudo rm -rf /tmp/loki                          # données du démarrage par défaut
sudo cp -a /etc/loki/config.yml /etc/loki/config.yml.rpm-orig

# 3.2 Certificats (CA interne + serveur Loki + clients)
sudo bash pki/gen-certs.sh /root/loki-pki 192.168.23.24 central AH-NOC-FRONT-TEST grafana
sudo install -d -m 0750 -o root -g loki /etc/loki/pki
sudo install -m 0644 -o root -g loki /root/loki-pki/ca.crt   /etc/loki/pki/ca.crt
sudo install -m 0644 -o root -g loki /root/loki-pki/loki.crt /etc/loki/pki/loki.crt
sudo install -m 0640 -o root -g loki /root/loki-pki/loki.key /etc/loki/pki/loki.key

# 3.3 Configuration, stockage, limites systemd
sudo install -m 0640 -o root -g loki loki/config.yml /etc/loki/config.yml
sudo install -d -m 0750 -o loki -g loki /var/lib/loki
sudo install -d /etc/systemd/system/loki.service.d
sudo install -m 0644 loki/systemd/loki.service.d/override.conf /etc/systemd/system/loki.service.d/override.conf

# 3.4 Vérification de la configuration, puis démarrage
sudo -u loki /usr/bin/loki -config.file=/etc/loki/config.yml -verify-config
sudo systemctl daemon-reload
sudo systemctl enable --now loki
sudo systemctl status loki --no-pager
```

Contrôle (environ 15 s après le démarrage) :

```bash
P=/root/loki-pki
sudo curl -s --cacert $P/ca.crt --cert $P/client-grafana.crt --key $P/client-grafana.key https://127.0.0.1:3100/ready
# attendu : ready
sudo curl -s -o /dev/null -w "%{http_code}\n" --cacert $P/ca.crt https://127.0.0.1:3100/ready
# attendu : 000 (refusé : pas de certificat client)
sudo ss -lntp | grep -E ':3100|:9096'
# attendu : 0.0.0.0:3100 et 127.0.0.1:9096
```

## Étape 4 — VM centrale : Grafana (datasource + dashboard + droits)

```bash
# 4.1 Certificat client de Grafana (lisible par l'utilisateur grafana uniquement)
sudo install -d -m 0750 -o root -g grafana /etc/grafana/pki
sudo install -m 0644 -o root -g grafana /root/loki-pki/ca.crt /etc/grafana/pki/ca.crt
sudo install -m 0644 -o root -g grafana /root/loki-pki/client-grafana.crt /etc/grafana/pki/client-grafana.crt
sudo install -m 0640 -o root -g grafana /root/loki-pki/client-grafana.key /etc/grafana/pki/client-grafana.key

# 4.2 Datasource "Loki-Nginx" (uid loki-nginx). L'ancienne datasource "loki" n'est pas modifiée.
sudo install -m 0640 -o root -g grafana grafana/provisioning/datasources/loki-nginx.yaml \
     /etc/grafana/provisioning/datasources/loki-nginx.yaml
sudo systemctl restart grafana-server      # ~10 s d'indisponibilité
```

Contrôle : dans Grafana, ouvrir *Connections → Data sources → Loki-Nginx → Test* : « Data source successfully connected ».

**Dashboard et droits** (compte administrateur Grafana) :

1. Créer le dossier **Nginx** : *Dashboards → New → New folder*.
2. Importer le dashboard dans ce dossier : *Dashboards → New → Import → Upload JSON file* avec `grafana/dashboards/nginx-analytics-loki.json`.
3. Droits sur le dossier **Nginx** (*Folder → Permissions*) :
   - retirer les droits implicites du rôle *Editor* si nécessaire ;
   - donner *View* à l'équipe des lecteurs (ex. équipe « NOC ») et *Edit* aux seuls administrateurs des dashboards.
4. Droits sur la datasource (*Data sources → Loki-Nginx → Permissions*, si la fonctionnalité est disponible dans votre édition) : limiter *Query* aux mêmes équipes.

Recommandations d'authentification Grafana (déjà actives ou à vérifier dans `/etc/grafana/grafana.ini`) :
- `[auth.anonymous] enabled = false` (constaté : valeur par défaut, désactivé) ;
- comptes nominatifs, pas de compte `admin` partagé ;
- Grafana est servi en **HTTP** sur le port 3000 : passer en HTTPS (`[server] protocol = https`, `cert_file`, `cert_key`) avec un certificat émis par la PKI de l'entreprise. Hors périmètre de ce projet, car cela modifie l'accès de production existant.

## Étape 5 — Serveur web : Nginx (format JSON + rotation)

```bash
# Depuis le poste d'administration
scp -r "$REPO"/nginx "$REPO"/alloy "$REPO"/tests root@192.168.206.112:/root/loki-deploy/
```

Sur le serveur web :

```bash
cd /root/loki-deploy

# 5.1 Rotation des logs : logrotate n'est PAS installé (access.log fait 100 Mo).
apt-get update && apt-get install -y logrotate
cp -a /etc/logrotate.d/nginx /root/loki-deploy/logrotate-nginx.orig
install -m 0644 nginx/logrotate-nginx /etc/logrotate.d/nginx
logrotate -d /etc/logrotate.d/nginx 2>&1 | tail -5     # simulation, aucune action
systemctl list-timers logrotate.timer --no-pager        # timer quotidien actif

# 5.2 Format JSON (ajouté à côté de l'access.log existant)
install -m 0644 nginx/conf.d/10-json-analytics-log.conf /etc/nginx/conf.d/10-json-analytics-log.conf
nginx -t && systemctl reload nginx
# Le master Nginx crée le fichier en root:root 0644 : aligner sur les droits de logrotate.
chown www-data:adm /var/log/nginx/access_json.log && chmod 0640 /var/log/nginx/access_json.log

# Contrôle : nouvelles lignes JSON valides
sleep 60; tail -n 2 /var/log/nginx/access_json.log | python3 -m json.tool
```

## Étape 6 — Serveur web : installation et configuration d'Alloy

```bash
# 6.1 Dépôt officiel Grafana (procédure de la documentation Alloy v1.21)
apt-get install -y gpg wget
mkdir -p /etc/apt/keyrings
wget -O /etc/apt/keyrings/grafana.asc https://apt.grafana.com/gpg-full.key
chmod 644 /etc/apt/keyrings/grafana.asc
echo "deb [signed-by=/etc/apt/keyrings/grafana.asc] https://apt.grafana.com stable main" > /etc/apt/sources.list.d/grafana.list
apt-get update
apt-get install -y alloy=1.21.3-1
apt-mark hold alloy          # pas de mise à jour non planifiée
alloy --version

# 6.2 Certificat client (copié depuis /root/loki-pki de la VM centrale via le poste d'administration :
#     ca.crt, client-AH-NOC-FRONT-TEST.crt, client-AH-NOC-FRONT-TEST.key)
install -d -m 0750 -o root -g alloy /etc/alloy/pki
install -m 0644 -o root -g alloy ca.crt /etc/alloy/pki/ca.crt
install -m 0644 -o root -g alloy client-AH-NOC-FRONT-TEST.crt /etc/alloy/pki/client.crt
install -m 0640 -o root -g alloy client-AH-NOC-FRONT-TEST.key /etc/alloy/pki/client.key
shred -u client-AH-NOC-FRONT-TEST.key

# 6.3 Configuration
install -m 0640 -o root -g alloy alloy/config.alloy /etc/alloy/config.alloy
install -m 0640 -o root -g alloy alloy/default-alloy /etc/default/alloy
SALT=$(openssl rand -hex 32)          # à conserver : identique sur tous les serveurs web
sed -i "s/^VISITOR_SALT=.*/VISITOR_SALT=\"$SALT\"/" /etc/default/alloy
install -d /etc/systemd/system/alloy.service.d
install -m 0644 alloy/systemd/alloy.service.d/override.conf /etc/systemd/system/alloy.service.d/override.conf

# 6.4 Vérification syntaxique, puis démarrage
alloy fmt /etc/alloy/config.alloy > /dev/null && echo "syntaxe OK"
id alloy                               # doit appartenir au groupe adm (lecture de /var/log/nginx)
systemctl daemon-reload
systemctl enable --now alloy
systemctl status alloy --no-pager
```

Contrôle :

```bash
journalctl -u alloy --since "-5 min" --no-pager | grep -iE "error|warn" | tail
curl -s 127.0.0.1:12345/metrics | grep -E "^loki_write_sent_entries_total|^loki_source_file_read_lines_total|^loki_process_dropped_lines_total"
```

Dans Grafana : le dashboard **Nginx — Analyse du trafic (Loki)** affiche des données après environ 10 s.

Au premier démarrage, Alloy lit `error.log` depuis le début. Loki rejette alors une fois les entrées de plus de 7 jours (`status=400 ... has timestamp too old`, compteur `loki_write_dropped_entries_total{reason="ingester_error"}`). C'est attendu et sans conséquence pour les lignes récentes.

## Étape 7 — Ajouter un nouveau serveur web

1. VM centrale : `sudo bash pki/gen-certs.sh /root/loki-pki 192.168.23.24 central <HOSTNAME>` (la CA existante est réutilisée).
2. VM centrale : ajouter l'IP dans `/etc/sysconfig/loki-firewall`, puis `sudo systemctl restart loki-firewall`.
3. Serveur web : étapes 5 et 6, avec le **même** `VISITOR_SALT` et les labels `site` / `environment` / `application` adaptés dans `config.alloy`.

## Retour arrière

| Composant | Commande |
|---|---|
| Alloy | `systemctl disable --now alloy && apt-get remove alloy` |
| Format JSON Nginx | `rm /etc/nginx/conf.d/10-json-analytics-log.conf && nginx -t && systemctl reload nginx` |
| Loki | `sudo systemctl disable --now loki && sudo rpm -e loki` (données dans `/var/lib/loki`) |
| Pare-feu | `sudo systemctl disable --now loki-firewall` (retire la chaîne et le saut) |
| Datasource | supprimer `/etc/grafana/provisioning/datasources/loki-nginx.yaml` + redémarrer Grafana |
