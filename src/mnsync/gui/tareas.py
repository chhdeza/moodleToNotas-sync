"""
Trabajo lento fuera del hilo de la ventana.

Bajar de Moodle y sondear Notas Parciales tarda minutos. Si eso corriera en el
hilo de la interfaz, la ventana dejaría de repintarse y Windows la marcaría
como «no responde» — que para alguien que no programa significa **se rompió**,
y lo lógico entonces es cerrarla a la mitad de una sincronización.

Así que todo lo que toca la red corre en un hilo aparte y avisa por señales.

Acá también se traduce cualquier fallo a algo que un profesor pueda leer. Una
traza de Python en pantalla no es un error reportado: es un error escondido
detrás de palabras que no significan nada para quien lo está viendo.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QThread, Signal

from ..errors import MnsyncError


@dataclass(frozen=True)
class Fallo:
    """Un error ya traducido a algo que se puede mostrar."""

    mensaje: str
    remedio: str = ""
    #: El detalle técnico, para el paquete de diagnóstico. No se muestra solo.
    detalle: str = ""

    @property
    def texto(self) -> str:
        return f"{self.mensaje}\n\n{self.remedio}" if self.remedio else self.mensaje


def traducir(error: BaseException) -> Fallo:
    """
    Convierte una excepción en algo que un profesor pueda leer y accionar.

    Los errores del propio programa ya vienen con su explicación y su remedio:
    se usan tal cual. Cualquier otro es un fallo que no anticipamos, y ahí lo
    honesto es decir que no lo esperábamos, no inventar una causa.
    """
    if isinstance(error, MnsyncError):
        return Fallo(
            mensaje=error.mensaje,
            remedio=error.remedio or "",
            detalle=f"{type(error).__name__}: {error}",
        )

    return Fallo(
        mensaje="El programa se encontró con un problema que no esperaba.",
        remedio=(
            "No se escribió nada. Probá de nuevo; si vuelve a pasar, usá "
            "«Exportar diagnóstico» y mandá el archivo a quien mantiene el programa."
        ),
        detalle=f"{type(error).__name__}: {error}",
    )


class Tarea(QThread):
    """
    Corre una función en su propio hilo y avisa cuando termina.

    Nunca deja escapar una excepción: un fallo se emite por ``fallada`` y la
    ventana decide cómo mostrarlo. Que un hilo muera en silencio dejaría la
    interfaz esperando para siempre un resultado que no va a llegar.
    """

    terminada = Signal(object)
    fallada = Signal(object)  # Fallo
    aviso = Signal(str)

    def __init__(self, funcion: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._funcion = funcion
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:  # pragma: no cover - se ejercita con la ventana viva
        try:
            resultado = self._funcion(*self._args, **self._kwargs)
        except BaseException as e:  # noqa: BLE001 - a propósito: nada puede escapar
            self.fallada.emit(traducir(e))
        else:
            self.terminada.emit(resultado)
