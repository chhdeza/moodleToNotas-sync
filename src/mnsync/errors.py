"""
Errores de mnsync.

Todos los mensajes van en español y dirigidos al profesor: dicen qué pasó,
por qué, y qué hacer al respecto. Un error que no dice cómo arreglarse
todavía no está terminado.
"""

from __future__ import annotations


class MnsyncError(Exception):
    """Base de todos los errores esperables. La CLI los muestra sin traceback."""

    def __init__(self, mensaje: str, *, remedio: str | None = None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.remedio = remedio

    def __str__(self) -> str:
        if self.remedio:
            return f"{self.mensaje}\n\n  ¿Qué hacer?  {self.remedio}"
        return self.mensaje


class ConfigError(MnsyncError):
    """El archivo courses.yml o el .env tienen un problema."""


class MoodleError(MnsyncError):
    """Algo falló hablando con Moodle."""


class ScopeError(MnsyncError):
    """
    La descarga trajo estudiantes que no son del grupo pedido.

    Es un error grave a propósito: significa que el filtro de grupo no se
    aplicó, y subir esos datos escribiría notas de estudiantes ajenos.
    """


class UploaderError(MnsyncError):
    """El script de Notas Parciales falló."""


class GuardError(MnsyncError):
    """Un freno de seguridad detuvo la operación antes de escribir."""
