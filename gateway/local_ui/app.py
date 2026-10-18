"""Localhost Bootstrap config UI for gateway device profiles (bind 127.0.0.1 only)."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

CONFIG_PATH = Path(os.environ.get('HFDN_GATEWAY_CONFIG', Path(__file__).resolve().parent.parent / 'gateway_config.json'))
HOST = '127.0.0.1'
PORT = int(os.environ.get('HFDN_GATEWAY_UI_PORT', '8787'))


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    return {
        'api_base': 'https://hfdn.example.org/api',
        'api_key': '',
        'gateway_name': 'local-gateway',
        'devices': [],
    }


def save_config(data):
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding='utf-8')


HTML = '''<!doctype html><html><head><title>HFDN Gateway</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
</head><body class="p-4"><div class="container" style="max-width:720px">
<h1 class="h3 mb-3">HFDN local gateway</h1>
<p class="text-muted">Configuration is stored only on this machine. Bound to 127.0.0.1.</p>
<form method="post" class="vstack gap-3">
<label class="form-label">API base<input class="form-control" name="api_base" value="{api_base}"></label>
<label class="form-label">API key<input class="form-control" name="api_key" value="{api_key}" type="password"></label>
<label class="form-label">Gateway name<input class="form-control" name="gateway_name" value="{gateway_name}"></label>
<label class="form-label">Devices JSON<textarea class="form-control" name="devices" rows="8">{devices}</textarea></label>
<button class="btn btn-primary">Save</button>
</form>
<p class="mt-3 small text-success">{message}</p>
</div></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        cfg = load_config()
        body = HTML.format(
            api_base=cfg.get('api_base', ''),
            api_key=cfg.get('api_key', ''),
            gateway_name=cfg.get('gateway_name', ''),
            devices=json.dumps(cfg.get('devices') or [], indent=2),
            message='',
        ).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length).decode('utf-8')
        form = {k: v[0] for k, v in parse_qs(raw).items()}
        try:
            devices = json.loads(form.get('devices') or '[]')
        except json.JSONDecodeError:
            devices = []
        cfg = {
            'api_base': form.get('api_base', ''),
            'api_key': form.get('api_key', ''),
            'gateway_name': form.get('gateway_name', ''),
            'devices': devices,
        }
        save_config(cfg)
        body = HTML.format(
            api_base=cfg['api_base'], api_key=cfg['api_key'], gateway_name=cfg['gateway_name'],
            devices=json.dumps(devices, indent=2), message='Saved.',
        ).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def main():
    server = HTTPServer((HOST, PORT), Handler)
    print(f'Gateway config UI at http://{HOST}:{PORT}/')
    server.serve_forever()


if __name__ == '__main__':
    main()
