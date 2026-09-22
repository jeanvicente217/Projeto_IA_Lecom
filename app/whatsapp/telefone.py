"""Normalização de telefones brasileiros.

O mesmo número aparece de várias formas: "(14) 99144-9422" no formulário do Lecom,
"14991449422" digitado por alguém, "5514991449422@s.whatsapp.net" no JID do WhatsApp, e
números antigos ainda sem o nono dígito ("1491449422"). A comparação só é confiável depois
de reduzir tudo a uma forma canônica: 55 + DDD + número.
"""
from __future__ import annotations

import re

DDI_BR = "55"


def normalizar(valor: str | None) -> str | None:
    """Devolve o número no formato canônico (só dígitos, com 55) ou None se não parecer um
    telefone brasileiro válido.

    Aceita 10 dígitos (DDD + 8), 11 (DDD + 9 + 8), 12 (55 + DDD + 8) e 13 (55 + DDD + 9 + 8).
    """
    if not valor:
        return None
    digitos = re.sub(r"\D", "", str(valor).split("@")[0])
    if digitos.startswith("00"):
        digitos = digitos[2:]
    if len(digitos) in (12, 13) and digitos.startswith(DDI_BR):
        nacional = digitos[2:]
    elif len(digitos) in (10, 11):
        nacional = digitos
    else:
        return None
    ddd = nacional[:2]
    if not ("11" <= ddd <= "99"):
        return None
    return DDI_BR + nacional


def variantes(canonico: str) -> set[str]:
    """Formas equivalentes do mesmo número: com e sem o nono dígito.

    Celulares ganharam o 9 na frente ao longo de 2012-2016; um cadastro antigo pode ter o
    número sem ele e o WhatsApp devolve sempre com. As duas formas apontam para a mesma linha.
    """
    if not canonico:
        return set()
    out = {canonico}
    nacional = canonico[len(DDI_BR):]
    ddd, resto = nacional[:2], nacional[2:]
    if len(resto) == 9 and resto[0] == "9":
        out.add(DDI_BR + ddd + resto[1:])  # sem o nono dígito
    elif len(resto) == 8 and resto[0] in "6789":
        out.add(DDI_BR + ddd + "9" + resto)  # com o nono dígito
    return out


def mesmo_numero(a: str | None, b: str | None) -> bool:
    na, nb = normalizar(a), normalizar(b)
    if not na or not nb:
        return False
    return bool(variantes(na) & variantes(nb))


def mascarar(canonico: str | None) -> str:
    """Para logs: 5514991449422 -> 55 (14) *****-9422."""
    if not canonico or len(canonico) < 8:
        return "?"
    return f"{canonico[:2]} ({canonico[2:4]}) *****-{canonico[-4:]}"
