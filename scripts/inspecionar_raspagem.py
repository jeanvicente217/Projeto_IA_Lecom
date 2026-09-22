"""Resumo do que foi raspado: cobertura, campos, histórico e amostra de um chamado.

Uso:
    python scripts/inspecionar_raspagem.py            # resumo geral
    python scripts/inspecionar_raspagem.py 13872      # detalhe de um chamado
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.scraper.config import RAW_DIR  # noqa: E402

arquivos = sorted(RAW_DIR.glob("*.json"), key=lambda p: int(p.stem), reverse=True)
if not arquivos:
    raise SystemExit(f"nada em {RAW_DIR}")


def carregar(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


if len(sys.argv) > 1:
    d = carregar(RAW_DIR / f"{sys.argv[1]}.json")
    print(f"### chamado {d['processo']}  ({d.get('fonte')})")
    print("cabeçalho:", d.get("cabecalho", "")[:200])
    print("atividade :", json.dumps(d.get("atividade_escolhida"), ensure_ascii=False)[:260])
    print("detalhes  :", json.dumps(d.get("detalhes"), ensure_ascii=False)[:400])
    print(f"\n--- campos ({len(d.get('campos', {}))}):")
    for k, v in (d.get("campos") or {}).items():
        print(f"   {k:<26} {str(v)[:90]!r}")
    print(f"\n--- histórico ({len(d.get('historico', []))} campos):")
    for h in d.get("historico") or []:
        print(f"   == {h['campo']} ({len(h['itens'])} itens)")
        for it in h["itens"]:
            print(f"      [{it.get('atividade')}] {it.get('responsavel')} {str(it.get('data'))[:16]}")
            print(f"         {it['texto'][:220]!r}")
    print(f"\n--- anexos: {json.dumps(d.get('anexos'), ensure_ascii=False)[:300]}")
    raise SystemExit

ok, pulados, erros = [], [], []
for p in arquivos:
    d = carregar(p)
    (erros if d.get("erro") else pulados if d.get("pulado") else ok).append(d)

print(f"### {len(arquivos)} arquivos em {RAW_DIR}")
print(f"   ok={len(ok)}  pulados={len(pulados)}  erros={len(erros)}")

if ok:
    campos = Counter()
    n_hist = Counter()
    itens_hist = 0
    for d in ok:
        campos.update(d["campos"].keys())
        for h in d.get("historico") or []:
            n_hist[h["campo"]] += 1
            itens_hist += len(h["itens"])
    print(f"\n   campos por chamado: média {sum(len(d['campos']) for d in ok) / len(ok):.1f}")
    print(f"   chamados com histórico: {sum(1 for d in ok if d.get('historico'))}/{len(ok)}"
          f" | {itens_hist} mensagens no total")
    print(f"   fontes: {Counter(d.get('fonte') for d in ok)}")
    print("\n   campos mais presentes:")
    for c, n in campos.most_common(18):
        print(f"      {n:>4}/{len(ok)}  {c}")
    print("\n   campos com histórico (o diálogo do atendimento):")
    for c, n in n_hist.most_common(10):
        print(f"      {n:>4}/{len(ok)}  {c}")

if pulados:
    print(f"\n   pulados: {[d['processo'] for d in pulados][:25]}")
    print(f"      motivos: {Counter(d['pulado'] for d in pulados)}")
    exemplo = pulados[0]
    print(f"      exemplo {exemplo['processo']}: "
          f"{[(a['atividade'], a['status_texto']) for a in exemplo.get('atividades', [])]}")

if erros:
    print(f"\n   erros: {Counter(str(d['erro'])[:60] for d in erros).most_common(8)}")
    print(f"      números: {[d['processo'] for d in erros][:25]}")
