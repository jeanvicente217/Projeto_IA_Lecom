"""Regras de autorização e montagem das respostas do bot de WhatsApp.

Princípios de segurança deste módulo:
  1. Só telefone cadastrado e ativo recebe dados. Qualquer outro é ignorado em silêncio
     pelo router (nem uma mensagem genérica é enviada) — decisão explícita do produto.
  2. O acesso é verificado chamado a chamado: cooperativa igual à do contato e, no escopo
     "proprio", telefone ou nome do solicitante batendo com o cadastro.
  3. "Não encontrado" e "não autorizado" produzem a MESMA resposta, para que ninguém consiga
     descobrir números de chamado de outras empresas por tentativa.
  4. Só campos de cara para o cliente são mostrados. Observações internas (CT_OBS_SQUAD),
     responsáveis internos (AUX_*) e e-mails nunca saem daqui.

Autoatendimento inteligente: quando a mensagem não é um número nem um comando conhecido,
ela é tratada como a descrição de um problema novo e roda a MESMA inteligência do endpoint
`/sugerir` (RAG sobre o histórico) — o cliente recebe uma dica fundamentada, um aviso de
possível duplicado, ou a confirmação de que pode seguir com a abertura, sem precisar sair
do WhatsApp. Se a recomendação for abrir chamado, perguntamos se ele quer registrar; a
confirmação vira um item na fila de pré-chamados (`whatsapp_pre_chamados`) para um humano
revisar — o bot NÃO abre chamado sozinho no Lecom, só prepara o terreno.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.whatsapp import indice, repositorio
from app.whatsapp.repositorio import Contato
from app.whatsapp.telefone import variantes

log = logging.getLogger(__name__)

# campos do histórico que o cliente pode ver, com o rótulo que aparece na mensagem
HISTORICO_VISIVEL = {
    "CT_DETALHAMENTO_CHAMADO": "Resposta do atendimento",
    "CT_FALTA_INFO": "Pedido de informação",
}
MAX_TEXTO_RESPOSTA = 600
MAX_ASSUNTO = 90
TTL_CONFIRMACAO_MIN = 30
TTL_SATISFACAO_MIN = 60 * 24  # a pergunta de satisfação fica valendo por 1 dia

CMD_LISTA = re.compile(r"\b(meus?\s+chamados?|lista|listar|chamados|abertos)\b", re.I)
CMD_NUMERO = re.compile(r"(?<!\d)(\d{3,6})(?!\d)")
CMD_AJUDA = re.compile(
    r"^\s*(ajuda|help|menu|oi|ol[aá]|bom\s?dia|boa\s?tarde|boa\s?noite|obrigad[oa]|valeu)\s*[!.?]*\s*$",
    re.I)
CMD_SIM = re.compile(r"^\s*(sim|s|yes|confirmo|confirmar|pode|isso|ok|okay|blz|beleza)\b", re.I)
CMD_NAO = re.compile(r"^\s*(n[aã]o|no|cancela|cancelar|deixa\s*(pra\s*l[aá])?)\b", re.I)
MIN_CHARS_DESCRICAO = 8


# ----------------------------------------------------------------------------- autorização

def _iguais(a: str | None, b: str | None) -> bool:
    return bool(a and b) and a.strip().casefold() == b.strip().casefold()


def autorizado(contato: Contato, chamado: dict[str, Any]) -> bool:
    """Decide se o contato pode ver este chamado (linha do índice)."""
    if not contato.ativo:
        return False
    if not _iguais(contato.cooperativa, chamado.get("cooperativa")):
        return False
    if contato.escopo == "cooperativa":
        return True
    # escopo "proprio": o chamado tem que ser do próprio telefone ou do próprio nome
    tel = chamado.get("telefone")
    if tel and tel in variantes(contato.telefone):
        return True
    return _iguais(contato.nome, chamado.get("solicitante"))


# ----------------------------------------------------------------------------- interpretação

def interpretar(texto: str) -> tuple[str, int | None]:
    """('chamado', 14764) | ('lista', None) | ('ajuda', None) | ('descricao', None).

    Um número encontrado sempre vira ('chamado', n): se não existir ou não for autorizado,
    quem chama (responder()) decide se trata como descrição de um problema novo — não aqui,
    porque essa decisão depende de saber se havia mais texto além do número.
    """
    t = (texto or "").strip()
    if not t:
        return "ajuda", None
    if CMD_AJUDA.match(t):
        return "ajuda", None
    m = CMD_NUMERO.search(t)
    if m:
        return "chamado", int(m.group(1))
    if CMD_LISTA.search(t):
        return "lista", None
    if len(t) >= MIN_CHARS_DESCRICAO:
        return "descricao", None
    return "ajuda", None


def _so_numero(texto: str) -> bool:
    return bool(re.fullmatch(r"\s*\d{3,6}\s*", texto or ""))


def _afirmativo(texto: str) -> bool | None:
    """True (sim) / False (não) / None (não deu para entender)."""
    t = (texto or "").strip()
    if CMD_SIM.match(t):
        return True
    if CMD_NAO.match(t):
        return False
    return None


# ----------------------------------------------------------------------------- formatação

def _data(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso[:19]).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return iso[:16]


def _corta(texto: str, n: int) -> str:
    texto = re.sub(r"\n{3,}", "\n\n", (texto or "").strip())
    return texto if len(texto) <= n else texto[: n - 1].rstrip() + "…"


def _ultimo_item_visivel(dados: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """A mensagem mais recente do atendimento que o cliente pode ver."""
    candidatos = []
    for bloco in dados.get("historico") or []:
        rotulo = HISTORICO_VISIVEL.get(bloco.get("campo"))
        if not rotulo:
            continue
        for item in bloco.get("itens") or []:
            if item.get("texto"):
                candidatos.append((item.get("data") or "", rotulo, item))
    if not candidatos:
        return None
    candidatos.sort(key=lambda c: c[0])
    _, rotulo, item = candidatos[-1]
    return rotulo, item


def formatar_chamado(dados: dict[str, Any], linha: dict[str, Any]) -> str:
    """Resumo do chamado só com o que o cliente pode ver."""
    campos = dados.get("campos") or {}
    atividades = dados.get("atividades") or []
    atual = atividades[0] if atividades else {}
    status = linha.get("status") or "-"
    partes = [
        f"*Chamado {linha['processo']}*",
        f"📌 {_corta(campos.get('LT_ASSUNTO') or linha.get('assunto') or '(sem assunto)', MAX_ASSUNTO)}",
        f"Status: *{status}*",
        f"Aberto em: {_data(linha.get('abertura'))}",
    ]
    if campos.get("LST_TIPO_PRODUTO"):
        partes.append(f"Produto: {campos['LST_TIPO_PRODUTO']}")
    if atual:
        etapa = f"Etapa atual: {atual.get('atividade', '-')}"
        if atual.get("status_texto"):
            etapa += f" ({atual['status_texto']})"
        partes.append(etapa)

    ultimo = _ultimo_item_visivel(dados)
    if ultimo:
        rotulo, item = ultimo
        quando = _data(item.get("data"))
        partes.append(f"\n💬 *{rotulo}* ({quando}):\n{_corta(item['texto'], MAX_TEXTO_RESPOSTA)}")
    else:
        partes.append("\nAinda não há resposta registrada do atendimento.")

    partes.append(f"\n_Informação atualizada em {_data(dados.get('raspado_em'))}._")
    return "\n".join(partes)


def formatar_lista(linhas: list[dict[str, Any]], contato: Contato) -> str:
    if not linhas:
        return ("Não encontrei chamados recentes para você. Se acabou de abrir um, ele aparece "
                "aqui na próxima atualização.")
    itens = []
    for l in linhas:
        itens.append(f"• *{l['processo']}* — {_corta(l.get('assunto') or '(sem assunto)', 60)}\n"
                     f"   {l.get('status') or '-'} · {_data(l.get('abertura'))[:10]}")
    escopo = "da sua empresa" if contato.escopo == "cooperativa" else "abertos por você"
    return (f"Seus chamados mais recentes ({escopo}):\n\n" + "\n".join(itens)
            + "\n\nMande o número de um chamado para ver os detalhes.")


def texto_ajuda(contato: Contato | None) -> str:
    quem = f"Olá, {contato.nome.split()[0]}! " if contato else "Olá! "
    return (quem + "Sou o assistente de chamados da Nexum. Posso te ajudar com:\n\n"
            "• *número do chamado* (ex.: 14764) → situação e última resposta\n"
            "• *meus chamados* → seus chamados mais recentes\n"
            "• *descreva um problema novo* → posso dar uma dica ou registrar para o time\n"
            "• *ajuda* → esta mensagem")


TEXTO_NAO_ENCONTRADO = (
    "Não encontrei o chamado *{n}* entre os chamados que você pode consultar. "
    "Confira o número ou mande *meus chamados* para ver a lista.")
TEXTO_LIMITE = "Você fez muitas consultas em pouco tempo. Aguarde alguns minutos e tente de novo."


# ----------------------------------------------------------------------------- resumo por IA

def resumo_ia(settings: Settings, texto_seguro: str) -> str | None:
    """Reescreve o resumo em linguagem simples. Recebe SÓ o texto já filtrado."""
    if not settings.whatsapp_resumo_ia:
        return None
    try:
        from app.rag.embeddings import get_openai

        resp = get_openai().chat.completions.create(
            model=settings.openai_chat_model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": (
                    "Você atende clientes pelo WhatsApp. Reescreva a situação do chamado abaixo "
                    "em no máximo 4 linhas, linguagem simples e cordial, sem inventar nada e sem "
                    "citar nomes de pessoas. Mantenha o número do chamado e o status. "
                    "Não use markdown além de *negrito*.")},
                {"role": "user", "content": texto_seguro},
            ],
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as e:  # noqa: BLE001
        log.warning("resumo por IA falhou: %s", type(e).__name__)
        return None


# --------------------------------------------------------------- autoatendimento (RAG /sugerir)

def _derivar_assunto(texto: str) -> str:
    linha = re.split(r"[\n.!?]", texto.strip(), maxsplit=1)[0].strip()
    return (linha[:MAX_ASSUNTO] or texto[:MAX_ASSUNTO] or "Consulta via WhatsApp")


def _consultar_sugestao(texto: str, contato: Contato, settings: Settings) -> str:
    """Roda a mesma inteligência do POST /sugerir para uma descrição livre recebida no
    WhatsApp: classifica, busca chamados parecidos e devolve a mensagem já pronta para o
    cliente. Se a recomendação for abrir chamado, guarda os dados e pergunta se o cliente
    quer registrar (vira um pré-chamado, não uma escrita direta no Lecom)."""
    assunto = _derivar_assunto(texto)
    try:
        from app.rag.schemas import SugestaoRequest
        from app.rag.service import sugerir as gerar_sugestao

        resp = gerar_sugestao(SugestaoRequest(
            assunto=assunto, descricao=texto.strip(), cliente=contato.cooperativa, top_k=5))
    except Exception:  # noqa: BLE001
        log.exception("falha ao gerar sugestão via WhatsApp para %s", contato.telefone)
        id_ = repositorio.criar_pre_chamado(settings.db_path, contato.telefone, contato.nome,
                                            contato.cooperativa, assunto, texto.strip(), None,
                                            origem="sugestao")
        return ("Não consegui analisar automaticamente agora, mas já registrei sua mensagem "
                f"(protocolo interno #{id_}). Um analista vai olhar em breve.")

    corpo = (resp.mensagem_para_cliente or "").strip() or \
        "Não encontrei uma orientação automática para esse caso."

    if resp.recomendacao == "abrir_chamado":
        repositorio.salvar_estado(settings.db_path, contato.telefone, "confirmar_pre_chamado", {
            "assunto": assunto, "descricao": texto.strip(),
            "classificacao": resp.classificacao.model_dump(), "origem": "sugestao",
        }, ttl_min=TTL_CONFIRMACAO_MIN)
        corpo += ("\n\nQuer que eu registre isso para nossa equipe seguir com a abertura? "
                  "Responda *sim* ou *não*.")
    else:
        corpo += "\n\nSe isso não resolver, é só me chamar de novo que eu registro para o time."

    return corpo


def _tratar_confirmacao_pre_chamado(afirmativo: bool | None, contato: Contato,
                                    dados: dict[str, Any], settings: Settings) -> str | None:
    if afirmativo is None:
        return None  # não reconheceu sim/não: quem chamou decide o que fazer
    repositorio.limpar_estado(settings.db_path, contato.telefone)
    if not afirmativo:
        return "Sem problemas, não vou registrar. Se precisar, é só me chamar de novo."
    id_ = repositorio.criar_pre_chamado(
        settings.db_path, contato.telefone, contato.nome, contato.cooperativa,
        dados["assunto"], dados["descricao"], dados.get("classificacao"),
        origem=dados.get("origem", "sugestao"))
    return (f"Registrado! (protocolo interno #{id_})\n"
            "Nossa equipe vai revisar e seguir com a abertura formal do chamado. "
            "Você pode continuar me chamando por aqui a qualquer momento.")


def _tratar_satisfacao(afirmativo: bool | None, contato: Contato, dados: dict[str, Any],
                       settings: Settings) -> str | None:
    if afirmativo is None:
        return None
    processo = dados["processo"]
    repositorio.limpar_estado(settings.db_path, contato.telefone)
    repositorio.registrar_resposta_satisfacao(settings.db_path, contato.telefone, processo,
                                              "sim" if afirmativo else "nao")
    if afirmativo:
        return "Que ótimo, fico feliz em ajudar! 🙂"
    repositorio.salvar_estado(settings.db_path, contato.telefone, "confirmar_pre_chamado", {
        "assunto": f"Reabertura do chamado {processo}",
        "descricao": f"Cliente indicou pelo WhatsApp que o chamado {processo} não foi resolvido.",
        "classificacao": None, "origem": "reabertura",
    }, ttl_min=TTL_CONFIRMACAO_MIN)
    return (f"Poxa, desculpa por isso. Quer que eu registre para o time retomar o contato "
            f"sobre o chamado {processo}? Responda *sim* ou *não*.")


def _oferecer_satisfacao_se_aplicavel(dados: dict[str, Any], linha: dict[str, Any],
                                      contato: Contato, settings: Settings) -> str:
    """Depois de mostrar um chamado já respondido pelo atendimento, pergunta (uma única vez
    por chamado) se aquilo resolveu o problema do cliente."""
    processo = linha["processo"]
    if repositorio.satisfacao_ja_perguntada(settings.db_path, contato.telefone, processo):
        return ""
    ultimo = _ultimo_item_visivel(dados)
    if not ultimo or ultimo[0] != "Resposta do atendimento":
        return ""
    repositorio.registrar_pergunta_satisfacao(settings.db_path, contato.telefone, processo)
    repositorio.salvar_estado(settings.db_path, contato.telefone, "satisfacao",
                              {"processo": processo}, ttl_min=TTL_SATISFACAO_MIN)
    return "\n\n_Isso resolveu o que você precisava? Responda *sim* ou *não*._"


# ----------------------------------------------------------------------------- orquestração

def responder(texto: str, contato: Contato, settings: Settings) -> str:
    """Monta a resposta para uma mensagem de um contato já autenticado (cadastrado e ativo)."""
    raw_dir = settings.whatsapp_raw_dir
    caminho_idx = Path(settings.db_path).parent / "chamados_idx.db"
    indice.atualizar(raw_dir, caminho_idx)

    # 0) havia uma pergunta pendente (confirmar abertura / satisfação)? trata primeiro.
    pendente = repositorio.obter_estado(settings.db_path, contato.telefone)
    if pendente:
        afirmativo = _afirmativo(texto)
        if pendente["tipo"] == "confirmar_pre_chamado":
            resp = _tratar_confirmacao_pre_chamado(afirmativo, contato, pendente["dados"], settings)
        elif pendente["tipo"] == "satisfacao":
            resp = _tratar_satisfacao(afirmativo, contato, pendente["dados"], settings)
        else:
            repositorio.limpar_estado(settings.db_path, contato.telefone)
            resp = None
        if resp is not None:
            return resp
        # não deu para entender como resposta à pendência: ela já foi limpa por
        # _tratar_* quando reconhecida; se ainda existir (afirmativo era None), removemos
        # e seguimos tratando a mensagem como um novo pedido, para não travar a conversa
        repositorio.limpar_estado(settings.db_path, contato.telefone)

    comando, numero = interpretar(texto)

    if comando == "chamado" and numero is not None:
        linha = indice.buscar(caminho_idx, numero)
        if not linha or not autorizado(contato, linha):
            if not _so_numero(texto):
                # o número fazia parte de uma frase maior: trata como problema novo
                return _consultar_sugestao(texto, contato, settings)
            return TEXTO_NAO_ENCONTRADO.format(n=numero)
        arquivo = raw_dir / f"{numero}.json"
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return TEXTO_NAO_ENCONTRADO.format(n=numero)
        texto_seguro = formatar_chamado(dados, linha)
        resposta = resumo_ia(settings, texto_seguro) or texto_seguro
        return resposta + _oferecer_satisfacao_se_aplicavel(dados, linha, contato, settings)

    if comando == "lista":
        if contato.escopo == "cooperativa":
            linhas = indice.listar(caminho_idx, cooperativa=contato.cooperativa,
                                   limite=settings.whatsapp_max_lista)
        else:
            linhas = indice.listar(caminho_idx, cooperativa=contato.cooperativa,
                                   telefones=variantes(contato.telefone),
                                   solicitante=contato.nome, limite=settings.whatsapp_max_lista)
        return formatar_lista(linhas, contato)

    if comando == "descricao":
        return _consultar_sugestao(texto, contato, settings)

    return texto_ajuda(contato)
