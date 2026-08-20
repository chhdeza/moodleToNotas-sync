"""
El "mundo" que simula el servidor falso de Notas Parciales.

Guarda rosters, instrumentos y notas en memoria, y —lo más importante—
lleva un **diario** (``journal``) con cada llamada de escritura recibida.
Las pruebas afirman sobre ese diario: es el registro exacto de lo que una
corrida real habría escrito.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

# Marcadores del servidor real (ver notasparciales_upload.py:256-258)
NOTA_NO_NOTA = 999  # sin nota cargada
NOTA_NO_PRESENTO = 998  # no presentó / no entregó
NOTA_RETIRADO = 994  # retiro justificado


@dataclass
class Instrument:
    """Un instrumento de evaluación del modelo (una columna calificable)."""

    codigo: str  # "Tar1"
    nombre: str  # "Tarea 1 (2)"


@dataclass
class Student:
    """Un estudiante en el roster oficial de un grupo."""

    cedula: str
    nombre: str
    # {codigo_instrumento: valor}. Valor real 0-10, o 999/998/994.
    notas: dict[str, float | int] = field(default_factory=dict)


@dataclass
class GroupState:
    """El roster de un (cu, grupo) concreto."""

    cu: str
    grupo: int
    students: dict[str, Student] = field(default_factory=dict)

    def add(self, student: Student) -> Student:
        self.students[student.cedula] = student
        return student


@dataclass
class WriteRecord:
    """Una llamada a ``actualizarNotas`` tal como llegó."""

    cu: str
    grupo: int
    cedula: str
    instrumento: str
    instrumento_nombre: str
    nota: str  # tal cual vino en el JSON: "8.9"
    tipo_nota: int  # 3 = regular, 1 = no presentó
    observacion_codigo: int
    justificacion: str

    @property
    def es_no_presento(self) -> bool:
        return self.tipo_nota == 1

    def resumen(self) -> str:
        destino = "NO PRESENTÓ" if self.es_no_presento else self.nota
        return f"{self.cedula}/{self.instrumento} → {destino}"


@dataclass
class Scenario:
    """
    Un escenario de prueba completo.

    Todo lo que el servidor falso necesita saber, más los interruptores para
    inyectar las fallas que el sistema real produce de vez en cuando.
    """

    instruments: list[Instrument] = field(default_factory=list)
    groups: dict[tuple[str, int], GroupState] = field(default_factory=dict)
    nota_minima: str = "7"

    # --- inyección de fallas -------------------------------------------
    #: (cedula, instrumento) que el servidor se niega a modificar.
    locked: set[tuple[str, str]] = field(default_factory=set)
    #: Si no es None, a partir de la N-ésima escritura el servidor devuelve
    #: HTML en vez de JSON, simulando que expiró la sesión ASP.NET.
    session_dies_after: int | None = None
    #: Devolver siempre un roster vacío (el caso "0 estudiantes" que no da error).
    force_empty_roster: bool = False
    #: Hacer que actualizarNotas responda HayError=True.
    fail_writes_with: str | None = None

    # --- estado observable ---------------------------------------------
    journal: list[WriteRecord] = field(default_factory=list)
    read_calls: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # --- construcción ---------------------------------------------------
    def group(self, cu: str, grupo: int) -> GroupState:
        key = (str(cu), int(grupo))
        if key not in self.groups:
            self.groups[key] = GroupState(cu=str(cu), grupo=int(grupo))
        return self.groups[key]

    def add_student(
        self,
        cu: str,
        grupo: int,
        cedula: str,
        nombre: str,
        notas: dict[str, float | int] | None = None,
    ) -> Student:
        """
        Agrega un estudiante. Los instrumentos que no se mencionen quedan en
        999 (sin nota), que es como los devuelve el servidor real.
        """
        base: dict[str, float | int] = {i.codigo: NOTA_NO_NOTA for i in self.instruments}
        base.update(notas or {})
        return self.group(cu, grupo).add(Student(cedula=cedula, nombre=nombre, notas=base))

    # --- consultas ------------------------------------------------------
    def tabla_datos(self, cu: str, grupo: int) -> list[dict[str, Any]]:
        """Filas en el formato ``Tabla_Datos`` que espera el cliente."""
        if self.force_empty_roster:
            return []
        gs = self.groups.get((str(cu), int(grupo)))
        if gs is None:
            return []
        filas: list[dict[str, Any]] = []
        for st in gs.students.values():
            fila: dict[str, Any] = {
                "Tipo": "E",
                "Cedula": st.cedula,
                "Nombre": st.nombre,
                "Promedio": 0,
                "Condicion": 0,
            }
            fila.update(st.notas)
            filas.append(fila)
        return filas

    def valor_actual(self, cu: str, grupo: int, cedula: str, instrumento: str) -> float | int | None:
        gs = self.groups.get((str(cu), int(grupo)))
        if gs is None or cedula not in gs.students:
            return None
        return gs.students[cedula].notas.get(instrumento)

    def instrumento_nombre(self, codigo: str) -> str:
        for i in self.instruments:
            if i.codigo == codigo:
                return i.nombre
        return ""

    # --- escritura ------------------------------------------------------
    def record_write(self, record: WriteRecord) -> None:
        """Anota la escritura en el diario y actualiza el estado."""
        with self._lock:
            self.journal.append(record)
            gs = self.groups.get((record.cu, record.grupo))
            if gs is None or record.cedula not in gs.students:
                return
            nuevo: float | int = (
                NOTA_NO_PRESENTO if record.es_no_presento else float(record.nota)
            )
            gs.students[record.cedula].notas[record.instrumento] = nuevo

    def writes_for(self, cedula: str, instrumento: str | None = None) -> list[WriteRecord]:
        return [
            w
            for w in self.journal
            if w.cedula == cedula and (instrumento is None or w.instrumento == instrumento)
        ]

    def journal_summary(self) -> list[str]:
        return [w.resumen() for w in self.journal]

    def reset_journal(self) -> None:
        with self._lock:
            self.journal.clear()
            self.read_calls.clear()
