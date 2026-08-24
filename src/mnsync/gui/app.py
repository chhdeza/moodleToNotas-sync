"""
El punto de entrada de la aplicación de escritorio.

    mnsync-app

Se instala junto con el extra ``gui``. Si PySide6 no está, el mensaje lo dice
con esas palabras y no con un ``ModuleNotFoundError``: quien abre esto no
necesariamente sabe qué es un módulo de Python.

El arranque decide qué pantalla corresponde. Un profesor que abre el programa
por primera vez no tiene nada configurado y lo que necesita es el asistente;
uno que ya lo usó no quiere volver a verlo nunca, quiere la pantalla que le
dice qué falta subir esta semana.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _sin_pyside() -> int:
    print()
    print("  No se encontró la parte gráfica del programa.")
    print()
    print("  Instalala con:")
    print()
    print('      pip install -e ".[gui]"')
    print()
    print("  Mientras tanto, la línea de comandos funciona igual: «mnsync --help».")
    print()
    return 1


def _contexto():
    """
    Lee lo que haya, sin exigir que esté completo.

    Faltar credenciales o cursos es el estado normal de una primera vez, no un
    error: se arranca igual y la ventana lo cuenta en su primera línea.
    """
    from ..config import load_config, load_credentials
    from ..errors import MnsyncError
    from .ventana import Contexto

    try:
        config = load_config()
    except MnsyncError:
        config = None

    return Contexto(creds=load_credentials(require=False), config=config)


def main(argv: list[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return _sin_pyside()

    from .asistente import construir_asistente
    from .ventana import Ventana

    # Qt admite una sola QApplication por proceso, y crear la segunda es un
    # error fatal. Se reutiliza la que haya para que abrir la ventana no
    # dependa de quién arrancó primero.
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("mnsync")
    app.setApplicationDisplayName("mnsync — de Moodle a Notas Parciales")

    work_dir = Path("salida").resolve()
    ctx = _contexto()

    # Sin cursos configurados no hay nada que sincronizar, así que el asistente
    # va primero. Si lo cancela, la ventana igual abre y explica qué falta.
    if ctx.config is None or not ctx.config.courses:
        asistente = construir_asistente(work_dir)
        asistente.exec()
        ctx = _contexto()

    ventana = Ventana(ctx, work_dir)
    ventana.resize(1000, 700)
    ventana.show()
    ventana.comprobar_ingresos()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
