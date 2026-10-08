"""Supervise the durable worker and expose process liveness for container hosts.

The queue lives in PostgreSQL. This HTTP endpoint is deliberately not an API for
submitting jobs, and process liveness does not prove that jobs are making progress.
"""
from __future__ import annotations

import http.server
import os
import signal
import subprocess
import sys
import threading


def main() -> int:
    child = subprocess.Popen([sys.executable, "-m", "sentinelops.worker"])
    stopping = threading.Event()

    class HealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            healthy = child.poll() is None and not stopping.is_set()
            status = 200 if healthy else 503
            if self.path != "/health/live":
                status = 404
            body = b'{"status":"running"}' if status == 200 else b'{"status":"unavailable"}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), HealthHandler)

    def stop(_signum: int, _frame: object) -> None:
        stopping.set()
        if child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        return child.wait()
    finally:
        server.shutdown()
        server.server_close()
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
