"""Local dev server: serves frontend/ and routes /api/* to the Lambda handler.
Jobs run in background threads instead of the worker Lambda; pricing uses local Chrome via renderer/cli.mjs.
AWS credentials come from the usual AWS_PROFILE / environment settings.

Usage: python scripts/dev_server.py   ->  http://localhost:8787
"""
import http.server
import os
import sys
import threading

os.environ.setdefault("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend", "src"))

import handler  # noqa: E402
import jobs  # noqa: E402


def start(job_id):
    threading.Thread(target=jobs.run, args=(job_id,), daemon=True).start()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=os.path.join(ROOT, "frontend"), **kw)

    def _api(self, method):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode() if method == "POST" else ""
        r = handler.lambda_handler({"httpMethod": method, "path": self.path[4:], "body": body}, None, start=start)
        self.send_response(r["statusCode"])
        for k, v in r["headers"].items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(r["body"].encode())

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._api("POST")
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._api("GET")
        else:
            super().do_GET()

    def log_message(self, fmt, *args):
        if "/api/jobs/" not in self.path:  # polling is noisy
            super().log_message(fmt, *args)


if __name__ == "__main__":
    print("http://localhost:8787")
    http.server.ThreadingHTTPServer(("127.0.0.1", 8787), Handler).serve_forever()
