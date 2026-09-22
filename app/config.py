import logging
import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
log = logging.getLogger(__name__)


def caminho_ascii(p: Path) -> Path:
    """Devolve um caminho equivalente sem caracteres não-ASCII.

    O ChromaDB (binding Rust, testado na 1.5.9 em Windows) não grava os arquivos do índice
    HNSW quando o diretório tem acentos no caminho: só o chroma.sqlite3 é persistido e, na
    próxima abertura, falha com "Error loading hnsw index". No Windows usamos o nome curto
    8.3 do diretório; se não houver, caímos para %LOCALAPPDATA%/n1-proativo.
    """
    if str(p).isascii():
        return p
    p.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        import ctypes

        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(p), buf, len(buf))
        if 0 < n < len(buf) and buf.value.isascii():
            log.info("Caminho com acento; usando nome curto %s", buf.value)
            return Path(buf.value)
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "n1-proativo" / p.name
    log.warning("Caminho com acento sem nome curto disponível; usando %s", base)
    return base


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-4.1-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    # Armazenamento
    chroma_path: str = "data/chroma"
    exports_path: str = "data/exports"
    chroma_collection: str = "chamados"

    # Recuperação
    top_k_default: int = 6
    top_k_max: int = 15
    ignorar_status: str = "Cancelado,Rejeitado"

    # API
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "*"

    # Segurança
    jwt_secret: str = ""
    jwt_expire_minutes: int = 60
    database_path: str = "data/users.db"
    admin_username: str = "admin"
    admin_password: str = ""
    default_rate_limit_per_hour: int = 120
    # hosts aceitos no header Host ("*" libera todos; em produção informe o domínio)
    allowed_hosts: str = "*"
    # expõe /docs e /redoc (desligue em produção)
    docs_habilitado: bool = True
    # serve a tela de testes em "/" (exige login quando ligada)
    tela_habilitada: bool = True
    # aplica HSTS e exige HTTPS nos redirecionamentos
    producao: bool = False
    tamanho_max_requisicao_kb: int = 256

    # WhatsApp (Evolution API)
    whatsapp_habilitado: bool = False
    evolution_url: str = ""            # ex.: https://evolution.seudominio.com.br
    evolution_instance: str = ""       # nome da instância no Evolution
    evolution_api_key: str = ""        # apikey da instância (header "apikey")
    # segredo que faz parte da URL do webhook: só o Evolution que o conhece consegue chamar
    whatsapp_webhook_token: str = ""
    whatsapp_msgs_por_10min: int = 20  # limite por telefone, contra abuso e custo
    whatsapp_max_lista: int = 5        # quantos chamados aparecem em "meus chamados"
    whatsapp_resumo_ia: bool = False   # resume o histórico com o modelo (custo por consulta)
    whatsapp_raw_path: str = "data/raw"

    @property
    def whatsapp_raw_dir(self) -> Path:
        return self._resolve(self.whatsapp_raw_path)

    @property
    def chroma_dir(self) -> Path:
        return caminho_ascii(self._resolve(self.chroma_path))

    @property
    def exports_dir(self) -> Path:
        return self._resolve(self.exports_path)

    @property
    def status_ignorados(self) -> list[str]:
        return [s.strip() for s in self.ignorar_status.split(",") if s.strip()]

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def hosts_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()] or ["*"]

    @property
    def db_path(self) -> str:
        return str(self._resolve(self.database_path))

    def validar_producao(self) -> list[str]:
        """Problemas de segurança que impedem subir em produção."""
        erros = []
        if len(self.jwt_secret) < 32:
            erros.append("JWT_SECRET deve ter ao menos 32 caracteres")
        if not self.admin_password:
            erros.append("ADMIN_PASSWORD não pode ficar em branco")
        elif len(self.admin_password) < 12:
            erros.append("ADMIN_PASSWORD deve ter ao menos 12 caracteres")
        if "*" in self.cors_list:
            erros.append("CORS_ORIGINS não pode ser '*' em produção")
        if "*" in self.hosts_list:
            erros.append("ALLOWED_HOSTS não pode ser '*' em produção")
        if self.whatsapp_habilitado:
            if len(self.whatsapp_webhook_token) < 24:
                erros.append("WHATSAPP_WEBHOOK_TOKEN deve ter ao menos 24 caracteres")
            if not (self.evolution_url and self.evolution_instance and self.evolution_api_key):
                erros.append("EVOLUTION_URL, EVOLUTION_INSTANCE e EVOLUTION_API_KEY são obrigatórios")
        return erros

    @staticmethod
    def _resolve(p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else BASE_DIR / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
