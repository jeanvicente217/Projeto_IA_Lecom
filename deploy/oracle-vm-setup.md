# Deploy na VM Oracle (ao lado da API OCR)

Esta API foi preparada para conviver com a **API OCR NEXUM** na mesma máquina:

| | API OCR | N1 Proativo |
|---|---|---|
| Porta interna | 8000 | **8001** (apenas 127.0.0.1) |
| Container | `ocr-nexum-api` | `n1-proativo` |
| Pasta | `/opt/ocr-nexum-api` | `/opt/n1-proativo` |
| Nginx | `ocr-api.conf` | `n1-proativo.conf` |
| Subdomínio | api.seudominio.com.br | n1.seudominio.com.br |

Nada é compartilhado entre as duas, então uma não derruba a outra.

## 1. Enviar o projeto

```bash
# na sua máquina, a partir da pasta do projeto
scp -r app scripts deploy requirements.txt Dockerfile docker-compose.prod.yml .env.example \
    usuario@IP_DA_VM:/opt/n1-proativo/
```

A base de conhecimento (`data/chroma`) e os chamados raspados (`data/raw`) também precisam ir,
já que a raspagem roda na sua máquina Windows, não na VM:

```bash
scp -r data/chroma data/raw usuario@IP_DA_VM:/opt/n1-proativo/data/
```

> Alternativa: rode a ingestão na própria VM enviando só `data/raw`. Leva alguns minutos e
> gasta embeddings de novo, mas evita copiar o índice.

## 2. Configurar o `.env`

```bash
cd /opt/n1-proativo
cp .env.example .env
nano .env
```

Preencha com atenção:

```env
OPENAI_API_KEY=sk-...
JWT_SECRET=<48 caracteres aleatórios>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<senha forte, mínimo 12 caracteres>

PRODUCAO=true
CORS_ORIGINS=https://bpm.nexum.com.br
ALLOWED_HOSTS=n1.seudominio.com.br
DOCS_HABILITADO=false
```

Gere os segredos assim:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET
python3 -c "import secrets; print(secrets.token_urlsafe(18))"   # ADMIN_PASSWORD
```

Com `PRODUCAO=true` a API **recusa subir** se algum desses itens estiver fraco ou como `*`.
É proposital: evita publicar sem querer uma API aberta.

## 3. Subir

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f n1-proativo
curl http://127.0.0.1:8001/health
```

O container roda como usuário sem privilégios, com sistema de arquivos somente leitura,
gravando apenas no volume `data/`.

## 4. Nginx e HTTPS

```bash
sudo cp deploy/nginx.conf /etc/nginx/conf.d/n1-proativo.conf
sudo nano /etc/nginx/conf.d/n1-proativo.conf   # troque n1.seudominio.com.br
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d n1.seudominio.com.br
```

O Nginx aplica dois limites de taxa: 30 requisições por minuto no geral e 5 por minuto em
`/auth/login`, que é o alvo natural de força bruta. Isso soma ao limite por cliente da API.

## 5. Firewall

Exponha apenas 443 e 22 (SSH restrito por IP). **Não** abra 8000 nem 8001: elas escutam
só em 127.0.0.1 e o Nginx é o único caminho de fora.

```bash
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

Na OCI, libere 443 também na Security List da subnet.

## 6. Criar o cliente do Lecom

Com a API no ar, autentique como admin e crie um usuário só para a integração, com uma
API key própria. Assim dá para revogar o acesso do Lecom sem mexer em mais nada.

```bash
TOKEN=$(curl -s -X POST https://n1.seudominio.com.br/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"SUA_SENHA"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -X POST https://n1.seudominio.com.br/usuarios \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"username":"lecom","password":"senha-longa-que-nao-sera-usada","rate_limit_per_hour":600}'

curl -X POST https://n1.seudominio.com.br/usuarios/lecom/api-key \
  -H "Authorization: Bearer $TOKEN"
```

A última chamada devolve a chave **uma única vez**. Guarde-a no Lecom. Para trocar, chame o
mesmo endpoint de novo: a chave anterior deixa de valer na hora.

## 7. Consumo pelo processo Lecom

```bash
curl -X POST https://n1.seudominio.com.br/sugerir \
  -H 'X-API-Key: nxp_...' -H 'Content-Type: application/json' \
  -d '{"assunto":"Erro ao anexar documento","descricao":"...","cliente":"Sicoob Credimogiana"}'
```

O campo `mensagem_para_cliente` da resposta é o texto pronto para exibir na tela de abertura.

## 8. Atualização

```bash
cd /opt/n1-proativo
# envie os arquivos novos, depois:
docker compose -f docker-compose.prod.yml up -d --build
```

## 9. Rotina diária

A raspagem roda na máquina Windows (precisa do Chrome com a sessão do Lecom). Depois dela,
envie os JSON novos e reindexe:

```bash
# Windows
scp -r data/raw usuario@IP_DA_VM:/opt/n1-proativo/data/

# VM
docker compose -f docker-compose.prod.yml exec n1-proativo python -m app.ingest
```

Ou chame `POST /ingest` autenticado como admin.

## 10. Operação e segurança

- **Backup**: `data/users.db` (clientes e chaves) e `data/chroma` (índice). Um `tar` da pasta
  `data/` resolve. Sem ele, as API keys precisam ser regeradas.
- **Logs**: `docker compose -f docker-compose.prod.yml logs`, com rotação em 10 MB e 5 arquivos.
  O conteúdo dos chamados não é registrado.
- **Rotação de segredos**: trocar `JWT_SECRET` invalida todos os tokens ativos (as API keys
  continuam valendo). Para girar a chave de um cliente, use o endpoint de API key.
- **Consumo**: `GET /usuarios/uso/relatorio` mostra as chamadas por cliente, dia e rota, o que
  serve para acompanhar o custo da OpenAI.
- **Chave da OpenAI**: fica só no `.env` da VM, nunca no cliente. Quem chama a API não a vê.
