import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse

from app.auth.router import router as auth_router
from app.config import get_settings
from app.rag.router import router as rag_router
from app.security import LimiteTamanhoMiddleware, SecurityHeadersMiddleware
from app.users.repository import preparar_banco
from app.users.router import router as users_router
from app.whatsapp import repositorio as whatsapp_repo
from app.whatsapp.router import router as whatsapp_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

settings = get_settings()
STATIC_DIR = Path(__file__).resolve().parent / "static"

if settings.producao:
    problemas = settings.validar_producao()
    if problemas:
        raise RuntimeError("Configuração insegura para produção: " + "; ".join(problemas))

app = FastAPI(
    title="N1 Proativo - Sugestões para chamados Lecom",
    description=(
        "Recebe o conteúdo de um chamado que está sendo aberto, busca chamados parecidos no "
        "histórico e devolve classificação (produto/área/tipo), dicas fundamentadas, possíveis "
        "duplicados e uma mensagem pronta para exibir ao cliente.\n\n"
        "**Autenticação**: `POST /auth/login` devolve um token para o header "
        "`Authorization: Bearer <token>`; integrações podem usar `X-API-Key` no lugar."
    ),
    version="1.0.0",
    docs_url="/docs" if settings.docs_habilitado else None,
    redoc_url="/redoc" if settings.docs_habilitado else None,
    openapi_url="/openapi.json" if settings.docs_habilitado else None,
)

# A ordem importa: o middleware registrado por último roda primeiro na requisição.
app.add_middleware(SecurityHeadersMiddleware, producao=settings.producao)
app.add_middleware(LimiteTamanhoMiddleware, max_kb=settings.tamanho_max_requisicao_kb)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.hosts_list)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=False,  # a autenticação é por header, não por cookie
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    max_age=3600,
)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(rag_router)
app.include_router(whatsapp_router)


@app.on_event("startup")
def preparar() -> None:
    preparar_banco(settings.db_path, settings.admin_username, settings.admin_password,
                   settings.default_rate_limit_per_hour)
    whatsapp_repo.init_db(settings.db_path)
    if not settings.admin_password:
        log.warning("ADMIN_PASSWORD vazio: nenhum administrador foi criado automaticamente")
    log.info("API pronta (produção=%s, docs=%s, tela=%s)",
             settings.producao, settings.docs_habilitado, settings.tela_habilitada)


@app.get("/", include_in_schema=False)
def index():
    """Tela de testes. O conteúdo é público, mas todas as chamadas que ela faz exigem login."""
    if not settings.tela_habilitada:
        raise HTTPException(404, "Tela desabilitada neste ambiente")
    return FileResponse(STATIC_DIR / "index.html")
