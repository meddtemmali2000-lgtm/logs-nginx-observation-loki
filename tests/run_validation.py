#!/usr/bin/env python3
"""Validation de bout en bout sur un serveur web (à lancer en root sur la VM frontend).

Prérequis : site de test nginx-loki-test-site.conf actif (127.0.0.1:8088) et
slow_backend.py lancé (127.0.0.1:8089). Interroge Loki directement avec le
certificat client d'Alloy.

Usage : python3 run_validation.py [--loki https://192.168.23.24:3100]
"""
import argparse
import json
import ssl
import sys
import time
import urllib.parse
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--loki", default="https://192.168.23.24:3100")
ap.add_argument("--site", default="http://127.0.0.1:8088")
ap.add_argument("--pki", default="/etc/alloy/pki")
args = ap.parse_args()

ctx = ssl.create_default_context(cafile=f"{args.pki}/ca.crt")
ctx.load_cert_chain(f"{args.pki}/client.crt", f"{args.pki}/client.key")
# Une CA émise par une version antérieure de gen-certs.sh n'a pas keyUsage : refusée en mode strict.
ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
UA = "loki-validation/1.0"
SEL = '{job="nginx-access"} |= `"server_name":"loki-test"`'


def hit(path, n=1, xff=None, ua=UA):
    for _ in range(n):
        req = urllib.request.Request(args.site + path, headers={"User-Agent": ua, **({"X-Forwarded-For": xff} if xff else {})})
        try:
            urllib.request.urlopen(req, timeout=30).read()
        except urllib.error.HTTPError:
            pass


def loki(path, **params):
    url = f"{args.loki}{path}?{urllib.parse.urlencode(params)}"
    return json.load(urllib.request.urlopen(url, context=ctx, timeout=60))


def instant(expr):
    res = loki("/loki/api/v1/query", query=expr, time=str(time.time()))["data"]["result"]
    return {tuple(sorted(r["metric"].items())): float(r["value"][1]) for r in res}


def scalar(expr):
    r = instant(expr)
    return next(iter(r.values())) if r else 0.0


start = time.time()
print("Génération du trafic de test...")
hit("/test/ok", 50)
hit("/test/notfound", 20)
hit("/test/error", 10)
hit("/test/badgateway", 5)
hit("/test/unavailable", 5)
hit("/test/static/app.js", 30)        # doit être filtré par Alloy
hit("/test/static/missing.css", 5)    # 404 statique : doit être conservé
for i in range(1, 26):                # 25 visiteurs distincts (même réseau /24)
    hit("/test/page-a", 1, xff=f"10.99.0.{i}")
hit("/test/page-b", 3)
slow_ms = [100 * i for i in range(1, 21)]  # 0,1 s à 2,0 s
for ms in slow_ms:
    hit(f"/test/slow?ms={ms}&token=SECRET")

EXPECTED_TOTAL = 50 + 20 + 10 + 5 + 5 + 5 + 25 + 3 + 20
print(f"{EXPECTED_TOTAL} lignes attendues dans Loki, attente de l'ingestion...")
rng = lambda: f"[{int(time.time() - start) + 30}s]"
deadline = time.time() + 120
while time.time() < deadline:
    total = scalar(f"sum(count_over_time({SEL} {rng()}))")
    if total >= EXPECTED_TOTAL:
        break
    time.sleep(5)

R = rng()
results = []


def check(name, got, expected, tol=0.0):
    ok = abs(got - expected) <= tol
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:55s} obtenu={got:<10.4g} attendu={expected}{f' ±{tol}' if tol else ''}")


check("Total des lignes (statiques 2xx exclus)", total, EXPECTED_TOTAL)
per_uri = {dict(k)["uri"]: v for k, v in instant(f'sum by (uri) (count_over_time({SEL} | json uri="uri" {R}))').items()}
for uri, n in [("/test/ok", 50), ("/test/notfound", 20), ("/test/error", 10), ("/test/badgateway", 5), ("/test/unavailable", 5),
               ("/test/static/missing.css", 5), ("/test/page-a", 25), ("/test/page-b", 3), ("/test/slow", 20)]:
    check(f"Requêtes {uri}", per_uri.get(uri, 0), n)
check("Fichier statique 200 filtré (/test/static/app.js)", per_uri.get("/test/static/app.js", 0), 0)

status = {dict(k)["status"]: v for k, v in instant(f'sum by (status) (count_over_time({SEL} | json status="status" {R}))').items()}
for code, n in [("200", 98), ("404", 25), ("500", 10), ("502", 5), ("503", 5)]:
    check(f"Code HTTP {code}", status.get(code, 0), n)

top = instant(f'topk(1, sum by (uri) (count_over_time({SEL} | json uri="uri" {R})))')
check("URL la plus consultée = /test/ok", 1.0 if top and dict(next(iter(top)))["uri"] == "/test/ok" else 0.0, 1)

vis = scalar(f'count(sum by (visitor_id) (count_over_time({SEL} |= "/test/page-a" | json visitor_id="visitor_id" {R})))')
check("Visiteurs uniques estimés (/test/page-a)", vis, 25)
nets = instant(f'sum by (remote_addr) (count_over_time({SEL} |= "/test/page-a" | json remote_addr="remote_addr" {R}))')
check("IP tronquées : un seul réseau 10.99.0.0", 1.0 if [dict(k)["remote_addr"] for k in nets] == ["10.99.0.0"] else 0.0, 1)

srt = sorted(x / 1000 for x in slow_ms)
exp_avg = sum(srt) / len(srt)
idx = 0.95 * (len(srt) - 1)
exp_p95 = srt[int(idx)] + (srt[int(idx) + 1] - srt[int(idx)]) * (idx - int(idx))
U = f'{SEL} |= "/test/slow" | json request_time="request_time" | unwrap request_time | __error__=""'
check("Temps moyen /test/slow (s)", scalar(f"avg_over_time({U} {R}) by (job)"), round(exp_avg, 3), tol=0.05)
check("P95 /test/slow (s)", scalar(f"quantile_over_time(0.95, {U} {R}) by (job)"), round(exp_p95, 3), tol=0.05)
check("Temps max /test/slow (s)", scalar(f"max_over_time({U} {R}) by (job)"), 2.0, tol=0.05)
UP = f'{SEL} |= "/test/slow" | json upstream_response_time="upstream_response_time" | unwrap upstream_response_time | __error__=""'
check("P95 upstream /test/slow (s)", scalar(f"quantile_over_time(0.95, {UP} {R}) by (job)"), round(exp_p95, 3), tol=0.05)

slowest = instant(f'topk(1, quantile_over_time(0.95, {SEL} | json uri="uri", request_time="request_time" | unwrap request_time | __error__="" {R}) by (uri) '
                  f'and on (uri) (sum by (uri) (count_over_time({SEL} | json uri="uri" {R})) >= 5))')
check("URL la plus lente (P95, >= 5 requêtes) = /test/slow", 1.0 if slowest and dict(next(iter(slowest)))["uri"] == "/test/slow" else 0.0, 1)

lines = loki("/loki/api/v1/query_range", query=f'{SEL} |= "/test/slow"', limit="5", start=str(int(start * 1e9)))["data"]["result"]
raw = " ".join(v[1] for s in lines for v in s["values"])
check("Query string absente des logs (token=SECRET)", 1.0 if raw and "SECRET" not in raw and "ms=" not in raw else 0.0, 1)

print(f"\n{sum(results)}/{len(results)} contrôles réussis")
sys.exit(0 if all(results) else 1)
