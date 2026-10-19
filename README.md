# N1 Proativo — sugestões para chamados Lecom (MVP)

API em Python/FastAPI que recebe o conteúdo de um chamado **antes** de ele ser aberto, busca chamados parecidos no histórico (RAG com ChromaDB + OpenAI) e devolve:

- **classificação**: produto, tipo de chamado, área responsável (N1/N2/N3) e criticidade sugeridos;
- **dicas** para o cliente, só quando existe chamado parecido com resposta do analista registrada;
- **possível duplicado** (mesmo cliente com chamado parecido em andamento);
- **resumo para o N1** e **perguntas** sobre informações que faltam no chamado.

Inclui uma tela HTML de testes em `/` para validar a qualidade das sugestões antes de plugar no processo do Lecom.

## Requisitos

- Python 3.11+ (testado no 3.14)
- Chave da OpenAI (`OPENAI_API_KEY`)
- Exports do Lecom do processo "Abertura de Chamados" em `data/exports/` (ver formatos abaixo)

## Fonte de dados

**A base é a raspagem do workspace Lecom** (`data/raw/*.json`), e só ela é indexada por
padrão. Cada chamado traz os campos do formulário e o diálogo completo, com autor, data e
etapa de cada mensagem. A lista de números de chamado a raspar vem de uma planilha exportada,
mas dela só se aproveita a coluna `#Processo`.

As planilhas antigas continuam suportadas para quem precisar, com `--com-planilhas`, mas
ficam fora por padrão: os campos delas são despadronizados, o mesmo campo muda de dono
conforme a etapa e não há data por mensagem.

<details>
<summary>Formatos de planilha aceitos (uso opcional)</summary>

| Formato | Abas | Id do documento |
|---|---|---|
| **Relatório** do Lecom | `Principal` | nº do processo Lecom (`14561`) |
| **Histórico do banco do formulário** | `Todas as etapas`, `Ultima etapa por chamado` | `COD_PROCESSO_F` com prefixo (`h8359`, exibido como `H8359`) |

O formato é detectado pelo nome das abas. No histórico do banco os textos vêm destas etapas:
2 = cliente abre/responde, 4 = N1 pede informação, 5 = analista responde, 1 = cliente replica,
3/6/7 = avaliação (6/7 = finalizado, 8/9 = avaliação negativa, 10 = cancelamento). Quando o
mesmo chamado vem da planilha e da raspagem, a raspagem prevalece.
</details>

Como as áreas (squads) mudam de nome ao longo do tempo, o prompt só oferece para
classificação as áreas que aparecem em chamados abertos a partir de 2026; os antigos entram
apenas como referência de solução.

## RPA de raspagem do workspace

O banco exportado é irregular: o mesmo campo muda de dono conforme a etapa e não há data por
mensagem. A raspagem resolve isso lendo o próprio workspace, onde cada mensagem vem com
**autor, data e etapa**.

```powershell
# a planilha só fornece a lista de números de chamado (coluna #Processo)
python scripts\raspar_lecom.py --planilha "C:\...\export (3).xlsx" --abas 3

python scripts\raspar_lecom.py --numeros 14764 14755        # chamados específicos
python scripts\raspar_lecom.py --planilha ... --limite 100  # amostra
python scripts\raspar_lecom.py --planilha ... --status      # só mostra o andamento
python scripts\raspar_lecom.py --planilha ... --headless    # sem janela, para cargas longas
```

A carga completa (cerca de 12 mil chamados) leva em torno de 14 horas. Para deixá-la rodando
em segundo plano sem prender o terminal:

```powershell
Start-Process -WindowStyle Hidden -FilePath ".\.venv\Scripts\python.exe" `
  -ArgumentList "scripts\raspar_lecom.py","--planilha","C:\...\export (3).xlsx","--abas","3","--headless" `
  -RedirectStandardOutput "data\raspagem.log" -RedirectStandardError "data\raspagem.err"

Get-Content data\raspagem.log -Wait -Tail 5      # acompanhar
Get-Process python | Stop-Process                # parar (é retomável)
```

Como funciona:

1. Abre o Chrome com um perfil próprio em `data/chrome-profile`, herdando a sessão do seu
   perfil pessoal. Na primeira execução, se a sessão não vier, **a janela espera você fazer
   login** e daí em diante a sessão fica salva.
2. Para cada chamado abre `/workspace/flow/{n}` e lê a tabela de atividades. Se a URL direta
   for recusada com "Modo de ambiente inválido", refaz a busca pela lupa do cabeçalho.
3. Escolhe a **atividade aprovada mais recente**. Chamados que só têm atividade em andamento
   ou cancelada são **pulados**, porque o formulário deles não abre.
4. Abre o formulário e captura as respostas das APIs internas (`form-initial-values`,
   `business-data/history`, `data-grid`), em vez de raspar o DOM. O DOM é a reserva.
5. Salva um JSON por chamado em `data/raw/`.

A execução é **retomável**: chamados já salvos com sucesso (ou pulados) são ignorados numa
nova rodada, então dá para interromper e continuar depois. Rende cerca de 14 chamados por
minuto com 3 abas.

Para ver o que foi raspado:

```powershell
python scripts\inspecionar_raspagem.py          # cobertura e qualidade
python scripts\inspecionar_raspagem.py 14764    # um chamado em detalhe
```

Se a aplicação mudar e os seletores quebrarem, use
`python scripts\explorar_lecom.py --chamado 14764 --abrir` para despejar HTML e screenshots
em `data/debug/`, e `python scripts\diagnostico_raspagem.py <números>` para comparar cenários.

## Início rápido (Windows)

```powershell
copy .env.example .env      # edite e preencha OPENAI_API_KEY
.\start.ps1 -Ingest         # cria venv, instala deps, ingere o último xlsx de data\exports e sobe a API
```

Manual:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env

# 1) raspe os chamados (seção "RPA de raspagem") e indexe
python -m app.ingest                      # só a raspagem (data\raw), incremental
python -m app.ingest --limpar             # recria o índice do zero
python -m app.ingest --com-planilhas      # inclui também os .xlsx de data\exports
python -m app.ingest --forcar             # re-embeda tudo (ex.: trocou o modelo de embedding)

# 2) suba a API
python -m uvicorn app.main:app --reload --port 8000
```

- Tela de testes: http://localhost:8000
- Swagger: http://localhost:8000/docs

## Segurança

Todas as rotas de dados exigem credencial. Há duas formas, e ambas valem no mesmo endpoint:

| Forma | Header | Para quem |
|---|---|---|
| Token JWT | `Authorization: Bearer <token>` | pessoas e a tela de testes; expira em 60 min |
| API key | `X-API-Key: nxp_...` | integrações servidor a servidor, como o processo Lecom |

O que está implementado:

- **Senhas** com bcrypt. **API keys** guardadas como SHA-256; a chave em claro aparece uma vez só, na criação.
- **Limite por cliente**, configurável por usuário (`rate_limit_per_hour`), com resposta 429 e `Retry-After`.
- **Bloqueio de força bruta** no login: 10 falhas por IP em 15 minutos e o IP é barrado.
- **Papéis**: só administrador reindexa (`/ingest`) e administra clientes (`/usuarios`).
- **Cabeçalhos** `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, CSP e HSTS em produção.
- **Limite de corpo** da requisição e limites de tamanho por campo, contra abuso.
- **Trava de produção**: com `PRODUCAO=true` a API recusa subir se `JWT_SECRET` for curto, a senha do admin for fraca, ou `CORS_ORIGINS`/`ALLOWED_HOSTS` estiverem como `*`.
- **Consumo por cliente** registrado em `/usuarios/uso/relatorio`, útil para acompanhar custo.

A chave da OpenAI fica só no servidor. Quem consome a API nunca a vê.

## Endpoints

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/health` | público | Verificação de disponibilidade |
| GET | `/` | público (a tela pede login) | Tela de testes |
| POST | `/auth/login` | público | Devolve o token a partir de usuário e senha |
| GET | `/auth/me` | autenticado | Dados do usuário e prefixo da API key ativa |
| POST/DELETE | `/auth/api-key` | autenticado | Gera ou revoga a própria API key |
| POST | `/sugerir` | autenticado | Recebe o chamado e devolve classificação, dicas e mensagem ao cliente |
| POST | `/feedback` | autenticado | Informa se a resposta de um `/sugerir` foi útil |
| GET | `/feedback/relatorio?dias=30` | admin | Taxa de respostas úteis e últimos comentários negativos |
| GET | `/stats` | autenticado | Total indexado, última ingestão, valores distintos |
| POST | `/ingest` | admin | Reindexa. Corpo: `{"com_planilhas": false, "limpar": false, "forcar": false}` |
| GET/POST | `/usuarios` | admin | Lista e cria clientes da API |
| PATCH | `/usuarios/{nome}` | admin | Muda senha, limite ou ativa/desativa |
| POST/DELETE | `/usuarios/{nome}/api-key` | admin | Gera ou revoga a API key de um cliente |
| GET | `/usuarios/uso/relatorio` | admin | Chamadas por cliente, dia e rota |
| POST | `/whatsapp/webhook/{token}` | Evolution (token) | Recebe as mensagens do WhatsApp |
| POST | `/whatsapp/simular` | admin | Mostra o que o bot responderia, sem enviar |
| GET/POST | `/whatsapp/contatos` | admin | Telefones autorizados a consultar |
| DELETE | `/whatsapp/contatos/{telefone}` | admin | Remove um telefone |
| GET | `/whatsapp/status` | admin | Configuração e tamanho do índice |
| GET/PATCH | `/whatsapp/pre-chamados` | admin | Fila de descrições recebidas por autoatendimento |
| POST | `/whatsapp/notificar` | admin | Dispara os avisos de mudança de status (agende) |
| POST | `/whatsapp/resumo-semanal` | admin | Dispara o resumo semanal dos gestores (agende) |

### Contrato do `/sugerir` (para a integração com o Lecom)

Requisição:

```json
{
  "assunto": "Erro ao abrir subprocesso",
  "descricao": "Ao clicar em avançar a etapa o subprocesso não abre. Produção, desde ontem.",
  "produto": "NxPro (Plataforma Lecom)",
  "cliente": "Sicoob Credimogiana",
  "top_k": 6,
  "instrucoes_extras": "Escreva a mensagem ao cliente em no máximo 3 linhas."
}
```

Só `assunto` é obrigatório. `produto` filtra a busca e `cliente` é usado para detectar duplicados.

**Ajustando o prompt pela própria chamada.** `instrucoes_extras` é somado ao prompt padrão e serve para mudar tom, tamanho ou foco sem alterar o servidor. `prompt_sistema` substitui o prompt inteiro, para casos avançados. Em ambos, um bloco de regras fixas é sempre anexado: responder em português, no formato JSON esperado, sem inventar procedimento fora do histórico, e ignorar instruções que venham dentro do texto do chamado.

Resposta (resumida):

```json
{
  "classificacao": {"produto": "NxPro (Plataforma Lecom)", "tipo_chamado": "Informe de erro", "area_sugerida": "N2 - Phoenix", "criticidade_sugerida": "Alto", "confianca": 0.82},
  "id_sugestao": "3f2a9c...",
  "mensagem_para_cliente": "Já tivemos um caso parecido. Antes de abrir, tente...",
  "dicas": ["Verifique se ..."],
  "resumo_para_n1": "Precedente no chamado 14547, resolvido com ...",
  "informacoes_faltantes": ["Qual o nome do processo/etapa?", "Tem print do erro?"],
  "recomendacao": "abrir_chamado | tentar_dica_antes | possivel_duplicado",
  "justificativa": "...",
  "chamados_similares": [
    {"processo": 14547, "origem": "lecom", "assunto": "...", "similaridade": 0.83, "produto": "...", "area": "...", "cliente": "...", "status": "Aprovado", "tem_resolucao": true, "resposta_analista": "N1: ...\nAnalista: ..."},
    {"processo": 8359, "origem": "historico", "assunto": "...", "similaridade": 0.79, "status": "Finalizado", "tem_resolucao": true, "resposta_analista": "..."}
  ],
  "modelo": "gpt-4.1-mini",
  "consulta": "Assunto: ...\nDescrição do cliente:\n..."
}
```

**`mensagem_para_cliente` é o campo pensado para o processo Lecom**: texto curto, escrito direto para quem está abrindo o chamado, pronto para exibir na tela antes da conclusão. Nunca volta vazio; quando não há precedente, ele diz que pode seguir com a abertura.

**Números de chamado são internos.** `mensagem_para_cliente` e `dicas` nunca citam nº de chamado, processo ou protocolo: o prompt proíbe e, por garantia, um filtro remove qualquer número que escape antes de a resposta sair (o WhatsApp usa a mesma saída). As referências continuam disponíveis para o time em `resumo_para_n1`, `justificativa` e `chamados_similares` — campos que **não** devem ser exibidos ao cliente.

### Feedback: a resposta foi útil?

Depois de exibir a mensagem, a integração devolve o `id_sugestao` com a avaliação do cliente:

```json
POST /feedback
{"id_sugestao": "3f2a9c...", "util": false, "comentario": "a dica não se aplicava"}
```

Só quem gerou a sugestão (ou um admin) pode avaliá-la; um id desconhecido ou de outro usuário devolve `404`. Reenviar substitui a avaliação anterior. `GET /feedback/relatorio` (admin) mostra a taxa de utilidade por recomendação e as últimas avaliações negativas com os chamados que embasaram a resposta.

Erros: `401` sem credencial, `403` sem permissão, `409` índice vazio, `413` corpo grande demais, `422` campo inválido, `429` limite atingido, `502` falha no modelo, `503` chave da OpenAI ausente.

## Bot de WhatsApp (consulta de chamado pelo cliente)

O cliente manda uma mensagem no WhatsApp e recebe a situação do chamado dele. A ponte com o
WhatsApp é a [Evolution API](https://doc.evolution-api.com/); esta API só recebe o webhook e
responde.

O que o cliente pode mandar:

| Mensagem | Resposta |
|---|---|
| `14764` ou `chamado 14764` | assunto, status, etapa atual e a última resposta do atendimento |
| `meus chamados` | os chamados mais recentes dentro do escopo dele |
| `ajuda`, `oi` | o menu |

### Como a segurança funciona

O ponto central é que **quem não está cadastrado não recebe nada** — nem uma mensagem
genérica dizendo que precisa se cadastrar — e quem está só vê o que é seu.

1. **Lista de permissão por telefone.** Um administrador cadastra cada número em
   `POST /whatsapp/contatos` com nome, cooperativa (igual ao campo *Cliente* do chamado) e
   escopo. Número fora da lista, ou inativo, é ignorado em silêncio: o bot não responde nada,
   por decisão explícita (evita até confirmar que o número do bot existe/está ativo).
2. **Escopo por contato.** `proprio` mostra só chamados abertos pelo próprio telefone ou pelo
   nome cadastrado. `cooperativa` mostra todos os chamados da empresa (para gestores).
3. **Verificação chamado a chamado.** A cooperativa do chamado tem que ser a do contato, e no
   escopo `proprio` o telefone (com ou sem o nono dígito) ou o nome do solicitante tem que bater.
4. **Sem enumeração.** "Não existe" e "existe mas não é seu" produzem a mesma resposta, então
   ninguém descobre números de chamado de outras empresas por tentativa.
5. **Só campos de cara para o cliente.** Observações internas do squad, responsáveis internos e
   e-mails nunca entram na mensagem. Fica assunto, status, etapa, resposta do atendimento e
   pedidos de informação.
6. **Webhook protegido.** A URL tem um token secreto (`WHATSAPP_WEBHOOK_TOKEN`), a `apikey` da
   instância que a Evolution envia é conferida, mensagens de grupo e as nossas próprias são
   ignoradas, e cada mensagem é processada uma vez só (a Evolution reenvia em caso de timeout).
7. **Limite por telefone** (`WHATSAPP_MSGS_POR_10MIN`), contra abuso e contra custo com IA.
8. **Logs sem conteúdo.** O log registra telefone mascarado e o tipo de comando, nunca o texto.

### Configuração

1. Suba uma instância na Evolution API e conecte o número.
2. No `.env`: `WHATSAPP_HABILITADO=true`, `EVOLUTION_URL`, `EVOLUTION_INSTANCE`,
   `EVOLUTION_API_KEY` e um `WHATSAPP_WEBHOOK_TOKEN` com 32+ caracteres. Em produção a API
   recusa subir se faltar algum.
3. Na Evolution, configure o webhook da instância para
   `https://SUA-API/whatsapp/webhook/<token>` com o evento `MESSAGES_UPSERT`.
4. Cadastre os telefones:

```bash
curl -X POST https://SUA-API/whatsapp/contatos -H "Authorization: Bearer <token-admin>" \
  -H "Content-Type: application/json" \
  -d '{"telefone":"14991449422","nome":"Gabriel Andrade","cooperativa":"Nexum","escopo":"proprio"}'
```

Para testar sem WhatsApp, `POST /whatsapp/simular` (admin) devolve o que o bot responderia para
um telefone e um texto, sem enviar nada. `GET /whatsapp/status` mostra se está tudo configurado.

Os dados vêm dos JSON da raspagem (`data/raw`), indexados num SQLite leve que se atualiza
sozinho quando chegam arquivos novos. A resposta informa a data da última atualização, porque
o status reflete o momento da raspagem, não o instante da consulta.

## Publicação

O pacote de deploy assume que esta API roda **na mesma VM da API OCR**, em porta e subdomínio
próprios, sem interferir nela. O passo a passo completo está em
[deploy/oracle-vm-setup.md](deploy/oracle-vm-setup.md) e cobre Docker, Nginx com HTTPS, firewall,
criação da API key do Lecom, backup e rotina diária.

```bash
docker compose -f docker-compose.prod.yml up -d --build
sudo cp deploy/nginx.conf /etc/nginx/conf.d/n1-proativo.conf
sudo certbot --nginx -d n1.seudominio.com.br
```

## Ingestão diária

A ingestão é **incremental**: cada chamado vira um documento cujo hash é o conteúdo. Só chamados novos ou alterados são enviados para embedding. Rodar todo dia é barato.

1. Exporte do Lecom a planilha com a lista de chamados e rode o RPA de raspagem (seção acima). Como a raspagem é retomável, a rodada diária só processa o que ainda falta.
2. Rode `python -m app.ingest` (ou `POST /ingest`, ou o botão **Reindexar** da tela).

Use `--com-planilhas` para incluir também os `.xlsx` de `data/exports`, e `--limpar` para recriar o índice do zero (por exemplo, ao descartar dados antigos).

Para agendar no Windows (Agendador de Tarefas), ação:

```
Programa: C:\caminho\do\projeto\.venv\Scripts\python.exe
Argumentos: -m app.ingest
Iniciar em: C:\caminho\do\projeto
```

## Avaliação da classificação

```powershell
python scripts\avaliar.py --n 100            # em processo (usa o .env)
python scripts\avaliar.py --n 100 --api http://localhost:8000
```

Sorteia chamados com produto/área/tipo preenchidos, consulta a sugestão **excluindo o próprio processo** dos similares e mede a acurácia. O detalhe por chamado fica em `data\avaliacao.csv`, útil para comparar ajustes de prompt.

## Testes

```powershell
python -m pytest -q
```

Os testes não chamam a OpenAI: usam um embedding falso determinístico e um modelo simulado, com Chroma em pasta temporária. Precisam de um `.xlsx` em `data\exports`.

## Docker

```bash
docker compose up -d --build
docker compose exec n1-proativo python -m app.ingest   # com o xlsx em ./data/exports
```

## Estrutura

```
app/
  config.py            configurações (.env); converte CHROMA_PATH para caminho sem acento
  main.py              FastAPI + tela estática
  ingest/loader.py     lê os dois formatos de planilha (relatório e histórico do banco)
  ingest/raspagem.py   converte os JSON do RPA em chamados, remontando o diálogo por data
  ingest/builder.py    monta texto/metadata/hash de cada chamado (inclui o diálogo do atendimento)
  ingest/service.py    upsert incremental no Chroma de todas as fontes
  scraper/browser.py   abre o Chrome com a sessão do usuário (Playwright assíncrono)
  scraper/lecom.py     navegação: lista de atividades, escolha da aprovada, busca pela lupa
  scraper/extrator.py  captura as APIs do formulário (campos, histórico, anexos)
  scraper/runner.py    orquestra a raspagem em abas paralelas, retomável
  rag/embeddings.py    OpenAI embeddings em lote
  rag/store.py         ChromaDB persistido em data/chroma
  rag/retriever.py     busca de similares (filtro por produto, ignora cancelados)
  rag/service.py       prompt + structured output -> resposta
  rag/router.py        rotas
  prompts/sugestao.txt system prompt (edite aqui para ajustar o comportamento)
  static/index.html    tela de testes
scripts/
  raspar_lecom.py         RPA de raspagem (entrada principal)
  inspecionar_raspagem.py resumo do que foi raspado
  explorar_lecom.py       despeja HTML/screenshots para remapear seletores
  diagnostico_raspagem.py compara cenários de raspagem quando algo falha
  diagnostico_ambiente.py compara URL direta x lupa (erro "modo de ambiente inválido")
  avaliar.py              avaliação leave-one-out da classificação
tests/                    pytest
```

## Limitações conhecidas

- **Chamados sem atividade aprovada não são raspados.** O formulário deles não abre (só existe atividade em andamento ou cancelada), então o RPA os pula e registra o motivo no JSON. Na amostra inicial isso foi cerca de 14% dos chamados mais recentes.
- **ChromaDB e acentos no caminho (Windows):** o Chroma 1.5.x não grava os binários do índice HNSW quando a pasta tem caractere não-ASCII no caminho (ex.: "Projetos Históricos"), e a próxima abertura falha com `Error loading hnsw index`. A configuração converte `CHROMA_PATH` automaticamente para o nome curto 8.3 (ou `%LOCALAPPDATA%\n1-proativo`). Se aparecer esse erro, apague `data\chroma` e rode `python -m app.ingest` de novo.
- No formato **Relatório** a maioria dos chamados só tem Assunto e a resposta do analista aparece em poucos casos (o campo é sobrescrito a cada etapa). O formato **Histórico** resolve isso, mas cobre 2024 até o fim de 2025 e não tem data de abertura confiável (só ~40% dos chamados têm data) nem o nº do processo Lecom.
- Sem autenticação no MVP. Para produção, reaproveitar o módulo JWT da API OCR NEXUM.
