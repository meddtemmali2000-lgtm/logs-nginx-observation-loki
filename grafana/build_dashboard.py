#!/usr/bin/env python3
"""Génère grafana/dashboards/nginx-analytics-loki.json (Grafana 12.0, datasource Loki).

Toutes les requêtes LogQL sont centralisées ici et documentées dans docs/04-requetes-logql.md.
Usage : python3 grafana/build_dashboard.py
"""
import json
import pathlib

DS = {"type": "loki", "uid": "${ds}"}
SEL = '{job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"}'
ESEL = '{job="nginx-error", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"}'
ANY = '{job=~"nginx-access|nginx-error", site=~"$site", environment=~"$environment", application=~"$application", hostname=~"$hostname"}'

# Filtres de ligne (sans parsing JSON, très peu coûteux) sur la ligne compacte produite par Alloy.
ST = lambda rx: f'|~ `"status":"{rx}"`'

# Tableaux par URL (sections C et D) : hors erreurs client 4xx. Un scan de vulnérabilités
# produit des centaines de milliers d'URLs 404 uniques qui dépasseraient max_query_series ;
# les 4xx sont analysées séparément en section F.
NO4XX = '!~ `"status":"4[0-9]{2}"`'
COUNT_BY_URI = f'sum by (uri) (count_over_time({SEL} {NO4XX} | json uri="uri" [$__range]))'
MIN_REQ = f'and on (uri) ({COUNT_BY_URI} >= $min_requests)'
UNWRAP_RT = f'{SEL} {NO4XX} | json uri="uri", request_time="request_time" | unwrap request_time | __error__=""'
UNWRAP_UP = (f'{SEL} {NO4XX} | json uri="uri", upstream_response_time="upstream_response_time" '
             f'| upstream_response_time =~ "^[0-9.]+$" | unwrap upstream_response_time | __error__=""')

_id = [0]


def nid():
    _id[0] += 1
    return _id[0]


def q(expr, ref="A", legend="", instant=False, step=None):
    t = {"refId": ref, "expr": expr, "datasource": DS, "editorMode": "code",
         "queryType": "instant" if instant else "range", "legendFormat": legend}
    if step:
        t["step"] = step
    return t


def row(title, y):
    return {"type": "row", "id": nid(), "title": title, "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []}


def stat(title, expr, x, y, w=4, h=4, unit="short", color="blue", desc="", decimals=None, thresholds=None):
    p = {"type": "stat", "id": nid(), "title": title, "description": desc, "datasource": DS,
         "gridPos": {"h": h, "w": w, "x": x, "y": y},
         "targets": [q(expr, instant=True)],
         "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                     "colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center"},
         "fieldConfig": {"defaults": {"unit": unit, "noValue": "0",
                                      "color": {"mode": "fixed", "fixedColor": color} if not thresholds else {"mode": "thresholds"},
                                      "thresholds": thresholds or {"mode": "absolute", "steps": [{"color": color, "value": None}]}},
                         "overrides": []}}
    if decimals is not None:
        p["fieldConfig"]["defaults"]["decimals"] = decimals
    return p


def ts(title, targets, x, y, w=12, h=8, unit="short", bars=False, stack=False, desc="", interval=None, overrides=None):
    p = {"type": "timeseries", "id": nid(), "title": title, "description": desc, "datasource": DS,
         "gridPos": {"h": h, "w": w, "x": x, "y": y}, "targets": targets,
         "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["sum", "max"], "showLegend": True},
                     "tooltip": {"mode": "multi", "sort": "desc"}},
         "fieldConfig": {"defaults": {"unit": unit, "custom": {
             "drawStyle": "bars" if bars else "line", "fillOpacity": 70 if bars else 15, "lineWidth": 1 if bars else 2,
             "stacking": {"mode": "normal" if stack else "none", "group": "A"}, "showPoints": "never",
             "barAlignment": -1 if bars else 0, "spanNulls": False, "insertNulls": False}},
             "overrides": overrides or []}}
    if interval:
        p["interval"] = interval
    return p


def pie(title, expr, legend, x, y, w=8, h=9, desc=""):
    return {"type": "piechart", "id": nid(), "title": title, "description": desc, "datasource": DS,
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "targets": [q(expr, legend=legend, instant=True)],
            # Requête instantanée = une table (label, Value) : une part par ligne (values=True).
            "options": {"pieType": "donut", "displayLabels": ["percent"],
                        "legend": {"displayMode": "table", "placement": "right", "values": ["value", "percent"], "showLegend": True},
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^Value/", "values": True},
                        "tooltip": {"mode": "single"}},
            "fieldConfig": {"defaults": {"unit": "short"}, "overrides": []}}


def table(title, labels, values, x, y, w, h, sort_ref="A", limit=None, desc="", position=True, empty="Aucune donnée sur la période"):
    """Tableau à partir de requêtes instantanées.

    Le frontend Loki de Grafana renvoie chaque requête instantanée sous forme de table
    (colonnes Time, <labels>, "Value #<refId>") ; "merge" les fusionne sur Time + labels.
    labels : [(label, titre)] ; values : [(refId, expr, titre, unité)].
    """
    targets = [q(expr, ref, instant=True) for ref, expr, _, _ in values]
    columns = labels + [(f"Value #{ref}", t) for ref, _, t, _ in values]
    units = {f"Value #{ref}": u for ref, _, _, u in values if u}
    sort_col = f"Value #{sort_ref}"
    tr = [{"id": "merge", "options": {}},
          {"id": "sortBy", "options": {"sort": [{"field": sort_col, "desc": True}]}}]
    if limit:
        tr.append({"id": "limit", "options": {"limitField": limit}})
    if position:
        # L'index de ligne commence à 0 : Position = index + 1.
        tr.append({"id": "calculateField", "options": {"mode": "index", "alias": "row_index", "index": {"asPercentile": False}, "replaceFields": False}})
        tr.append({"id": "calculateField", "options": {"mode": "binary", "alias": "Position", "replaceFields": False,
                                                       "binary": {"left": "row_index", "operator": "+", "right": "1"}}})
    rename = dict(columns)
    order = {k: i + 1 for i, k in enumerate([c for c, _ in columns])}
    if position:
        order = {"Position": 0, **order}
    tr.append({"id": "organize", "options": {"excludeByName": {"Time": True, "row_index": True}, "indexByName": order, "renameByName": rename}})
    overrides = [{"matcher": {"id": "byName", "options": "Position"},
                  "properties": [{"id": "custom.width", "value": 90}, {"id": "unit", "value": "none"},
                                 {"id": "custom.cellOptions", "value": {"type": "auto"}}]}] if position else []
    for name, unit in (units or {}).items():
        props = [{"id": "unit", "value": unit}]
        if unit == "s":
            props += [{"id": "decimals", "value": None},
                      {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "gradient"}},
                      {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                          {"color": "green", "value": None}, {"color": "#EAB839", "value": 0.5},
                          {"color": "orange", "value": 1}, {"color": "red", "value": 2}]}},
                      {"id": "color", "value": {"mode": "thresholds"}}]
        if unit == "percent":
            props += [{"id": "decimals", "value": 2},
                      {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic"}},
                      {"id": "max", "value": 100}, {"id": "min", "value": 0}]
        overrides.append({"matcher": {"id": "byName", "options": rename.get(name, name)}, "properties": props})
    return {"type": "table", "id": nid(), "title": title, "description": desc, "datasource": DS,
            "gridPos": {"h": h, "w": w, "x": x, "y": y}, "targets": targets, "transformations": tr,
            "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
            "fieldConfig": {"defaults": {"noValue": empty, "custom": {"align": "auto", "filterable": True, "cellOptions": {"type": "auto"}}},
                            "overrides": overrides}}


def logs(title, expr, x, y, w=24, h=10, desc=""):
    return {"type": "logs", "id": nid(), "title": title, "description": desc, "datasource": DS,
            "gridPos": {"h": h, "w": w, "x": x, "y": y}, "targets": [{**q(expr), "maxLines": 200}],
            "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True,
                        "prettifyLogMessage": False, "dedupStrategy": "none"}}


def text(content, x, y, w=24, h=3):
    return {"type": "text", "id": nid(), "title": "", "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "options": {"mode": "markdown", "content": content}}


def var_label(name, label, stream='{job="nginx-access"}'):
    return {"name": name, "label": label, "type": "query", "datasource": DS,
            "query": {"qryType": 1, "query": f"label_values({stream}, {name})", "refId": "LokiVariableQueryEditor-VariableQuery"},
            "definition": f"label_values({stream}, {name})", "refresh": 2, "includeAll": True, "multi": True,
            "allValue": ".+", "current": {"selected": True, "text": ["All"], "value": ["$__all"]}, "sort": 1}


P = []
y = 0

# ---------------------------------------------------------------- A. Vue générale
P.append(row("A. Vue générale du trafic", y)); y += 1
P += [
    stat("Requêtes (période)", f"sum(count_over_time({SEL} [$__range]))", 0, y, desc="Nombre total de requêtes HTTP sur la période sélectionnée (fichiers statiques réussis et sondes du proxy exclus à la collecte)."),
    stat("Requêtes / seconde (moy.)", f"sum(count_over_time({SEL} [$__range])) / $__range_s", 4, y, unit="reqps", decimals=3),
    stat("Visiteurs uniques estimés", f'count(sum by (visitor_id) (count_over_time({SEL} | json visitor_id="visitor_id" [$__range])))', 8, y, color="purple",
         desc="Nombre d'empreintes distinctes (IP + User-Agent, hachées et salées sur le serveur web). Estimation : une IP partagée (NAT) regroupe plusieurs personnes, un utilisateur mobile peut changer d'IP."),
    stat("Serveurs web actifs", f"count(count by (hostname) (count_over_time({ANY} [$__range])))", 12, y, color="green", desc="Serveurs ayant envoyé au moins une ligne sur la période."),
    stat("Requêtes (24 dernières heures)", f"sum(count_over_time({SEL} [24h]))", 16, y, color="blue"),
    stat("Temps de réponse P95", f'quantile_over_time(0.95, {SEL} | json request_time="request_time" | unwrap request_time | __error__="" [$__range]) by (job)', 20, y,
         unit="s", thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 0.5}, {"color": "red", "value": 1}]},
         desc="95 % des requêtes ont été servies en moins de ce temps (request_time Nginx)."),
]
y += 4
P += [
    ts("Évolution du trafic par heure", [q(f"sum(count_over_time({SEL} [1h]))", legend="Requêtes / heure", step="1h")], 0, y, bars=True, interval="1h"),
    ts("Requêtes par seconde par serveur", [q(f"sum by (hostname) (rate({SEL} [$__auto]))", legend="{{hostname}}")], 12, y, unit="reqps"),
]
y += 8

# ---------------------------------------------------------------- B. Codes HTTP
P.append(row("B. Répartition des codes HTTP", y)); y += 1
for i, (lbl, rx, col) in enumerate([("2xx — Succès", "2[0-9]{2}", "green"), ("3xx — Redirections", "3[0-9]{2}", "blue"),
                                    ("4xx — Erreurs client", "4[0-9]{2}", "orange"), ("5xx — Erreurs serveur", "5[0-9]{2}", "red")]):
    P.append(stat(lbl, f"sum(count_over_time({SEL} {ST(rx)} [$__range]))", i * 6, y, w=6, color=col))
y += 4
P += [
    pie("Répartition par code HTTP", f'sum by (status) (count_over_time({SEL} | json status="status" [$__range]))', "{{status}}", 0, y),
    ts("Évolution par classe de statut", [
        q(f"sum(count_over_time({SEL} {ST('2[0-9]{2}')} [$__auto]))", "A", "2xx"),
        q(f"sum(count_over_time({SEL} {ST('3[0-9]{2}')} [$__auto]))", "B", "3xx"),
        q(f"sum(count_over_time({SEL} {ST('4[0-9]{2}')} [$__auto]))", "C", "4xx"),
        q(f"sum(count_over_time({SEL} {ST('5[0-9]{2}')} [$__auto]))", "D", "5xx"),
    ], 8, y, w=16, h=9, stack=True, overrides=[
        {"matcher": {"id": "byName", "options": n}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
        for n, c in [("2xx", "green"), ("3xx", "blue"), ("4xx", "orange"), ("5xx", "red")]]),
]
y += 9
P.append(ts("Évolution des erreurs (4xx / 5xx) par code", [
    q(f'sum by (status) (count_over_time({SEL} {ST("[45][0-9]{2}")} | json status="status" [$__auto]))', legend="{{status}}")], 0, y, w=24, bars=True, stack=True))
y += 8

# ---------------------------------------------------------------- C. Top 20 URLs consultées
P.append(row("C. Top 20 des URLs les plus consultées", y)); y += 1
TOP = f"topk(20, {COUNT_BY_URI})"
P.append(table("Top 20 des URLs les plus consultées", [("uri", "URL")], [
    ("A", TOP, "Nombre de requêtes", None),
    ("B", f"100 * {TOP} / ignoring(uri) group_left sum(count_over_time({SEL} [$__range]))", "Pourcentage du trafic", "percent"),
], 0, y, 24, 12,
    desc="Requêtes hors erreurs client 4xx (bruit des scanners, analysé en section F). Pourcentage = part du trafic total. "
         "Chemins sans query string ; les segments de type jeton (≥ 32 caractères) sont remplacés par :token."))
y += 12

# ---------------------------------------------------------------- D. Top 20 URLs lentes
P.append(row("D. Top 20 des URLs les plus lentes (classement par P95)", y)); y += 1
P.append(text("**Classement par P95 de `request_time`** (temps total Nginx), hors erreurs client 4xx. Seules les URLs ayant au moins **$min_requests requêtes** sur la période sont classées "
              "(variable *Volume minimum* en haut du dashboard) : en dessous, un P95 n'est pas représentatif. "
              "**Upstream P95** = temps du serveur applicatif (`upstream_response_time`) : s'il est proche du P95 total, la lenteur vient de l'application ; "
              "s'il est faible ou vide, elle vient de Nginx, du réseau client ou d'un contenu servi directement.", 0, y, h=3))
y += 3
P.append(table("Top 20 des URLs les plus lentes", [("uri", "URL")], [
    ("A", f"{COUNT_BY_URI} >= $min_requests", "Nombre de requêtes", None),
    ("B", f"avg_over_time({UNWRAP_RT} [$__range]) by (uri) {MIN_REQ}", "Temps moyen", "s"),
    ("C", f"quantile_over_time(0.95, {UNWRAP_RT} [$__range]) by (uri) {MIN_REQ}", "P95", "s"),
    ("D", f"max_over_time({UNWRAP_RT} [$__range]) by (uri) {MIN_REQ}", "Temps maximum", "s"),
    ("E", f"quantile_over_time(0.95, {UNWRAP_UP} [$__range]) by (uri) {MIN_REQ}", "Upstream P95", "s"),
], 0, y, 24, 13, sort_ref="C", limit=20,
    empty="Aucune URL n'atteint le volume minimum sur la période : baisser « Volume minimum » ou élargir la période"))
y += 13
P.append(ts("Temps de réponse : total vs serveur applicatif", [
    q(f'quantile_over_time(0.95, {SEL} | json request_time="request_time" | unwrap request_time | __error__="" [$__auto]) by (job)', "A", "P95 total (request_time)"),
    q(f'quantile_over_time(0.95, {SEL} | json upstream_response_time="upstream_response_time" | upstream_response_time =~ "^[0-9.]+$" | unwrap upstream_response_time | __error__="" [$__auto]) by (job)', "B", "P95 upstream"),
    q(f'avg_over_time({SEL} | json request_time="request_time" | unwrap request_time | __error__="" [$__auto]) by (job)', "C", "Moyenne totale"),
], 0, y, w=24, unit="s"))
y += 8

# ---------------------------------------------------------------- E. Visiteurs
P.append(row("E. Analyse des visiteurs", y)); y += 1
P.append(text("⚠️ **Visiteurs estimés** : une empreinte = un couple (IP, navigateur), haché et salé sur le serveur web. Plusieurs personnes derrière une même IP (NAT, entreprise) "
              "peuvent compter pour un seul visiteur ; un mobile qui change d'IP compte plusieurs fois. Les IP sont **tronquées** (/24 en IPv4, /48 en IPv6) avant l'envoi.", 0, y, h=2))
y += 2
P += [
    stat("Visiteurs uniques estimés", f'count(sum by (visitor_id) (count_over_time({SEL} | json visitor_id="visitor_id" [$__range])))', 0, y, w=6, h=8, color="purple"),
    ts("Visiteurs uniques estimés par heure", [q(f'count(sum by (visitor_id) (count_over_time({SEL} | json visitor_id="visitor_id" [1h])))', legend="Visiteurs / heure", step="1h")],
       6, y, w=18, h=8, bars=True, interval="1h"),
]
y += 8
P += [
    table("Top 20 des réseaux clients les plus actifs (IP tronquées)", [("remote_addr", "Réseau (/24)")], [
        ("A", f'topk(20, sum by (remote_addr) (count_over_time({SEL} | json remote_addr="remote_addr" [$__range])))', "Requêtes", None)],
        0, y, 8, 10),
    pie("Navigateurs (User-Agent)", f'sum by (ua_family) (count_over_time({SEL} | json ua_family="ua_family" [$__range]))', "{{ua_family}}", 8, y, h=10,
        desc="Famille calculée par Alloy à partir du User-Agent. « Outil/Script » = curl, scanners, bibliothèques HTTP."),
    pie("Méthodes HTTP", f'sum by (request_method) (count_over_time({SEL} | json request_method="request_method" [$__range]))', "{{request_method}}", 16, y, h=10),
]
y += 10

# ---------------------------------------------------------------- F. Erreurs
P.append(row("F. Analyse des erreurs", y)); y += 1


def err_table(title, rx, x, w):
    return table(title, [("uri", "URL")], [("A", f'topk(20, sum by (uri) (count_over_time({SEL} {ST(rx)} | json uri="uri" [$__range])))', "Occurrences", None)],
                 x, y, w, 10, empty=f"Aucune erreur {rx} sur la période")


P.append(text("Sur une période contenant un **scan de vulnérabilités massif** (plus de 50 000 URLs 404 différentes), le tableau des 404 peut afficher "
              "*maximum number of series reached* : réduire la période. Le tableau « 404 par réseau client » reste disponible pour identifier la source du scan.", 0, y, h=2))
y += 2
P += [err_table("URLs en erreur 404", "404", 0, 8), err_table("URLs en erreur 500", "500", 8, 8),
      table("Erreurs 502 / 503", [("uri", "URL"), ("status", "Code")],
            [("A", f'topk(20, sum by (uri, status) (count_over_time({SEL} {ST("50[23]")} | json uri="uri", status="status" [$__range])))', "Occurrences", None)],
            16, y, 8, 10, empty="Aucune erreur 502 / 503 sur la période")]
y += 10
P += [
    table("Erreurs par serveur web", [("hostname", "Serveur"), ("status", "Code")],
          [("A", f'sum by (hostname, status) (count_over_time({SEL} {ST("[45][0-9]{2}")} | json status="status" [$__range]))', "Occurrences", None)],
          0, y, 8, 9, position=False, empty="Aucune erreur 4xx / 5xx sur la période"),
    table("404 par réseau client (détection de scans)", [("remote_addr", "Réseau (/24)")],
          [("A", f'topk(20, sum by (remote_addr) (count_over_time({SEL} {ST("404")} | json remote_addr="remote_addr" [$__range])))', "404", None)],
          8, y, 6, 9, position=False, empty="Aucune erreur 404 sur la période"),
    ts("Évolution des erreurs 5xx par serveur", [q(f"sum by (hostname) (count_over_time({SEL} {ST('5[0-9]{2}')} [$__auto]))", legend="{{hostname}}")], 14, y, w=10, h=9, bars=True),
]
y += 9
P.append(logs("Dernières requêtes en erreur serveur (5xx)",
              f'{SEL} {ST("5[0-9]{2}")} | json | line_format "{{{{.status}}}} {{{{.request_method}}}} {{{{.uri}}}} {{{{.request_time}}}}s upstream={{{{.upstream_response_time}}}} id={{{{.request_id}}}}"',
              0, y, h=9))
y += 9
P.append(logs("Journal d'erreurs Nginx (error.log)", ESEL, 0, y, h=9))
y += 9

dashboard = {
    "uid": "nginx-loki-analytics", "title": "Nginx — Analyse du trafic (Loki)", "tags": ["nginx", "loki", "alloy"],
    "timezone": "browser", "schemaVersion": 41, "version": 1, "editable": True, "graphTooltip": 1,
    "time": {"from": "now-24h", "to": "now"}, "refresh": "5m",
    "timepicker": {"refresh_intervals": ["1m", "5m", "15m", "30m", "1h"]},
    "templating": {"list": [
        {"name": "ds", "label": "Source Loki", "type": "datasource", "query": "loki", "current": {"text": "Loki-Nginx", "value": "loki-nginx"}, "hide": 0},
        var_label("site", "Site"), var_label("environment", "Environnement"), var_label("application", "Application"),
        var_label("hostname", "Serveur web", '{job="nginx-access", site=~"$site", environment=~"$environment", application=~"$application"}'),
        {"name": "min_requests", "label": "Volume minimum (URLs lentes)", "type": "custom", "query": "1,3,5,10,20,50,100",
         "options": [{"text": v, "value": v, "selected": v == "3"} for v in ["1", "3", "5", "10", "20", "50", "100"]],
         "current": {"text": "3", "value": "3"}, "includeAll": False, "multi": False,
         "description": "Nombre minimal de requêtes pour qu'une URL soit classée dans le Top 20 des URLs lentes."},
    ]},
    "annotations": {"list": []}, "links": [], "panels": P,
}

out = pathlib.Path(__file__).parent / "dashboards" / "nginx-analytics-loki.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"{out} : {len(P)} panneaux")
