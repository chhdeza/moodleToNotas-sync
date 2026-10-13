"""
El vocabulario de la columna «accion» de un ``plan.csv``.

Lo define ``notasparciales_upload.py``, no este programa. Vive en un solo lugar
para que, si el script cambia una palabra, haya una sola línea que corregir y no
cinco módulos que se contradigan en silencio.
"""

from __future__ import annotations

SUBIR = "upload"
NO_PRESENTO = "mark_not_presented"
#: Una nota que **ya estaba puesta** y quedaría distinta. Es la única que exige
#: permiso explícito, y en la aplicación de escritorio ese permiso se da fila
#: por fila (specs/003, A-11).
SOBRESCRIBIRIA = "would_overwrite"
REVISAR = "review"
YA_ESTABA = "skip_already_set"
RETIRADO = "skip_retirado"
#: Quien no está en el roster consultado: casi siempre, de otro destino.
SIN_ROSTER = "skip_not_in_roster"

#: Cómo se llama cada acción en castellano llano, y qué debe hacer el profesor.
#:
#: El orden es el que se usa para contarlas en pantalla: están **todas**, incluso
#: las que dan cero, para que la vista no reduzca el plan a «se sube» y «no se
#: sube» (specs/003, A-10).
ETIQUETAS: dict[str, tuple[str, str]] = {
    SUBIR: ("Nota nueva subida", "Nada, es lo normal"),
    NO_PRESENTO: ("Marcada como «no presentó»", "Confirmá que de verdad no entregó"),
    SOBRESCRIBIRIA: ("Cambiaría una nota existente", "🛑 Revisar"),
    REVISAR: ("Caso raro, no se tocó", "🛑 Revisar"),
    YA_ESTABA: ("Ya estaba igual", "Nada. Ver muchas de estas es buena señal"),
    RETIRADO: ("Estudiante retirado", "Nada"),
    SIN_ROSTER: ("De otro grupo oficial", "Nada: lo recoge el plan de su grupo"),
}

#: Notas que se escriben sin pisar nada que ya estuviera puesto.
NUEVAS = (SUBIR, NO_PRESENTO)

#: Acciones que implican escribir en el sistema de la UNED.
ESCRITURA = (*NUEVAS, SOBRESCRIBIRIA)

#: Acciones que el profesor debería mirar sí o sí.
#:
#: ``SIN_ROSTER`` NO está acá: con el abanico de destinos, casi todas esas filas
#: son estudiantes de otro destino, y marcarlas como problema ahogaría el
#: reporte. Los que de verdad no aparecen en ningún lado se reportan aparte, con
#: nombre y apellido (specs/002, R-07).
REQUIEREN_ATENCION = (SOBRESCRIBIRIA, REVISAR)


def etiqueta(accion: str) -> str:
    """El nombre legible de una acción; una desconocida se muestra tal cual."""
    return ETIQUETAS.get(accion, (accion, ""))[0]
