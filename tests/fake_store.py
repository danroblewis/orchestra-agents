"""A fake time-series store answering the Prometheus HTTP API (series, label values, metadata, build
info and range queries) with Orchestra's PromQL subset over points held in memory."""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeStore:
    """A store answering the Prometheus API's series, metadata, build info and range queries."""

    def __init__(self, dump, meta: dict | None = None):
        from orchestra.metrics import promql
        fake = self
        self.max_series = 0          # a listing of more series answers 422 (a per-query limit)
        self.busy = 0                # the next N listings answer 429

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self, form):
                path = urllib.parse.urlsplit(self.path).path.rsplit("/", 1)[-1]
                one = lambda k, d=None: (form.get(k) or [d])[0]
                if path in ("series", "values"):
                    seen = {}
                    for m in form.get("match[]") or []:
                        for lab, _ in promql.select(dump, m, float(one("start", 0)), float(one("end", time.time()))):
                            seen.setdefault(tuple(sorted(lab.items())), lab)
                    data = list(seen.values())
                    if path == "values":
                        data = sorted({lab["__name__"] for lab in data})
                    elif fake.max_series and len(data) > fake.max_series:
                        return self._send(422, {"status": "error", "error": f"query matched {len(data)} series, over the limit"})
                    if fake.busy:
                        fake.busy -= 1
                        return self._send(429, {"status": "error", "error": "slow down"})
                elif path == "metadata":
                    data = meta if meta is not None else {"http_requests_total": [{"type": "counter", "help": "Requests served.", "unit": ""}]}
                elif path == "buildinfo":
                    data = {"version": "fake-1"}
                elif path == "query_range":
                    res = promql.query_range(dump, one("query"), float(one("start")), float(one("end")), float(one("step")))
                    data = {"resultType": "matrix", "result": [{"metric": lab, "values": [[t, repr(v)] for t, v in pts]} for lab, pts in res]}
                elif path in ("query", "labels"):
                    data = {"resultType": "vector", "result": []} if path == "query" else ["__name__"]
                else:
                    return self._send(404, {"status": "error", "error": "not found"})
                self._send(200, {"status": "success", "data": data})

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._go(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query))

            def do_POST(self):
                self._go(urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()))

        fake.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        fake.url = f"http://127.0.0.1:{fake.server.server_address[1]}"
        threading.Thread(target=fake.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
