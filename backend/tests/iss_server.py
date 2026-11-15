"""CI-only recorded ISS HTTP service. No dependency on app or provider network."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path)
        fixtures = [json.loads(p.read_text(encoding='utf-8')) for p in Path('/fixtures').glob('*.json')]
        result = None
        if path.path == '/health':
            result = {'status': 'ok'}
        for data in fixtures:
            block = data['description']['description']
            values = {row[0]: row[block['columns'].index('value')] for row in block['data']}
            isin = values['ISIN']
            if path.path == '/iss/securities/' + isin + '.json':
                result = data['description']
            elif path.path == '/iss/securities/' + isin + '/bondization.json':
                result = data['bondization']
            elif path.path == '/iss/emitters/' + values.get('EMITTER_ID', '') + '.json':
                result = data['emitter']
            elif path.path == '/iss/securities.json' and parse_qs(path.query).get('q') == [isin]:
                result = data.get('search', {})
        self.send_response(200 if result is not None else 404)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(result or {}, ensure_ascii=False).encode('utf-8'))

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8000), Handler).serve_forever()
