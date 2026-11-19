# Paquets hors ligne

Ce dossier est volontairement **vide** dans le dépôt public.

Téléchargez le RPM Loki sur une machine avec Internet, puis vérifiez l'empreinte :

```bash
wget https://github.com/grafana/loki/releases/download/v3.7.10/loki-3.7.10-1.x86_64.rpm
sha256sum loki-3.7.10-1.x86_64.rpm
# attendu : 900dcd39fa6365046fb9f371501a1f9a2067a404c9182e38ac2eb666a1542510
```

Ne committez jamais le fichier `.rpm` (trop volumineux et hors périmètre Git).
