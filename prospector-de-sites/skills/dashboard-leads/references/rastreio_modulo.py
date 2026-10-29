# -*- coding: utf-8 -*-
"""Rastreio de links dos disparos — módulo sem dependências (só Python padrão).

O que faz:
  - acrescenta à tabela `disparos` as colunas codigo_rastreio, enviado_em, primeira_abertura_em,
    respondeu_em e total_aberturas, e cria a tabela `acessos_link`;
  - gera o código de rastreio e marca os links da App Routine Tech com ?l=<codigo>;
  - busca na VPS os acessos novos (/r/sync, com token) e grava em acessos_link;
  - classifica cada acesso como bot ou pessoa e recalcula primeira abertura e total;
  - calcula as métricas da aba Métricas.

A VPS não conhece o envio: ela só guarda o acesso bruto. A regra dos 15 s depois do envio
é aplicada aqui, e é recalculada sempre que o Solon marca (ou desfaz) o "Enviei".

Integração com o servidor: o disparos_modulo chama `garantir_tabelas` e `recalcular`, e
`tratar(handler, metodo)` responde às rotas /api/rastreio*.
"""
import datetime, json, os, re, secrets, sqlite3, urllib.request

ALFABETO = 'abcdefghjkmnpqrstuvwxyz23456789'     # sem 0/o, 1/l/i: dá para ler e digitar
TAM_CODIGO = 7
JANELA_BOT_S = 15                                # acesso até 15 s depois do envio = pré-visualização
HORAS_SEM_ABERTURA = 72
DOMINIO = 'approutinetech.com.br'
RE_LINK_ART = re.compile(r'https?://(?:www\.)?approutinetech\.com\.br/(?:modelos|clientes)/[^\s<>"\')\]]*', re.I)

# Robôs de pré-visualização e ferramentas. "Instagram" sozinho NÃO entra: o navegador interno do
# Instagram (quem toca no link dentro da DM) manda "... Instagram 300.0 ..." no user-agent e é
# uma pessoa. A pré-visualização da DM do Instagram vem como facebookexternalhit.
RE_BOT = re.compile(r'whatsapp|facebookexternalhit|facebot|meta-externalagent|bot\b|bot/|crawler|spider|'
                    r'curl|wget|python-requests|python-urllib|go-http|headless|preview|slurp|skypeuripreview|'
                    r'telegrambot|discordbot|linkedinbot|embedly', re.I)
RE_INSTAGRAM = re.compile(r'instagram', re.I)


def agora():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def garantir_tabelas(c):
    cols = [r[1] for r in c.execute('PRAGMA table_info(disparos)')]
    for col, tipo in (('codigo_rastreio', 'TEXT'), ('enviado_em', 'TEXT'), ('primeira_abertura_em', 'TEXT'),
                      ('respondeu_em', 'TEXT'), ('total_aberturas', 'INTEGER DEFAULT 0')):
        if col not in cols:
            c.execute('ALTER TABLE disparos ADD COLUMN %s %s' % (col, tipo))
    # SQLite não deixa ADD COLUMN ... UNIQUE: a unicidade vem de um índice único (vários NULL são permitidos)
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_disparos_codigo ON disparos(codigo_rastreio)')
    c.execute('''CREATE TABLE IF NOT EXISTS acessos_link(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT NOT NULL, data_hora TEXT NOT NULL, user_agent TEXT, ip_hash TEXT,
        bot BOOLEAN NOT NULL DEFAULT 0, motivo_bot TEXT, pagina TEXT,
        id_vps INTEGER UNIQUE)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_acessos_codigo ON acessos_link(codigo)')
    c.execute('CREATE TABLE IF NOT EXISTS rastreio_estado(chave TEXT PRIMARY KEY, valor TEXT)')


# ---------------------------------------------------------------- código e link

def novo_codigo(c=None):
    while True:
        cod = ''.join(secrets.choice(ALFABETO) for _ in range(TAM_CODIGO))
        if c is None or not c.execute('SELECT 1 FROM disparos WHERE codigo_rastreio=?', (cod,)).fetchone():
            return cod


def marcar_links(texto, codigo):
    """Põe ?l=<codigo> em cada link de /modelos/ ou /clientes/ que ainda não tem l=. Outros links ficam iguais."""
    if not texto or not codigo:
        return texto
    def troca(m):
        url = m.group(0)
        fim = re.search(r'[.,;:!?]+$', url)              # pontuação colada no fim da frase não é do link
        pont = fim.group(0) if fim else ''
        url = url[:len(url) - len(pont)]
        if re.search(r'[?&]l=', url):
            return url + pont
        return url + ('&' if '?' in url else '?') + 'l=' + codigo + pont
    return RE_LINK_ART.sub(troca, texto)


def sem_codigo(texto):
    """Texto sem o ?l=<codigo> (para achar mensagem repetida na importação)."""
    return re.sub(r'[?&]l=[a-z0-9]{6,8}\b', '', texto or '')


def preparar_pendentes(c):
    """Disparos ainda não enviados, sem código e com link da App Routine Tech ganham código e ?l= nos textos."""
    n = 0
    for id_, msg, fup, apr in c.execute('''SELECT id, mensagem, followupMsg, apresentacaoMsg FROM disparos
            WHERE enviadoEm IS NULL AND encerradoEm IS NULL AND codigo_rastreio IS NULL''').fetchall():
        if not RE_LINK_ART.search(' '.join(t or '' for t in (msg, fup, apr))):
            continue
        cod = novo_codigo(c)
        c.execute('UPDATE disparos SET codigo_rastreio=?, mensagem=?, followupMsg=?, apresentacaoMsg=? WHERE id=?',
                  (cod, marcar_links(msg, cod), marcar_links(fup, cod), marcar_links(apr, cod), id_))
        n += 1
    return n


# ---------------------------------------------------------------- classificação

def classificar(ua, data_hora, enviado_em):
    """(bot, motivo). Sem envio marcado, só o user-agent decide; o tempo entra quando houver enviado_em."""
    ua = ua or ''
    if not ua.strip():
        return True, 'sem user-agent'
    m = RE_BOT.search(ua)
    if m:
        return True, 'user-agent: ' + m.group(0)
    if RE_INSTAGRAM.search(ua) and 'mozilla/' not in ua.lower():
        return True, 'user-agent: Instagram (não é navegador)'
    if enviado_em and data_hora:
        try:
            dt = datetime.datetime.fromisoformat(data_hora)
            env = datetime.datetime.fromisoformat(enviado_em)
            if dt <= env + datetime.timedelta(seconds=JANELA_BOT_S):
                return True, 'até %ds depois do envio' % JANELA_BOT_S if dt >= env else 'antes do envio'
        except ValueError:
            pass
    return False, None


def recalcular(c, codigos=None):
    """Reclassifica os acessos e atualiza primeira_abertura_em / total_aberturas dos disparos."""
    if codigos is None:
        codigos = [r[0] for r in c.execute('SELECT codigo_rastreio FROM disparos WHERE codigo_rastreio IS NOT NULL')]
    for cod in codigos:
        r = c.execute('SELECT id, enviado_em FROM disparos WHERE codigo_rastreio=?', (cod,)).fetchone()
        if not r:
            continue
        id_, env = r
        for aid, ua, dh in c.execute('SELECT id, user_agent, data_hora FROM acessos_link WHERE codigo=?', (cod,)).fetchall():
            bot, motivo = classificar(ua, dh, env)
            c.execute('UPDATE acessos_link SET bot=?, motivo_bot=? WHERE id=?', (1 if bot else 0, motivo, aid))
        # sem "Enviei" marcado não há abertura: o lead ainda não recebeu
        prim, tot = c.execute('SELECT MIN(data_hora), COUNT(*) FROM acessos_link WHERE codigo=? AND bot=0', (cod,)).fetchone() if env else (None, 0)
        c.execute('UPDATE disparos SET primeira_abertura_em=?, total_aberturas=? WHERE id=?', (prim, tot or 0, id_))


# ---------------------------------------------------------------- sincronização com a VPS

def config_rastreio(pasta):
    try:
        cfg = json.load(open(os.path.join(pasta, 'prospector-config.json'), encoding='utf-8'))
    except Exception:
        cfg = {}
    r = cfg.get('rastreio') or {}
    url = os.environ.get('RASTREIO_URL') or r.get('url') or ''
    token = os.environ.get('RASTREIO_TOKEN') or r.get('token') or ''
    return url.rstrip('/'), token


def gravar_acessos(c, linhas):
    novos = 0
    conhecidos = {r[0] for r in c.execute('SELECT codigo_rastreio FROM disparos WHERE codigo_rastreio IS NOT NULL')}
    tocados = set()
    for a in linhas:
        cod = str(a.get('codigo') or '').lower()
        if not re.fullmatch(r'[a-z0-9]{6,8}', cod):
            continue
        cur = c.execute('''INSERT OR IGNORE INTO acessos_link(codigo, data_hora, user_agent, ip_hash, pagina, id_vps)
                           VALUES (?,?,?,?,?,?)''', (cod, a.get('data_hora'), (a.get('user_agent') or '')[:400],
                                                     a.get('ip_hash'), (a.get('pagina') or '')[:200], a.get('id')))
        if cur.rowcount:
            novos += 1
            if cod in conhecidos:
                tocados.add(cod)
    recalcular(c, tocados)
    return novos


def sincronizar(c, pasta, timeout=6):
    """Puxa da VPS os acessos com id maior que o último visto. Falha de rede vira aviso, não erro."""
    url, token = config_rastreio(pasta)
    if not url:
        return {'ok': False, 'aviso': 'rastreio sem URL configurada (prospector-config.json → "rastreio")'}
    ult = c.execute("SELECT valor FROM rastreio_estado WHERE chave='ultimo_id_vps'").fetchone()
    desde = int(ult[0]) if ult else 0
    total = 0
    try:
        for _ in range(50):                               # até 50 páginas de 500 por sincronização
            req = urllib.request.Request('%s/sync?desde=%d' % (url, desde), headers={'X-Token': token})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                j = json.loads(resp.read().decode('utf-8'))
            linhas = j.get('acessos') or []
            total += gravar_acessos(c, linhas)
            if linhas:
                desde = max(int(a['id']) for a in linhas)
                c.execute("INSERT OR REPLACE INTO rastreio_estado VALUES ('ultimo_id_vps', ?)", (str(desde),))
            c.execute("INSERT OR REPLACE INTO rastreio_estado VALUES ('ultima_sync', ?)", (agora(),))
            c.commit()
            if not j.get('mais'):
                break
    except Exception as e:
        c.commit()
        return {'ok': False, 'novos': total, 'aviso': 'não deu para falar com a VPS (%s)' % e}
    return {'ok': True, 'novos': total}


# ---------------------------------------------------------------- métricas

def _min(a, b):
    try:
        return (datetime.datetime.fromisoformat(b) - datetime.datetime.fromisoformat(a)).total_seconds() / 60
    except (TypeError, ValueError):
        return None


def metricas(c):
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute('''SELECT d.id, d.slug, d.canal, d.frente, d.codigo_rastreio, d.enviado_em, d.enviadoEm,
            d.primeira_abertura_em, d.total_aberturas, d.respondeu_em, d.respondeuEm, d.encerradoEm,
            d.followupEnviadoEm, l.nome AS lead_nome, l.status AS lead_status
            FROM disparos d LEFT JOIN leads l ON l.slug = d.slug
            WHERE d.id = (SELECT MAX(id) FROM disparos x WHERE x.slug = d.slug)''').fetchall()]
    c.row_factory = None
    agora_dt = datetime.datetime.now()
    por_canal, calor, fila = {}, {}, []
    for d in rows:
        if not (d['enviado_em'] or d['enviadoEm']):
            continue
        k = d['canal']
        m = por_canal.setdefault(k, {'enviados': 0, 'rastreados': 0, 'abertos': 0, 'responderam': 0,
                                     'min_abertura': [], 'min_resposta': []})
        m['enviados'] += 1
        resp = bool(d['respondeu_em'] or d['respondeuEm'] or d['lead_status'] in ('respondeu', 'fechado'))
        m['responderam'] += resp
        if d['codigo_rastreio'] and d['enviado_em']:
            m['rastreados'] += 1
            if d['primeira_abertura_em']:
                m['abertos'] += 1
                t = _min(d['enviado_em'], d['primeira_abertura_em'])
                if t is not None:
                    m['min_abertura'].append(t)
            if d['respondeu_em']:
                t = _min(d['enviado_em'], d['respondeu_em'])
                if t is not None and t >= 0:
                    m['min_resposta'].append(t)
            # mapa de calor: hora × dia da semana do envio (só envios com hora exata)
            dt = datetime.datetime.fromisoformat(d['enviado_em'])
            cel = calor.setdefault('%d-%d' % (dt.weekday(), dt.hour), {'enviados': 0, 'abertos': 0, 'responderam': 0})
            cel['enviados'] += 1
            cel['abertos'] += 1 if d['primeira_abertura_em'] else 0
            cel['responderam'] += 1 if resp else 0
            # fila de follow-up: rastreado, enviado há mais de 72 h, nunca aberto, sem resposta, não encerrado
            horas = (agora_dt - dt).total_seconds() / 3600
            if horas >= HORAS_SEM_ABERTURA and not d['primeira_abertura_em'] and not resp and not d['encerradoEm'] \
                    and d['lead_status'] != 'descartado':
                fila.append({'id': d['id'], 'nome': d['lead_nome'] or d['slug'], 'canal': k, 'enviado_em': d['enviado_em'],
                             'horas': round(horas), 'followupEnviadoEm': d['followupEnviadoEm']})
    media = lambda v: round(sum(v) / len(v), 1) if v else None
    mediana = lambda v: round(sorted(v)[len(v) // 2], 1) if v else None
    canais = {}
    for k, m in por_canal.items():
        canais[k] = {'enviados': m['enviados'], 'rastreados': m['rastreados'], 'abertos': m['abertos'],
                     'responderam': m['responderam'],
                     'taxa_abertura': round(100 * m['abertos'] / m['rastreados'], 1) if m['rastreados'] else None,
                     'taxa_resposta': round(100 * m['responderam'] / m['enviados'], 1) if m['enviados'] else None,
                     'media_min_abertura': media(m['min_abertura']), 'mediana_min_abertura': mediana(m['min_abertura']),
                     'media_min_resposta': media(m['min_resposta']), 'mediana_min_resposta': mediana(m['min_resposta'])}
    fila.sort(key=lambda x: -x['horas'])
    ult = c.execute("SELECT valor FROM rastreio_estado WHERE chave='ultima_sync'").fetchone()
    bots = c.execute('SELECT COUNT(*), SUM(bot) FROM acessos_link').fetchone()
    todos = [t for m in por_canal.values() for t in m['min_abertura']]
    return {'canais': canais, 'calor': calor, 'sem_abertura': fila, 'ultima_sync': ult[0] if ult else None,
            'geral_mediana_min_abertura': mediana(todos),
            'acessos': {'total': bots[0] or 0, 'bots': bots[1] or 0}}


def acessos_do(c, id_):
    r = c.execute('SELECT codigo_rastreio FROM disparos WHERE id=?', (id_,)).fetchone()
    if not r or not r[0]:
        return []
    return [{'data_hora': a, 'user_agent': b, 'bot': bool(x), 'motivo_bot': m, 'pagina': p} for a, b, x, m, p in
            c.execute('SELECT data_hora, user_agent, bot, motivo_bot, pagina FROM acessos_link WHERE codigo=? ORDER BY data_hora', (r[0],))]


# ---------------------------------------------------------------- rotas HTTP

def tratar(handler, metodo, pasta, db):
    caminho = handler.path.split('?')[0].rstrip('/')
    if not caminho.startswith('/api/rastreio'):
        return False
    c = sqlite3.connect(db)
    try:
        garantir_tabelas(c)
        partes = caminho.split('/')                  # ['', 'api', 'rastreio', ...]
        if metodo == 'POST' and partes[3:] == ['sincronizar']:
            handler._json(200, sincronizar(c, pasta)); return True
        if metodo == 'GET' and partes[3:] == ['metricas']:
            handler._json(200, metricas(c)); return True
        if metodo == 'GET' and len(partes) == 5 and partes[3] == 'acessos' and partes[4].isdigit():
            handler._json(200, {'acessos': acessos_do(c, int(partes[4]))}); return True
        handler._json(404, {'erro': 'rota'}); return True
    except Exception as e:
        c.rollback(); handler._json(500, {'erro': str(e)}); return True
    finally:
        c.close()
