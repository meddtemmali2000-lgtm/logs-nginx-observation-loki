#!/usr/bin/env python3
"""Génère des lignes au format Nginx `json_analytics` avec une distribution connue,
pour valider le pipeline Alloy et les requêtes LogQL sans trafic réel.

Usage : gen_nginx_json_logs.py <access_json.log> <error.log> [minutes_en_arriere]
Résultats attendus (affichés à la fin) : nombre de lignes par URL, P95 de /slow, etc.
"""
import json
import random
import sys
from datetime import datetime, timedelta, timezone

random.seed(42)
access_path, error_path = sys.argv[1], sys.argv[2]
span_min = int(sys.argv[3]) if len(sys.argv) > 3 else 30
now = datetime.now(timezone.utc)

CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
EDGE = CHROME + " Edg/142.0.0.0"
CURL = "curl/8.14.1"

# (uri, status, nb, (rt_min, rt_max), upstream?)
PLAN = [
    ("/", "200", 300, (0.005, 0.050), True),
    ("/api/devices", "200", 200, (0.020, 0.120), True),
    ("/slow", "200", 40, (2.000, 3.000), True),
    ("/rare-slow", "200", 2, (9.000, 9.500), True),          # trop peu de requêtes
    ("/missing", "404", 60, (0.001, 0.003), False),
    ("/api/crash", "500", 15, (0.100, 0.300), True),
    ("/api/gateway", "502", 8, (0.010, 0.020), True),
    ("/api/busy", "503", 5, (0.001, 0.002), True),
    ("/static/app.js", "200", 500, (0.001, 0.002), False),   # doit être supprimé
    ("/static/logo.png", "304", 100, (0.001, 0.002), False), # doit être supprimé
    ("/static/old.css", "404", 7, (0.001, 0.002), False),    # doit être conservé
    ("/reset/" + "a" * 40, "200", 3, (0.010, 0.020), True),  # jeton masqué
]
VISITORS = [(f"10.20.{i // 250}.{i % 250 + 1}", random.choice([CHROME, FIREFOX, EDGE])) for i in range(120)]

lines, expected = [], {}
for uri, status, n, (lo, hi), ups in PLAN:
    rts = []
    for _ in range(n):
        ip, ua = random.choice(VISITORS)
        rt = round(random.uniform(lo, hi), 3)
        rts.append(rt)
        ts = now - timedelta(seconds=random.uniform(0, span_min * 60))
        lines.append((ts, {
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "server_name": "lab", "remote_addr": ip, "request_method": "GET" if "devices" not in uri else random.choice(["GET", "POST"]),
            "uri": uri, "status": status, "body_bytes_sent": str(random.randint(100, 9000)),
            "request_time": f"{rt:.3f}", "upstream_response_time": f"{max(rt - 0.001, 0):.3f}" if ups else "",
            "http_user_agent": ua, "request_id": "%032x" % random.getrandbits(128),
        }))
    rts.sort()
    expected[uri] = {"count": n, "status": status, "avg": round(sum(rts) / n, 3), "max": rts[-1]}

# Sondes du reverse proxy (200 => supprimées) et un outil (curl).
for _ in range(20):
    ts = now - timedelta(seconds=random.uniform(0, span_min * 60))
    lines.append((ts, {"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S+00:00"), "server_name": "lab", "remote_addr": "192.168.206.40",
                       "request_method": "GET", "uri": "/", "status": "200", "body_bytes_sent": "7449", "request_time": "0.004",
                       "upstream_response_time": "0.004", "http_user_agent": "NPM Manager-upstream-check", "request_id": "%032x" % random.getrandbits(128)}))
for _ in range(10):
    ts = now - timedelta(seconds=random.uniform(0, span_min * 60))
    lines.append((ts, {"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S+00:00"), "server_name": "lab", "remote_addr": "203.0.113.77",
                       "request_method": "GET", "uri": "/", "status": "200", "body_bytes_sent": "7449", "request_time": "0.004",
                       "upstream_response_time": "0.004", "http_user_agent": CURL, "request_id": "%032x" % random.getrandbits(128)}))

lines.sort(key=lambda x: x[0])
with open(access_path, "a") as f:
    for _, d in lines:
        f.write(json.dumps(d, separators=(",", ":")) + "\n")

with open(error_path, "a") as f:
    ts = now.strftime("%Y/%m/%d %H:%M:%S")
    f.write(f'{ts} [error] 830#830: *1234 connect() failed (111: Connection refused) while connecting to upstream, '
            f'client: 10.20.0.5, server: lab, request: "GET /api/gateway?token=SECRET123 HTTP/1.1", '
            f'upstream: "http://127.0.0.1:8000/api/gateway?token=SECRET123", host: "lab", referrer: "https://lab/page?session=XYZ"\n')

print(json.dumps({"total_lines_written": len(lines), "expected_kept": len(lines) - 500 - 100 - 20,
                  "visitors_pool": len(VISITORS), "per_uri": expected}, indent=1))
