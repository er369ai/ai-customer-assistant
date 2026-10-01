"""Local web server for the assistant. Routes only; the logic is in assistant.py.

    ./start.sh        (or: .venv/bin/python app.py)

Listens on 127.0.0.1 only, and refuses requests whose Host or Origin isn't this machine, so a
website open in another tab can't use the API key behind your back.
"""
import json
import sys
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import assistant as A

HERE = Path(__file__).resolve().parent
UI = HERE / "ui.html"
MAX_BODY = 512 * 1024
LOCAL = {"localhost", "127.0.0.1"}


class Handler(BaseHTTPRequestHandler):
    server_version = "AIAssistant"

    def log_message(self, *args):
        pass

    def _local(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        origin = self.headers.get("Origin")
        return host in LOCAL and (origin is None or urlparse(origin).hostname in LOCAL)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise A.UserError("Request too large.")
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise A.UserError("Malformed request.")
        if not isinstance(body, dict):
            raise A.UserError("Malformed request.")
        return body

    def _handle(self, fn):
        if not self._local():
            return self._send(403, {"error": "Only this computer can use the assistant."})
        try:
            fn()
        except A.UserError as e:
            self._send(400, {"error": str(e)})
        except A.SetupError as e:
            self._send(409, {"error": str(e), "setup": True})
        except A.ServiceError as e:
            self._send(502, {"error": str(e)})
        except Exception as e:
            A.DATA.mkdir(parents=True, exist_ok=True)
            with open(A.DATA / "errors.log", "a", encoding="utf-8") as f:
                f.write(f"--- {datetime.now().isoformat(timespec='seconds')} {self.command} {self.path}\n")
                f.write(traceback.format_exc())
            self._send(500, {"error": f"Unexpected error ({type(e).__name__}); details in data/errors.log"})

    def do_GET(self):
        self._handle(self._get)

    def do_POST(self):
        self._handle(self._post)

    def _get(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/":
            self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/business":
            self._send(200, A.public_business())
        elif url.path == "/api/leads":
            self._send(200, {"leads": A.list_leads(), "remind_hours": A.remind_hours(A.load_business())})
        elif url.path == "/api/chatlog":
            self._send(200, {"lines": A.chat_log(q.get("id", [""])[0])})
        elif url.path == "/api/usage":
            self._send(200, A.usage_stats())
        else:
            self._send(404, {"error": "Not found"})

    def _post(self):
        path = urlparse(self.path).path
        body = self._body()
        if path == "/api/business":
            A.save_business(body)
            self._send(200, A.public_business())
        elif path == "/api/key":
            A.save_key(body.get("key", ""))
            self._send(200, {"key": A.key_status()})
        elif path == "/api/key/test":
            self._send(200, A.test_key())
        elif path == "/api/chat/new":
            self._send(200, {"id": A.new_chat()})
        elif path == "/api/chat":
            self._send(200, A.reply(body.get("id", ""), body.get("text", "")))
        elif path == "/api/leads/status":
            A.set_lead_status(body.get("id", ""), body.get("status", ""))
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "Not found"})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else A.load_business()["port"]
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"{A.load_business()['business'].get('name') or 'Assistant'}: http://localhost:{port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
