#!/usr/bin/env python3
"""Backend HTTP de test : répond après ?ms=<millisecondes> (127.0.0.1:8089 uniquement)."""
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        ms = int(parse_qs(urlparse(self.path).query).get("ms", ["0"])[0])
        time.sleep(ms / 1000)
        body = f"slept {ms} ms\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", 8089), Handler).serve_forever()
