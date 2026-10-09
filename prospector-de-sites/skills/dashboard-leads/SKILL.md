---
name: dashboard-leads
description: Esta skill deve ser usada para criar e ATUALIZAR o dashboard de leads — o painel de controle local (SQLite + página web) onde o usuário administra prospecções, sites, publicações e propostas. Acione sempre que qualquer comando do plugin mudar dados de leads (/prospectar, /redesenhar, /publicar, /proposta), ou quando o usuário disser "dashboard", "painel", "meus leads", "controle de clientes", "banco de dados de leads".
---

# Dashboard de leads (SQLite + página local)

Arquitetura na RAIZ da pasta conectada:

- **`prospector.db`** — banco SQLite, a FONTE DA VERDADE dos leads.
- **`dashboard-server.py` + `iniciar-dashboard.bat`** — mini-servidor local (Python padrão, sem dependências). O usuário dá duplo clique no .bat → abre `http://localhost:8765` com o painel completo: editar, excluir e arrastar cards salvam direto no banco.
- **`disparos_modulo.py` + `disparos.html`** — a página **Disparos** (`http://localhost:8765/disparos.html`, item "Disparos" na lateral): todas as mensagens de prospecção (sites, ingressos, decoração…) numa tabela `disparos` do banco, em filas (A enviar, Follow-up hoje, Aguardando, Responderam, Encerrados). O servidor atende as rotas `/api/disparos*` pelo módulo e, ao iniciar, importa os JSON da pasta `entrada/`.
- **`dashboard.html`** — a página do painel (gerada do template). Servida pelo servidor (modo banco) ou aberta por duplo clique (modo arquivo: só leitura + edições presas ao navegador). O badge no topo indica o modo.

## Setup (uma vez, no /setup ou no primeiro uso)

1. Copie `references/dashboard-server.py`, `references/iniciar-dashboard.bat`, `references/disparos_modulo.py` e `references/disparos.html` desta skill para a raiz da pasta conectada, e crie a pasta `entrada/` (com `entrada/importados/`). Se o usuário já tem um `dashboard-server.py` ou `dashboard.html` personalizado, **não sobrescreva**: acrescente só as linhas do Disparos (veja "Página Disparos" abaixo).
2. Crie o `prospector.db` com o schema abaixo (via python3/sqlite3 no bash).
3. Gere o `dashboard.html` a partir de `references/dashboard-template.html` substituindo `__DADOS__` pelo snapshot JSON.
4. Diga ao usuário: "duplo clique em `iniciar-dashboard.bat` abre o painel com o banco conectado" (requer Python instalado no Windows — se não tiver, o dashboard.html funciona no modo arquivo).

## Schema do banco

```sql
CREATE TABLE IF NOT EXISTS leads(
  slug TEXT PRIMARY KEY, nome TEXT, nicho TEXT, cidade TEXT, nota REAL, avaliacoes INTEGER,
  email TEXT, telefone TEXT, whatsapp TEXT, siteAntigo TEXT, motivo TEXT,
  status TEXT DEFAULT 'novo', urlNova TEXT, dataProposta TEXT, valor REAL, obs TEXT,
  contratoStatus TEXT DEFAULT 'pendente', contratoEm TEXT, manutencao REAL, pago INTEGER DEFAULT 0,
  docCliente TEXT, endCliente TEXT,
  atualizado TEXT DEFAULT (datetime('now','localtime')));
```

Status: `novo | redesenhado | publicado | proposta | respondeu | fechado | descartado`. `slug` é a chave.

## Como os comandos atualizam (SEMPRE os 2 passos)

1. **Upsert no banco** via bash (exemplo):
```bash
python3 - <<'EOF'
import sqlite3
c = sqlite3.connect('CAMINHO/prospector.db')
c.execute("INSERT INTO leads (slug,nome,status,...) VALUES (?,?,?,...) ON CONFLICT(slug) DO UPDATE SET status=excluded.status, atualizado=datetime('now','localtime')", (...))
c.commit()
EOF
```
   - `/prospectar` → insere leads (`novo`) e descartados (`descartado`, motivo em `obs`). NUNCA sobrescreva um lead cujo status já avançou.
   - `/redesenhar` → `status='redesenhado'` · `/publicar` → `status='publicado'`, `urlNova` · `/proposta` → `status='proposta'`, `dataProposta`.
   - Usuário conta que respondeu/fechou → `status='respondeu'|'fechado'`, `valor` (+ `manutencao` se houver mensalidade).
   - `/contrato` → `contratoStatus='enviado'` + `contratoEm`. Cliente assinou → `contratoStatus='assinado'`. Pagamento recebido → `pago=1`.
2. **Regenerar o snapshot**: leia todos os leads do banco e regrave `dashboard.html` do template com o JSON embutido atualizado (`{"atualizado": "...", "leads": [...]}`) — é o fallback para quem abre sem servidor.

Se o banco não existir ainda (usuário antigo), crie-o e importe os leads do snapshot embutido no `dashboard.html` atual antes do upsert. Respeite edições do usuário: antes de regravar um lead, leia o registro atual do banco.

## Página Disparos (mensagens de prospecção)

Cada mensagem preparada por um comando ou skill vira um registro em `disparos` (`slug`, `frente` = `sites`/`ingressos`/`decoracao`, `canal` = `whatsapp`/`instagram`/`email`/`messenger`, `contato`, `assunto`, `mensagem`, `followupMsg`, `script`, `origem`, datas de envio, follow-up, resposta e encerramento). A tabela é criada pelo próprio módulo (`garantir_tabela`).

- **Gravar (pasta conectada):** `import disparos_modulo as dm` (da raiz da pasta) e use `dm.gravar_lead(c, lead)` e `dm.gravar_disparo(c, disparo, origem)`, com `origem` = `Claude Code /<comando> AAAA-MM-DD` ou o nome do chat. Lead novo entra; lead existente só ganha campos vazios e nunca muda de status; disparo repetido (mesmo slug + canal + mensagem) não duplica.
- **Gravar (chat sem pasta):** salve `disparos-AAAA-MM-DD-<nicho>.json` (`{"origem", "leads": [...], "disparos": [...]}`) em `entrada/` pelo conector do Google Drive; o painel importa ao abrir ou no botão "Atualizar e importar entrada".
- **Não gere mais páginas `disparos-*.html`.** O envio continua manual: o usuário envia pelo botão do canal e marca na página ("Marcar como enviada" → lead `proposta` + `dataProposta`, follow-up em 4 dias; "Respondeu"; "Recusou/Descartar"; "Reabrir"; "Desfazer").
- **Em painel personalizado:** no `dashboard-server.py`, `import disparos_modulo as dm` no topo e `if dm.tratar(self, 'GET'): return` (e `'POST'`, `'PUT'`) no começo de `do_GET`, `do_POST` e `do_PUT`; no `__main__`, depois de abrir a conexão, `dm.garantir_tabela(c)` e `dm.importar_entrada(c, os.path.join(PASTA, 'entrada'))`. No `dashboard.html`, um item "Disparos" na lateral que abre `/disparos.html`. O `references/dashboard-server.py` e o template já vêm assim.

## O que o painel faz sozinho (não reimplementar)

Kanban drag & drop, edição em modal, exclusão, busca, paginação automática, funil, follow-ups (proposta 4+ dias), receita fechada/potencial, vista Contratos (status pendente/enviado/assinado + link do documento + pago) e vista Financeiro (recebido, a receber, MRR de manutenções, projeção 12 meses) — tudo no template. O plugin só mantém o BANCO correto e o snapshot em dia.
