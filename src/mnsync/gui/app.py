"""
El punto de entrada de la aplicación de escritorio.

    mnsync-app

Se instala junto con el extra ``gui``. Si PySide6 no está, el mensaje lo dice
con esas palabras y no con un ``ModuleNotFoundError``: quien abre esto no
necesariamente sabe qué es un módulo de Python.
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


def main(argv: list[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return _sin_pyside()

    from .asistente import (
        Asistente,
        PasoCodigos,
        PasoColumnas,
        PasoCredenciales,
        PasoGrupos,
        PasoReparto,
    )

    # Qt admite una sola QApplication por proceso, y crear la segunda es un
    # error fatal. Se reutiliza la que haya para que abrir la ventana no
    # dependa de quién arrancó primero.
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("mnsync")
    app.setApplicationDisplayName("mnsync — de Moodle a Notas Parciales")

    asistente = Asistente(Path("salida").resolve())
    for paso in (
        PasoCredenciales,
        PasoGrupos,
        PasoCodigos,
        PasoReparto,
        PasoColumnas,
    ):
        asistente.addPage(paso(asistente.borrador))

    asistente.resize(760, 560)
    asistente.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
