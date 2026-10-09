---
description: Escreve e envia (ou cria rascunho) da proposta por e-mail via Gmail
argument-hint: "[nome do cliente ou todos]"
---

Envie propostas para os leads com página publicada, seguindo a skill `proposta-email`.

## Passos

1. Leia `prospector-config.json` (assinatura e modo de envio) e `leads.md`.
2. Determine os destinatários: `$ARGUMENTS`, ou todos os leads com status `publicado` que ainda não receberam proposta. Somente leads com e-mail confirmado — para os demais, informe que a abordagem fica manual via WhatsApp (ofereça o texto adaptado).
3. Para cada cliente, escreva o e-mail seguindo a skill `proposta-email` na íntegra, usando os dados reais do lead: elogio baseado nas avaliações do Google, o defeito específico apontado na prospecção e — como ÚNICO link — a página-capa publicada (`https://[dominio]/[pastaBase]/[slug]/proposta.html`). Se a capa não foi publicada, gere e publique-a agora (template na skill `proposta-email`, upload pela skill `deploy-hostgator`) antes de criar o rascunho. NUNCA mencione preço.
4. **Checklist anti-spam (bloqueante)**: valide o e-mail contra a checklist da skill `proposta-email` (1 link, sem palavras-gatilho, sem anexo, assunto-pergunta ≤ 60 caracteres, primeira linha personalizada). Reescreva até passar em todos os itens.
5. Envio conforme o modo do config:
   - **rascunho** (padrão): crie o rascunho pelo conector do Gmail e informe que está pronto para revisão na caixa de rascunhos.
   - **enviar direto**: se o conector do Gmail não oferecer envio direto, use o Claude in Chrome no Gmail web para enviar, ou crie o rascunho e avise o usuário.
6. Registre a proposta na página **Disparos** do painel (skill `dashboard-leads`, seção "Página Disparos"): um disparo por cliente com `frente` = `sites`, `canal` = `email`, `contato` = e-mail do lead, `assunto`, `mensagem` = texto do e-mail, `script` = `proposta-email` e `origem` = `Claude Code /proposta AAAA-MM-DD` (ou o nome do chat). Com a pasta conectada, grave com `disparos_modulo.gravar_disparo`; sem pasta, salve o JSON em `entrada/` pelo conector do Google Drive. Não gere página `disparos-*.html`.
   - **rascunho:** grave o disparo sem `enviadoEm` (fica em "A enviar"). O usuário envia o rascunho pelo Gmail (não pelo botão "Abrir e-mail" da página, para não mandar duas vezes) e clica em "Marcar como enviada": isso põe o lead em `proposta` com `dataProposta`.
   - **enviar direto:** grave o disparo com `enviadoEm` = hoje e `followupEm` = hoje + 4 dias, e mude o lead para `proposta` + `dataProposta` como antes.
   Atualize também o `leads.md`.

## Saída

Resuma: quantas propostas criadas/enviadas e para quem, com o link da capa de cada uma. Lembre o usuário: `/respostas` verifica quem respondeu (dá pra agendar diário) e `/followup` cuida de quem está 3+ dias sem responder.
