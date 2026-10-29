#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rastreio de links — serviço da VPS (só Python padrão). Fica atrás do Nginx em 127.0.0.1:8790.

Rotas (o Nginx repassa /r/ para cá):
  GET  /r/r.js             script que a página carrega (injetado pelo Nginx com sub_filter)
  POST /r/a  | GET /r/a    registra um acesso: {"l": "<codigo>", "p": "/modelos/petshop/"}
  GET  /r/sync?desde=N     acessos com id > N, até 500 por vez; exige o cabeçalho X-Token

Guarda só o bruto: código, data/hora (horário de Brasília), user-agent, hash do IP e página.
Quem decide se é robô é o painel, que sabe a hora do envio.

Uso na VPS:   RASTREIO_TOKEN=... RASTREIO_SAL=... python3 rastreio_vps.py
Teste local:  python rastreio_vps.py --porta 8790 --site ./site   (serve a pasta e injeta o script,
              imitando o sub_filter do Nginx)
"""
import argparse, datetime, hashlib, hmac, json, os, re, sqlite3, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PASTA = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get('RASTREIO_DB') or os.path.join(PASTA, 'acessos.db')
TOKEN = os.environ.get('RASTREIO_TOKEN', '')
SAL = os.environ.get('RASTREIO_SAL', 'troque-este-sal')
BRT = datetime.timezone(datetime.timedelta(hours=-3))      # Brasil sem horário de verão desde 2019
RE_COD = re.compile(r'^[a-z0-9]{6,8}$')
INJETAR = '<script src="/r/r.js" defer></script></body>'
LIMITE_POR_MIN = 30                                         # por IP: freio contra abuso, não afeta uso normal

JS = r"""(function(){try{
var m=location.search.match(/[?&]l=([a-z0-9]{6,8})(?:&|$)/i);if(!m)return;
var k='rl_'+m[1];try{if(sessionStorage.getItem(k))return;sessionStorage.setItem(k,'1');}catch(e){}
var d=JSON.stringify({l:m[1].toLowerCase(),p:location.pathname});
if(navigator.sendBeacon&&navigator.sendBeacon('/r/a',new Blob([d],{type:'text/plain'})))return;
var x=new XMLHttpRequest();x.open('POST','/r/a',true);x.setRequestHeader('Content-Type','text/plain');x.send(d);
}catch(e){}})();"""

_janela = {}


def conexao():
    c = sqlite3.connect(DB, timeout=5)
    c.execute('''CREATE TABLE IF NOT EXISTS acessos(
        id INTEGER PRIMARY KEY AUTOINCREMENT, codigo TEXT NOT NULL, data_hora TEXT NOT NULL,
        user_agent TEXT, ip_hash TEXT, pagina TEXT)''')
    return c


def ip_hash(ip):
    return hmac.new(SAL.encode(), (ip or '').encode(), hashlib.sha256).hexdigest()[:16]


def limitado(ip):
    agora = time.time()
    lst = [t for t in _janela.get(ip, []) if agora - t < 60]
    lst.append(agora)
    _janela[ip] = lst
    if len(_janela) > 5000:
        _janela.clear()
    return len(lst) > LIMITE_POR_MIN


class App(BaseHTTPRequestHandler):
    site = None

    def _ip(self):
        ip = self.client_address[0]
        if ip in ('127.0.0.1', '::1'):                     # atrás do Nginx: o IP real vem no cabeçalho
            ip = self.headers.get('X-Real-IP') or ip
        return ip

    def _enviar(self, code, corpo=b'', tipo='text/plain; charset=utf-8', extra=None):
        self.send_response(code)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(corpo)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if corpo and self.command != 'HEAD':
            self.wfile.write(corpo)

    def _registrar(self, cod, pagina):
        cod = (cod or '').lower()
        if not RE_COD.match(cod):
            return self._enviar(204)
        ip = self._ip()
        if limitado(ip):
            return self._enviar(204)
        c = conexao()
        c.execute('INSERT INTO acessos(codigo, data_hora, user_agent, ip_hash, pagina) VALUES (?,?,?,?,?)',
                  (cod, datetime.datetime.now(BRT).strftime('%Y-%m-%d %H:%M:%S'),
                   (self.headers.get('User-Agent') or '')[:400], ip_hash(ip), (pagina or '')[:200]))
        c.commit(); c.close()
        return self._enviar(204, extra={'Cache-Control': 'no-store'})

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == '/r/r.js':
            return self._enviar(200, JS.encode(), 'application/javascript; charset=utf-8', {'Cache-Control': 'public, max-age=3600'})
        if u.path == '/r/a':
            return self._registrar((q.get('l') or [''])[0], (q.get('p') or [''])[0])
        if u.path == '/r/sync':
            if not TOKEN or not hmac.compare_digest(self.headers.get('X-Token', ''), TOKEN):
                return self._enviar(403, b'{"erro":"token"}', 'application/json')
            desde = int((q.get('desde') or ['0'])[0] or 0)
            c = conexao()
            rows = c.execute('SELECT id, codigo, data_hora, user_agent, ip_hash, pagina FROM acessos WHERE id>? ORDER BY id LIMIT 501',
                             (desde,)).fetchall(); c.close()
            dados = [dict(zip(('id', 'codigo', 'data_hora', 'user_agent', 'ip_hash', 'pagina'), r)) for r in rows[:500]]
            corpo = json.dumps({'acessos': dados, 'mais': len(rows) > 500}, ensure_ascii=False).encode('utf-8')
            return self._enviar(200, corpo, 'application/json; charset=utf-8', {'Cache-Control': 'no-store'})
        if self.site:                                     # só no teste local: imita o Nginx + sub_filter
            return self._estatico(u.path)
        return self._enviar(404)

    def do_POST(self):
        if urlparse(self.path).path != '/r/a':
            return self._enviar(404)
        n = min(int(self.headers.get('Content-Length', 0) or 0), 2000)
        try:
            j = json.loads(self.rfile.read(n).decode('utf-8') or '{}')
        except Exception:
            j = {}
        return self._registrar(str(j.get('l') or ''), str(j.get('p') or ''))

    def _estatico(self, caminho):
        alvo = os.path.normpath(os.path.join(self.site, caminho.lstrip('/')))
        if not alvo.startswith(os.path.normpath(self.site)):
            return self._enviar(403)
        if os.path.isdir(alvo):
            if not caminho.endswith('/'):
                return self._enviar(301, extra={'Location': caminho + '/' + ('?' + urlparse(self.path).query if urlparse(self.path).query else '')})
            alvo = os.path.join(alvo, 'index.html')
        if not os.path.isfile(alvo):
            return self._enviar(404)
        corpo = open(alvo, 'rb').read()
        tipo = 'text/html; charset=utf-8' if alvo.endswith('.html') else 'application/octet-stream'
        if alvo.endswith('.html'):
            corpo = corpo.replace(b'</body>', INJETAR.encode(), 1)
        return self._enviar(200, corpo, tipo)

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--porta', type=int, default=8790)
    ap.add_argument('--site', help='só para teste local: pasta servida como se fosse o Nginx')
    a = ap.parse_args()
    App.site = os.path.abspath(a.site) if a.site else None
    conexao().close()
    host = '127.0.0.1'
    print('rastreio em http://%s:%d  (token %s)' % (host, a.porta, 'definido' if TOKEN else 'NÃO definido: /r/sync recusa tudo'))
    ThreadingHTTPServer((host, a.porta), App).serve_forever()
