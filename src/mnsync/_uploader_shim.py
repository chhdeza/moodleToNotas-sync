"""
Lanzador de ``notasparciales_upload.py``.

**Todas** las invocaciones pasan por acá —producción, pruebas y ensayo— para
que lo que se prueba sea exactamente lo que se ejecuta. Un arnés de pruebas
que toma un camino distinto al de producción no prueba producción.

Hace tres cosas, en este orden:

1. Pone ``vendor/grade-uploader`` en el path e importa el script tal cual,
   sin modificarlo ni una línea.
2. Si ``NP_BASE_URL`` apunta a otro lado, redirige el cliente ahí. Sirve para
   el servidor falso de las pruebas. Sin esa variable, apunta al sistema real.
3. Si ``MNSYNC_FENCE_WRITES=1``, instala la **reja**: deja pasar todas las
   llamadas de lectura al servidor real, pero intercepta la única que
   escribe (``actualizarNotas``) y la anota en disco en vez de enviarla.

Sobre el punto 2: ``BASE`` y ``PAGE`` son constantes de módulo, pero el
cliente las lee *en el momento de cada llamada*
(``url = f"{PAGE}/{method}"``), así que reasignarlas después del import
alcanza y no hace falta tocar el repositorio original.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

#: Marca que deja la reja al instalarse. ``rehearse`` se niega a continuar si
#: no la encuentra: una reja que no puede demostrar que está puesta no sirve.
FENCE_SENTINEL_ENV = "MNSYNC_FENCE_INSTALLED"

#: El único método que escribe en el sistema de la UNED.
WRITE_METHOD = "actualizarNotas"


def vendor_script() -> Path:
    """Ruta a ``notasparciales_upload.py`` dentro del submódulo."""
    override = os.environ.get("MNSYNC_UPLOADER_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "vendor" / "grade-uploader" / "notasparciales_upload.py"


def _import_uploader():
    """Importa el script del submódulo como módulo, sin ejecutarlo."""
    script = vendor_script()
    if not script.exists():
        raise SystemExit(
            f"No se encontró «{script}».\n\n"
            "  ¿Qué hacer?  El submódulo no está inicializado. Ejecutá:\n"
            "                 git submodule update --init --recursive"
        )
    sys.path.insert(0, str(script.parent))
    import notasparciales_upload  # type: ignore[import-not-found]

    return notasparciales_upload


def _redirect_base(mod, base_url: str) -> None:
    """Apunta el cliente a otro servidor (el falso, en pruebas)."""
    mod.BASE = base_url.rstrip("/")
    mod.PAGE = f"{mod.BASE}/Formularios/CapturaNotas.aspx"


def install_write_fence(journal_path: Path) -> None:
    """
    Intercepta ``actualizarNotas`` a nivel de ``requests``.

    Todo lo demás viaja al servidor real sin tocarse: el ensayo tiene que
    ejercitar el login NTLM, el roster y los instrumentos de verdad, porque
    justamente esas son las cosas que un servidor falso no puede validar.
    """
    import requests

    original_send = requests.Session.send
    journal_path.parent.mkdir(parents=True, exist_ok=True)

    def fenced_send(self, request, **kwargs):  # type: ignore[no-untyped-def]
        url = getattr(request, "url", "") or ""
        if url.rstrip("/").endswith(f"/{WRITE_METHOD}"):
            body = request.body
            if isinstance(body, bytes):
                body = body.decode("utf-8", errors="replace")

            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "url": url,
                "metodo": WRITE_METHOD,
                "bloqueado": True,
                "payload": _safe_json(body),
            }
            with journal_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

            # Respuesta sintética con la forma que espera el cliente.
            resp = requests.Response()
            resp.status_code = 200
            resp.headers["Content-Type"] = "application/json; charset=utf-8"
            resp._content = json.dumps(
                {"d": {"HayError": False, "Descripcion": "ENSAYO: escritura interceptada"}}
            ).encode("utf-8")
            resp.url = url
            resp.request = request
            return resp

        return original_send(self, request, **kwargs)

    requests.Session.send = fenced_send  # type: ignore[method-assign]
    os.environ[FENCE_SENTINEL_ENV] = "1"


def _safe_json(body: str | None):
    if not body:
        return None
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return body


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    mod = _import_uploader()

    base_url = (os.environ.get("NP_BASE_URL") or "").strip()
    if base_url and base_url.rstrip("/") != mod.BASE.rstrip("/"):
        _redirect_base(mod, base_url)

    if os.environ.get("MNSYNC_FENCE_WRITES") == "1":
        journal = Path(os.environ.get("MNSYNC_FENCE_JOURNAL") or "rehearsal.jsonl")
        install_write_fence(journal)

    # El script usa argparse sobre sys.argv; se lo dejamos armado.
    sys.argv = [str(vendor_script()), *argv]
    return int(mod.main() or 0)


if __name__ == "__main__":
    sys.exit(main())
