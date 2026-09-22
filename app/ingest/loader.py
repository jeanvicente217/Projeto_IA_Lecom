"""Leitura dos exports do Lecom (processo "Abertura de Chamados") para uma lista de Chamado.

Dois formatos são suportados e detectados pelo nome das abas:

1. **Export de relatório** (aba `Principal`): uma linha por chamado com o estado atual do
   formulário. Tem linhas de título antes do cabeçalho real, localizado pela célula
   "#Processo". A coluna "Cliente" aparece duas vezes (pessoa e cooperativa).
   Identificador: número do processo Lecom. `origem = "lecom"`.

2. **Export do banco do formulário** (abas `Todas as etapas` e `Ultima etapa por chamado`):
   uma linha por (processo, etapa, ciclo) com os campos do formulário em cada etapa. Aqui
   reconstruímos o diálogo cliente/N1/analista. Identificador: COD_PROCESSO_F (sequência
   própria do formulário, não é o número do processo Lecom). `origem = "historico"`.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

SHEET_RELATORIO = "Principal"
SHEET_HISTORICO = "Todas as etapas"

# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------

@dataclass
class Chamado:
    processo: int
    assunto: str
    origem: str = "lecom"  # "lecom" (número do processo Lecom) | "historico" (id do formulário)
    abertura: datetime | None = None
    status: str | None = None
    atividade: str | None = None
    descricao_solicitacao: str | None = None
    descricao_processo: str | None = None
    descricao: str | None = None
    resposta_analista: str | None = None
    motivo: str | None = None
    observacao_interna: str | None = None
    produto: str | None = None
    tipo: str | None = None
    area: str | None = None
    criticidade: str | None = None
    ambiente: str | None = None
    resolvido: str | None = None
    satisfacao: str | None = None
    solicitante: str | None = None
    aberto_por: str | None = None
    cliente: str | None = None
    # diálogo em ordem cronológica: [("Cliente"|"N1"|"Analista", texto), ...]
    historico: list[tuple[str, str]] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def doc_id(self) -> str:
        return str(self.processo) if self.origem == "lecom" else f"h{self.processo}"

    @property
    def rotulo(self) -> str:
        """Como o processo é citado no prompt/tela."""
        return str(self.processo) if self.origem == "lecom" else f"H{self.processo}"

    @property
    def descricao_cliente(self) -> str:
        """Junta os campos de descrição preenchidos pelo cliente (o formulário varia por versão)."""
        partes = [
            _clean(self.descricao_solicitacao),
            _clean(self.descricao_processo),
            _clean(self.descricao),
        ]
        return "\n".join(p for p in partes if p)

    @property
    def observacoes(self) -> str:
        partes = [_clean(self.observacao_interna), _clean(self.motivo)]
        return "\n".join(p for p in partes if p)

    @property
    def atendimento(self) -> list[tuple[str, str]]:
        """Diálogo do atendimento. Para o formato de relatório há no máximo uma resposta."""
        if self.historico:
            return self.historico
        r = _clean(self.resposta_analista)
        return [("Analista", r)] if r else []

    @property
    def tem_resolucao(self) -> bool:
        return any(papel == "Analista" for papel, _ in self.atendimento)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

_JUNK = re.compile(r"^(teste?\w*|qw\w*|asd\w*|kk+|ok|\d{1,4}|[a-z]{1,3}|[.\-_ ]+)$", re.I)
MIN_TEXTO = 15  # textos menores que isso são considerados lixo/teste


def _is_nan(v: Any) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NaT


def _clean(v: Any) -> str | None:
    if _is_nan(v):
        return None
    s = str(v).strip()
    return s or None


def _texto_util(v: Any) -> str | None:
    """Texto limpo, descartando conteúdo de teste ('teste', 'qw', '12')."""
    s = _clean(v)
    if not s or len(s) < MIN_TEXTO or _JUNK.match(s):
        return None
    return s


def _to_datetime(v: Any) -> datetime | None:
    if _is_nan(v):
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return pd.to_datetime(s, dayfirst=True).to_pydatetime()
    except Exception:  # noqa: BLE001
        return None


def _to_int(v: Any) -> int | None:
    if _is_nan(v):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


_TIPO_NORMALIZADO = {"solicitação de melhoria": "Melhoria"}


def _norm_tipo(v: str | None) -> str | None:
    if not v:
        return None
    return _TIPO_NORMALIZADO.get(v.lower(), v)


def _norm_area(v: str | None) -> str | None:
    return re.sub(r"\s+", " ", v).strip() if v else None


# ---------------------------------------------------------------------------
# Formato 1: export de relatório (aba Principal)
# ---------------------------------------------------------------------------

COLUNAS_RELATORIO = {
    "#Processo": "processo",
    "Abertura": "abertura",
    "Status do Processo": "status",
    "Atividade atual": "atividade",
    "Assunto:": "assunto",
    "Descreva a Solicitação": "descricao_solicitacao",
    "Descreva o processo": "descricao_processo",
    "Descrição": "descricao",
    "Detalhamento do Chamado": "resposta_analista",
    "Descreva o Motivo": "motivo",
    "Observação interna": "observacao_interna",
    "Qual o produto?": "produto",
    "Tipo de Chamado": "tipo",
    "Responsável": "area",
    "Nível de Criticidade": "criticidade",
    "Qual o ambiente?": "ambiente",
    "Tudo foi resolvido?": "resolvido",
    "Selecione o Nível de Satisfação": "satisfacao",
    "Nome do Solicitante": "solicitante",
    "Aberto por": "aberto_por",
}


def read_export(path: str | Path) -> pd.DataFrame:
    """Lê a aba Principal e devolve um DataFrame já com o cabeçalho correto."""
    raw = pd.read_excel(path, sheet_name=SHEET_RELATORIO, header=None)
    matches = raw.index[raw.iloc[:, 0].astype(str).str.strip() == "#Processo"]
    if len(matches) == 0:
        raise ValueError(f"Cabeçalho '#Processo' não encontrado em {path}")
    hdr = matches[0]
    df = raw.iloc[hdr + 1 :].copy()
    df.columns = [str(c).strip() for c in raw.iloc[hdr].tolist()]
    df = df.dropna(how="all").reset_index(drop=True)
    return df


def _cliente_column(df: pd.DataFrame) -> int | None:
    """A coluna 'Cliente' aparece duas vezes. Escolhe a que tem mais valores preenchidos
    (a da cooperativa/empresa); a outra é o nome de uma pessoa e é quase sempre vazia."""
    idx = [i for i, c in enumerate(df.columns) if c == "Cliente"]
    if not idx:
        return None
    return max(idx, key=lambda i: df.iloc[:, i].notna().sum())


def load_relatorio(path: str | Path) -> list[Chamado]:
    df = read_export(path)
    cli_idx = _cliente_column(df)

    pos = {}
    for i, c in enumerate(df.columns):
        if c in COLUNAS_RELATORIO and c not in pos:
            pos[c] = i

    faltando = [c for c in COLUNAS_RELATORIO if c not in pos]
    if faltando:
        log.warning("Colunas ausentes no export (seguindo sem elas): %s", faltando)

    chamados: list[Chamado] = []
    descartados = 0
    for row in df.itertuples(index=False, name=None):
        valores: dict[str, Any] = {}
        for col, attr in COLUNAS_RELATORIO.items():
            if col in pos:
                valores[attr] = row[pos[col]]

        processo = _to_int(valores.get("processo"))
        assunto = _clean(valores.get("assunto"))
        if processo is None or not assunto:
            descartados += 1
            continue

        chamado = Chamado(
            processo=processo,
            assunto=assunto,
            origem="lecom",
            abertura=_to_datetime(valores.get("abertura")),
            cliente=_clean(row[cli_idx]) if cli_idx is not None else None,
        )
        for attr in COLUNAS_RELATORIO.values():
            if attr in ("processo", "assunto", "abertura"):
                continue
            setattr(chamado, attr, _clean(valores.get(attr)))
        chamado.tipo = _norm_tipo(chamado.tipo)
        chamado.area = _norm_area(chamado.area)
        chamados.append(chamado)

    log.info("Relatório %s: %d linhas, %d chamados válidos, %d descartados (sem processo/assunto)",
             Path(path).name, len(df), len(chamados), descartados)
    return chamados


# ---------------------------------------------------------------------------
# Formato 2: export do banco do formulário (Todas as etapas)
# ---------------------------------------------------------------------------

# Quem escreve em cada etapa e em qual campo. O arquivo não tem data/hora por etapa e o
# COD_CICLO_F conta execuções de CADA etapa (não rodadas do chamado), então a ordem cronológica
# é aproximada: k-ésimo ciclo de cada etapa na sequência 2 (cliente abre/responde) -> 4 (N1
# pede informação) -> 5 (analista responde) -> 1 (cliente replica). A avaliação (3/6/7/8/9,
# campo CT_MOTIVO) é um valor carregado entre etapas e vai sempre para o fim do diálogo.
# 6/7 = finalizado, 8/9 = avaliação negativa, 10 = decisão de abertura/cancelamento.
ETAPAS_TEXTO: dict[int, tuple[str, str]] = {
    2: ("Cliente", "CT_DECREVA_SOLICITACAO"),
    4: ("N1", "CT_FALTA_INFO"),
    5: ("Analista", "CT_DETALHAMENTO_CHAMADO"),
    1: ("Cliente", "CT_DETALHAMENTO_CHAMADO"),
    10: ("Cliente", "CT_DECREVA_SOLICITACAO"),
}
ETAPAS_AVALIACAO = {3, 6, 7, 8, 9}
PAPEL_AVALIACAO = "Cliente (avaliação)"
ORDEM_ETAPA = {2: 0, 10: 0, 4: 1, 5: 2, 1: 3}
SHEET_ULTIMA = "Ultima etapa por chamado"

STATUS_ULTIMA_ETAPA = {
    2: "Abertura", 10: "Abertura",
    1: "Em andamento", 4: "Em andamento", 5: "Em andamento",
    3: "Avaliação", 6: "Finalizado", 7: "Finalizado",
    8: "Reaberto", 9: "Reaberto",
}


def _ultimo(serie: pd.Series) -> str | None:
    vals = [v for v in (_clean(x) for x in serie.tolist()) if v]
    return vals[-1] if vals else None


def _mais_comum(serie: pd.Series) -> str | None:
    vals = [v for v in (_clean(x) for x in serie.tolist()) if v]
    return max(set(vals), key=vals.count) if vals else None


def _ultimas_etapas(path: str | Path) -> dict[int, tuple[int, str | None]]:
    """{COD_PROCESSO_F: (etapa atual, RB_DECISAO_ABERTURA)} a partir da aba de última etapa."""
    try:
        u = pd.read_excel(path, sheet_name=SHEET_ULTIMA, usecols=lambda c: c in {"COD_PROCESSO_F", "COD_ETAPA_F", "RB_DECISAO_ABERTURA"})
    except ValueError:  # aba ausente
        return {}
    out: dict[int, tuple[int, str | None]] = {}
    for row in u.itertuples(index=False):
        cod, etapa = _to_int(row.COD_PROCESSO_F), _to_int(row.COD_ETAPA_F)
        if cod is not None and etapa is not None:
            out[cod] = (etapa, _clean(getattr(row, "RB_DECISAO_ABERTURA", None)))
    return out


def load_historico(path: str | Path) -> list[Chamado]:
    df = pd.read_excel(path, sheet_name=SHEET_HISTORICO)
    obrig = ["COD_PROCESSO_F", "COD_ETAPA_F", "COD_CICLO_F"]
    if any(c not in df.columns for c in obrig):
        raise ValueError(f"Colunas {obrig} não encontradas em {path}")
    ultimas = _ultimas_etapas(path)

    df["etapa_n"] = pd.to_numeric(df["COD_ETAPA_F"], errors="coerce").fillna(0).astype(int)
    df["ciclo_n"] = pd.to_numeric(df["COD_CICLO_F"], errors="coerce").fillna(0).astype(int)
    df["ordem_n"] = df["etapa_n"].map(ORDEM_ETAPA).fillna(9).astype(int)
    df = df.sort_values(["COD_PROCESSO_F", "ciclo_n", "ordem_n", "etapa_n"])

    chamados: list[Chamado] = []
    sem_texto = 0
    for cod, g in df.groupby("COD_PROCESSO_F", sort=False):
        processo = _to_int(cod)
        if processo is None:
            continue

        historico: list[tuple[str, str]] = []
        avaliacoes: list[str] = []
        vistos: set[str] = set()
        for row in g.itertuples(index=False):
            if row.etapa_n in ETAPAS_AVALIACAO:
                texto = _texto_util(getattr(row, "CT_MOTIVO", None))
                if texto and texto not in avaliacoes:
                    avaliacoes.append(texto)
                continue
            spec = ETAPAS_TEXTO.get(row.etapa_n)
            if not spec:
                continue
            papel, col = spec
            texto = _texto_util(getattr(row, col, None))
            if texto and texto not in vistos:  # o mesmo texto se repete quando o valor é carregado entre etapas
                vistos.add(texto)
                historico.append((papel, texto))
        if avaliacoes:
            historico.append((PAPEL_AVALIACAO, avaliacoes[-1]))

        assunto = _ultimo(g["LT_ASSUNTO"]) if "LT_ASSUNTO" in g else None
        primeira_desc = next((t for p, t in historico if p == "Cliente"), None)
        if not assunto:
            # chamados antigos não tinham o campo Assunto: usa a primeira linha da descrição
            if not primeira_desc:
                sem_texto += 1
                continue
            assunto = re.split(r"[\n.!?]", primeira_desc, maxsplit=1)[0].strip()[:120] or primeira_desc[:120]
        if not historico:
            sem_texto += 1
            continue

        if processo in ultimas:
            ultima_etapa, decisao = ultimas[processo]
        else:
            ultima = g.iloc[-1]
            ultima_etapa, decisao = int(ultima["etapa_n"]), _clean(ultima.get("RB_DECISAO_ABERTURA"))
        status = STATUS_ULTIMA_ETAPA.get(ultima_etapa, "Em andamento")
        if ultima_etapa == 10 and decisao == "Cancelar":
            status = "Cancelado"

        datas = [d for d in (_to_datetime(x) for x in g.get("AUX_EMAIL2", pd.Series(dtype=object)).tolist()) if d]
        analista = [t for p, t in historico if p == "Analista"]
        obs = [t for t in (_texto_util(x) for x in g.get("CT_OBS_SQUAD", pd.Series(dtype=object)).tolist()) if t]

        chamados.append(
            Chamado(
                processo=processo,
                assunto=assunto,
                origem="historico",
                abertura=min(datas) if datas else None,
                status=status,
                atividade=f"etapa {ultima_etapa}",
                descricao_solicitacao=primeira_desc,
                resposta_analista=analista[-1] if analista else None,
                motivo=avaliacoes[-1] if avaliacoes else None,
                observacao_interna="\n".join(dict.fromkeys(obs)) or None,
                produto=_mais_comum(g.get("LST_TIPO_PRODUTO", pd.Series(dtype=object))),
                tipo=_norm_tipo(_mais_comum(g.get("LST_PRODUTO", pd.Series(dtype=object)))),
                area=_norm_area(_ultimo(g.get("LST_RESP", pd.Series(dtype=object)))),
                criticidade=_mais_comum(g.get("LST_NIVEL_CRITICIDADE", pd.Series(dtype=object))),
                ambiente=_mais_comum(g.get("RB_AMBIENTE", pd.Series(dtype=object))),
                satisfacao=_ultimo(g.get("RB_SATISFACAO", pd.Series(dtype=object))),
                solicitante=_ultimo(g.get("LT_NOME", pd.Series(dtype=object))),
                cliente=_ultimo(g.get("LT_COOPERATIVA2", pd.Series(dtype=object))),
                historico=historico,
            )
        )

    log.info("Histórico %s: %d linhas, %d chamados válidos, %d descartados (sem texto útil)",
             Path(path).name, len(df), len(chamados), sem_texto)
    return chamados


# ---------------------------------------------------------------------------
# Detecção de formato
# ---------------------------------------------------------------------------

def detectar_formato(path: str | Path) -> str:
    sheets = pd.ExcelFile(path).sheet_names
    if SHEET_HISTORICO in sheets:
        return "historico"
    if SHEET_RELATORIO in sheets:
        return "relatorio"
    raise ValueError(f"Formato não reconhecido em {path}: abas {sheets}")


def load_chamados(path: str | Path) -> list[Chamado]:
    fmt = detectar_formato(path)
    return load_historico(path) if fmt == "historico" else load_relatorio(path)


def list_exports(exports_dir: str | Path) -> list[Path]:
    """Todos os .xlsx da pasta, do mais antigo para o mais novo."""
    return sorted(
        (p for p in Path(exports_dir).glob("*.xlsx") if not p.name.startswith("~$")),
        key=lambda p: p.stat().st_mtime,
    )


def latest_export(exports_dir: str | Path) -> Path | None:
    files = list_exports(exports_dir)
    return files[-1] if files else None
