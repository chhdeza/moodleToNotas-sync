"""
Parser para los cuerpos que envía ``notasparciales_upload.py``.

El cliente real manda dos dialectos distintos al mismo servidor:

  - **JSON de verdad** (``actualizarNotas``, ``wmConsultarNotaMinima``).
  - **Literal de objeto JavaScript** — claves sin comillas y textos con
    comillas simples::

        {_peTipoRetorno: 1, _peAno: '2026', _peCU: '42', _peGrupo: 1}

    Eso NO es JSON válido. ASP.NET lo acepta igual, así que el servidor
    falso también tiene que aceptarlo, o las pruebas estarían midiendo un
    protocolo distinto al de producción.
"""

from __future__ import annotations

import json
import re
from typing import Any

# clave: valor  →  valor puede ser 'texto', "texto", número, true/false/null
_PAIR_RE = re.compile(
    r"""
    (?P<key>[A-Za-z_][A-Za-z0-9_]*)      # clave sin comillas
    \s*:\s*
    (?P<value>
        '(?:[^'\\]|\\.)*'                # 'texto'
      | "(?:[^"\\]|\\.)*"                # "texto"
      | -?\d+\.\d+                       # decimal
      | -?\d+                            # entero
      | true | false | null              # literales
    )
    """,
    re.VERBOSE,
)


def _coerce(raw: str) -> Any:
    if raw.startswith(("'", '"')):
        inner = raw[1:-1]
        return inner.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw == "null":
        return None
    if "." in raw:
        return float(raw)
    return int(raw)


def parse_body(body: str) -> dict[str, Any]:
    """
    Convierte el cuerpo de una petición a diccionario.

    Intenta JSON primero; si no es JSON, lo trata como literal JavaScript.
    """
    body = (body or "").strip()
    if not body:
        return {}

    try:
        data = json.loads(body)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    return {m.group("key"): _coerce(m.group("value")) for m in _PAIR_RE.finditer(body)}
