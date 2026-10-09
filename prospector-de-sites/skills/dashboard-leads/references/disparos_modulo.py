# -*- coding: utf-8 -*-
"""Disparos unificados do Prospector — módulo sem dependências (só Python padrão).

O que faz:
  - cria a tabela `disparos` no prospector.db (não altera a tabela `leads`);
  - lista os disparos (último de cada lead) com os dados do lead;
  - aplica as ações do painel (enviado, follow-up, respondeu, encerrar...) sempre
    atualizando `disparos` e `leads` na mesma transação, sem rebaixar status;
  - importa os arquivos JSON da pasta entrada/ e move para entrada/importados/.

Integração com o servidor (2 linhas, no começo de do_GET / do_POST / do_PUT):
    import disparos_modulo as dm
    if dm.tratar(self, 'GET'): return      # (ou 'POST' / 'PUT')
"""
import json, os, re, shutil, sqlite3, datetime

ORDEM = {'novo': 0, 'redesenhado': 1, 'publicado': 2, 'proposta': 3, 'respondeu': 4, 'fechado': 5}
CANAIS = {'whatsapp', 'instagram', 'email', 'messenger'}
CAMPOS_EDITAVEIS = {'mensagem', 'followupMsg', 'apresentacaoMsg', 'assunto', 'contato', 'canal', 'frente', 'followupEm'}
CAMPOS_DISPARO = ['slug', 'frente', 'canal', 'contato', 'assunto', 'mensagem', 'followupMsg', 'script',
                  'origem', 'criadoEm', 'enviadoEm', 'followupEm', 'followupEnviadoEm',
                  'respondeuEm', 'encerradoEm', 'motivo', 'apresentacaoMsg', 'apresentacaoEm']
DIAS_FOLLOWUP = 4
AVISOS = []          # avisos da última importação (o painel mostra)


def hoje():
    return datetime.date.today().isoformat()


def mais_dias(d, n):
    return (datetime.date.fromisoformat(d) + datetime.timedelta(days=n)).isoformat()


def garantir_tabela(c):
    c.execute('''CREATE TABLE IF NOT EXISTS disparos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL, frente TEXT NOT NULL, canal TEXT NOT NULL,
        contato TEXT, assunto TEXT, mensagem TEXT NOT NULL, followupMsg TEXT,
        script TEXT, origem TEXT,
        criadoEm TEXT DEFAULT (date('now','localtime')),
        enviadoEm TEXT, followupEm TEXT, followupEnviadoEm TEXT,
        respondeuEm TEXT, encerradoEm TEXT, motivo TEXT,
        atualizado TEXT DEFAULT (datetime('now','localtime')))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_disparos_slug ON disparos(slug)')
    cols = [r[1] for r in c.execute('PRAGMA table_info(disparos)')]
    for col in ('statusAntes',        # status do lead antes de encerrar (Reabrir)
                'apresentacaoMsg',    # segunda mensagem opcional, com a apresentação/modelo
                'apresentacaoEm'):
        if col not in cols:
            c.execute('ALTER TABLE disparos ADD COLUMN %s TEXT' % col)


def colunas_leads(c):
    return [r[1] for r in c.execute('PRAGMA table_info(leads)')]


def frente_do_nicho(nicho):
    n = (nicho or '').lower()
    if 'ingresso' in n:
        return 'ingressos'
    if 'decora' in n or 'festa' in n:
        return 'decoracao'
    return 'sites'


# ---------------------------------------------------------------- leitura

def listar(c):
    c.row_factory = sqlite3.Row
    cols = set(colunas_leads(c))
    extra = [k for k in ('nome', 'cidade', 'nicho', 'status', 'motivo', 'obs', 'whatsapp', 'email',
                         'siteAntigo', 'urlNova', 'dataProposta', 'nota', 'avaliacoes') if k in cols]
    sel = ', '.join('l.%s AS lead_%s' % (k, k) for k in extra)
    sql = '''SELECT d.*%s FROM disparos d LEFT JOIN leads l ON l.slug = d.slug
             WHERE d.id = (SELECT MAX(id) FROM disparos x WHERE x.slug = d.slug)
             ORDER BY d.id DESC''' % ((', ' + sel) if sel else '')
    rows = [dict(r) for r in c.execute(sql).fetchall()]
    c.row_factory = None
    return rows


# ---------------------------------------------------------------- ações

def _lead(c, slug):
    r = c.execute('SELECT status, dataProposta, obs FROM leads WHERE slug=?', (slug,)).fetchone()
    return {'status': r[0], 'dataProposta': r[1], 'obs': r[2]} if r else None


def _avancar(c, slug, novo_status, extra=None):
    """Muda o status do lead só se for avanço (nunca rebaixa). Retorna True se mudou."""
    lead = _lead(c, slug)
    if not lead:
        return False
    atual = lead['status'] or 'novo'
    if atual == 'descartado' and novo_status != 'descartado':
        return False
    if novo_status != 'descartado' and ORDEM.get(atual, 0) >= ORDEM.get(novo_status, 0):
        return False
    if novo_status == 'descartado' and atual == 'fechado':
        return False
    sets, vals = ['status=?'], [novo_status]
    for k, v in (extra or {}).items():
        sets.append('%s=?' % k); vals.append(v)
    c.execute('UPDATE leads SET %s, atualizado=datetime("now","localtime") WHERE slug=?' % ','.join(sets),
              vals + [slug])
    return True


def _anotar(c, slug, texto):
    lead = _lead(c, slug)
    if lead is None:
        return
    obs = (lead['obs'] or '').strip()
    obs = (obs + ' | ' if obs else '') + texto
    c.execute('UPDATE leads SET obs=? WHERE slug=?', (obs, slug))


def acao(c, id_, nome, corpo=None):
    """Aplica uma ação do painel. Devolve o 'antes' para o Desfazer."""
    corpo = corpo or {}
    c.row_factory = sqlite3.Row
    d = c.execute('SELECT * FROM disparos WHERE id=?', (id_,)).fetchone()
    c.row_factory = None
    if not d:
        raise ValueError('disparo %s não existe' % id_)
    d = dict(d)
    antes = {'disparo': d, 'lead': _lead(c, d['slug'])}
    h = corpo.get('hoje') or hoje()
    upd = {}
    if nome == 'enviado':
        upd = {'enviadoEm': h, 'followupEm': mais_dias(h, DIAS_FOLLOWUP)}
        lead = _lead(c, d['slug'])
        extra = {} if (lead and lead['dataProposta']) else {'dataProposta': h}
        _avancar(c, d['slug'], 'proposta', extra)
        _anotar(c, d['slug'], '%s enviado %s; follow-up %s' % (d['canal'], h[8:] + '/' + h[5:7],
                upd['followupEm'][8:] + '/' + upd['followupEm'][5:7]))
    elif nome == 'fupEnviado':
        upd = {'followupEnviadoEm': h}
    elif nome == 'apresentacao':       # segunda mensagem opcional, depois do primeiro contato
        upd = {'apresentacaoEm': h}
        if corpo.get('texto'):
            upd['apresentacaoMsg'] = corpo['texto']
        _anotar(c, d['slug'], 'apresentação enviada %s' % (h[8:] + '/' + h[5:7]))
    elif nome == 'respondeu':
        upd = {'respondeuEm': h}
        _avancar(c, d['slug'], 'respondeu')
    elif nome in ('encerrar', 'descartar'):
        motivo = corpo.get('motivo') or ('Sem resposta depois do follow-up.' if nome == 'encerrar' else 'Descartado.')
        upd = {'encerradoEm': h, 'motivo': motivo}
        lead = _lead(c, d['slug'])
        if _avancar(c, d['slug'], 'descartado'):
            if lead['status'] != 'descartado':
                upd['statusAntes'] = lead['status'] or 'novo'
            _anotar(c, d['slug'], 'Encerrado %s: %s' % (h, motivo))
    elif nome == 'reabrir':            # Reabrir é ação explícita: devolve o lead ao status de antes do encerramento
        upd = {'encerradoEm': None, 'motivo': None, 'statusAntes': None}
        lead = _lead(c, d['slug'])
        if lead and lead['status'] == 'descartado':
            volta = (d.get('statusAntes') if d.get('statusAntes') not in (None, '', 'descartado') else None) or ('respondeu' if d.get('respondeuEm') else
                                             'proposta' if d.get('enviadoEm') else 'novo')
            c.execute('UPDATE leads SET status=?, atualizado=datetime("now","localtime") WHERE slug=?',
                      (volta, d['slug']))
            _anotar(c, d['slug'], 'Reaberto %s (volta para %s)' % (h, volta))
    elif nome == 'restaurar':          # Desfazer: volta o disparo e o lead ao estado anterior
        ant = corpo.get('antes') or {}
        dd = ant.get('disparo') or {}
        upd = {k: dd.get(k) for k in CAMPOS_DISPARO + ['statusAntes'] if k in dd and k != 'slug'}
        ld = ant.get('lead')
        if ld:
            c.execute('UPDATE leads SET status=?, dataProposta=?, obs=? WHERE slug=?',
                      (ld.get('status'), ld.get('dataProposta'), ld.get('obs'), d['slug']))
    else:
        raise ValueError('ação desconhecida: %s' % nome)
    if upd:
        c.execute('UPDATE disparos SET %s, atualizado=datetime("now","localtime") WHERE id=?' %
                  ','.join('%s=?' % k for k in upd), list(upd.values()) + [id_])
    return antes


def editar(c, id_, campos):
    sets = {k: v for k, v in campos.items() if k in CAMPOS_EDITAVEIS}
    if 'canal' in sets and sets['canal'] not in CANAIS:
        raise ValueError('canal inválido')
    if sets:
        c.execute('UPDATE disparos SET %s, atualizado=datetime("now","localtime") WHERE id=?' %
                  ','.join('%s=?' % k for k in sets), list(sets.values()) + [id_])


# ---------------------------------------------------------------- importação

def gravar_disparo(c, dsp, origem):
    """Insere um disparo se não houver um igual (mesmo slug + canal + mensagem)."""
    existe = c.execute('SELECT 1 FROM disparos WHERE slug=? AND canal=? AND mensagem=?',
                       (dsp['slug'], dsp['canal'], dsp['mensagem'])).fetchone()
    if existe:
        return False
    campos = {k: dsp.get(k) for k in CAMPOS_DISPARO if dsp.get(k) not in (None, '')}
    campos.setdefault('origem', origem)
    c.execute('INSERT INTO disparos (%s) VALUES (%s)' % (','.join(campos), ','.join('?' * len(campos))),
              list(campos.values()))
    return True


def gravar_lead(c, lead):
    """Upsert sem rebaixar status e sem apagar o que já está preenchido."""
    cols = colunas_leads(c)
    if not cols:
        raise ValueError('o banco não tem a tabela leads')
    dados = {k: v for k, v in lead.items() if k in cols and v not in (None, '')}
    if not dados.get('slug'):
        raise ValueError('lead sem slug')
    atual = c.execute('SELECT * FROM leads WHERE slug=?', (dados['slug'],)).fetchone()
    if not atual:
        dados.setdefault('status', 'novo')
        c.execute('INSERT INTO leads (%s) VALUES (%s)' % (','.join(dados), ','.join('?' * len(dados))),
                  list(dados.values()))
        return 'novo'
    atual = dict(zip(cols, atual))
    vazios = {k: v for k, v in dados.items() if k not in ('slug', 'status') and atual.get(k) in (None, '')}
    if vazios:
        c.execute('UPDATE leads SET %s WHERE slug=?' % ','.join('%s=?' % k for k in vazios),
                  list(vazios.values()) + [dados['slug']])
    return 'existente'


def validar(doc):
    if not isinstance(doc, dict):
        return 'o arquivo não é um objeto JSON'
    if not isinstance(doc.get('disparos', []), list) or not isinstance(doc.get('leads', []), list):
        return '"leads" e "disparos" precisam ser listas'
    for i, d in enumerate(doc.get('disparos', [])):
        for k in ('slug', 'canal', 'mensagem'):
            if not d.get(k):
                return 'disparo %d sem "%s"' % (i + 1, k)
        if d['canal'] not in CANAIS:
            return 'disparo %d com canal inválido: %s' % (i + 1, d['canal'])
    return None


def importar_entrada(c, pasta_entrada):
    """Importa entrada/*.json. Arquivo inválido fica na pasta e gera aviso."""
    global AVISOS
    AVISOS = []
    resumo = {'arquivos': 0, 'leadsNovos': 0, 'disparosNovos': 0, 'repetidos': 0}
    if not os.path.isdir(pasta_entrada):
        return resumo
    destino = os.path.join(pasta_entrada, 'importados')
    os.makedirs(destino, exist_ok=True)
    for nome in sorted(os.listdir(pasta_entrada)):
        caminho = os.path.join(pasta_entrada, nome)
        if not (nome.lower().endswith('.json') and os.path.isfile(caminho)):
            continue
        try:
            doc = json.load(open(caminho, encoding='utf-8'))
        except Exception as e:
            AVISOS.append('%s: JSON inválido (%s)' % (nome, e)); continue
        erro = validar(doc)
        if erro:
            AVISOS.append('%s: %s' % (nome, erro)); continue
        origem = doc.get('origem') or ('Importado: ' + nome)
        try:
            slugs_leads = set()
            for l in doc.get('leads', []):
                if gravar_lead(c, l) == 'novo':
                    resumo['leadsNovos'] += 1
                slugs_leads.add(l.get('slug'))
            nichos = dict(c.execute('SELECT slug, nicho FROM leads').fetchall())
            for d in doc.get('disparos', []):
                if d['slug'] not in nichos:
                    raise ValueError('disparo para lead que não existe: %s' % d['slug'])
                d = dict(d)
                d.setdefault('frente', frente_do_nicho(nichos.get(d['slug'])))
                if gravar_disparo(c, d, origem):
                    resumo['disparosNovos'] += 1
                else:
                    resumo['repetidos'] += 1
            c.commit()
        except Exception as e:
            c.rollback()
            AVISOS.append('%s: não importado (%s)' % (nome, e)); continue
        alvo = os.path.join(destino, nome)
        if os.path.exists(alvo):
            base, ext = os.path.splitext(nome)
            alvo = os.path.join(destino, '%s-%s%s' % (base, datetime.datetime.now().strftime('%H%M%S'), ext))
        shutil.move(caminho, alvo)
        resumo['arquivos'] += 1
    return resumo


# ---------------------------------------------------------------- rotas HTTP

def _caminhos(handler):
    mod = __import__('sys').modules.get(type(handler).__module__)
    pasta = getattr(mod, 'PASTA', os.path.dirname(os.path.abspath(__file__)))
    db = getattr(mod, 'DB', os.path.join(pasta, 'prospector.db'))
    return pasta, db


def tratar(handler, metodo):
    """Responde às rotas /api/disparos*. Devolve True se a rota era dele."""
    caminho = handler.path.split('?')[0].rstrip('/')
    if not caminho.startswith('/api/disparos'):
        return False
    pasta, db = _caminhos(handler)
    c = sqlite3.connect(db)
    try:
        garantir_tabela(c)
        partes = caminho.split('/')            # ['', 'api', 'disparos', id, acao]
        if metodo == 'GET' and len(partes) == 3:
            resumo = None
            if 'importar=1' in handler.path:
                resumo = importar_entrada(c, os.path.join(pasta, 'entrada'))
            handler._json(200, {'hoje': hoje(), 'disparos': listar(c), 'avisos': AVISOS, 'importacao': resumo})
            return True
        if metodo == 'POST' and len(partes) == 5 and partes[3].isdigit():
            antes = acao(c, int(partes[3]), partes[4], handler._corpo())
            c.commit()
            handler._json(200, {'ok': True, 'antes': antes})
            return True
        if metodo == 'PUT' and len(partes) == 4 and partes[3].isdigit():
            editar(c, int(partes[3]), handler._corpo())
            c.commit()
            handler._json(200, {'ok': True})
            return True
        handler._json(404, {'erro': 'rota'})
        return True
    except ValueError as e:
        c.rollback(); handler._json(400, {'erro': str(e)}); return True
    except Exception as e:
        c.rollback(); handler._json(500, {'erro': str(e)}); return True
    finally:
        c.close()
