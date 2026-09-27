"""VPN-only ingestion bridge. Does not expose the inventory UI or admin API."""
import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(404, b'{"error":"Not found"}')

    def do_POST(self):
        if self.path != '/api/automation/report':
            return self.reply(404, b'{"error":"Not found"}')
        auth = self.headers.get('Authorization', '')
        if not auth.startswith('Bearer ') or len(auth) > 250 or self.headers.get('Origin'):
            return self.reply(401, b'{"error":"Machine credential required"}')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                raise ValueError()
            self.connection.settimeout(15)
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError()
            request = urllib.request.Request('http://127.0.0.1:' + os.environ.get('REBORN_PORT', '8741') + '/api/automation/report', data=body, headers={'Authorization': auth, 'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(request, timeout=15) as response:
                self.reply(response.status, response.read())
        except urllib.error.HTTPError as error:
            self.reply(error.code, error.read())
        except ValueError:
            self.reply(400, b'{"error":"Invalid payload size"}')
        except (OSError, urllib.error.URLError):
            self.reply(503, b'{"error":"Inventory backend unavailable; retry later"}')

if __name__ == '__main__':
    if '--managed' in sys.argv:
        def parent_closed():
            sys.stdin.buffer.read()
            os._exit(0)
        threading.Thread(target=parent_closed, daemon=True).start()
    while True:
        try:
            server = ThreadingHTTPServer((os.environ.get('REBORN_HEARTBEAT_BIND', '127.0.0.1'), 8742), Handler)
            server.serve_forever()
        except OSError:
            # VPN may not be available yet after login. Never bind a public interface.
            time.sleep(60)
