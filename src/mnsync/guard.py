"""
Frenos de seguridad, entre planificar y escribir.

Los planes ya vienen calculados; acá se decide si **dejarlos correr**. Dos
frenos, que atienden dos accidentes distintos:

1. **Radio de daño** — una corrida que va a cambiar muchísimas notas de golpe
   suele ser un error grande, no un cuatrimestre atareado. Se cuenta por
   destino, no por grupo de Moodle (specs/002, R-12).

2. **Emparejamiento equivocado** — estudiantes que no aparecen en ningún roster
   oficial. Acá lo importante es *qué forma* tiene el problema, no cuántos son:

   - Unos pocos de un centro universitario, mientras el resto sí empareja:
     es un vacío de matrícula. Poco frecuente, legítimo, y **no detiene nada**
     (specs/001, D-07).
   - **Todos** los de un centro universitario: ese CU no resolvió destino.
     Se detiene ese CU.
   - **Todos** los de **todos** los centros universitarios: la corrida entera
     apunta a la asignatura equivocada. No se escribe nada.

   Juzgado solo por proporción, un grupo pequeño con dos vacíos legítimos es
   indistinguible de una corrida apuntada al modelo equivocado. La forma los
   separa: los vacíos caen sobre individuos dispersos, una configuración rota
   arrasa centros universitarios enteros. Y un freno que salta en corridas
   normales termina desactivado, y entonces no protege nada.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .config import Destination, Policy
from .uploader import ACCION_SIN_ROSTER, ACCIONES_DE_ESCRITURA


@dataclass
class PlanSummary:
    """Recuento de un ``plan.csv``, por acción, para un destino."""

    destination: Destination
    total: int
    por_accion: Counter[str]
    plan_path: Path

    @property
    def cambios(self) -> int:
        """Filas que escribirían algo en el sistema de la UNED."""
        return sum(self.por_accion[a] for a in ACCIONES_DE_ESCRITURA)

    @property
    def fuera_de_roster(self) -> int:
        return self.por_accion.get(ACCION_SIN_ROSTER, 0)

    @property
    def ya_estaban(self) -> int:
        return self.por_accion.get("skip_already_set", 0)

    @property
    def sobrescribirian(self) -> int:
        return self.por_accion.get("would_overwrite", 0)

    @property
    def a_revisar(self) -> int:
        return self.por_accion.get("review", 0)

    @property
    def propias(self) -> int:
        """Filas de estudiantes que sí pertenecen a este destino."""
        return self.total - self.fuera_de_roster


@dataclass
class GuardVerdict:
    """El fallo de los frenos para un destino."""

    allowed: bool
    reason: str = ""
    remedy: str = ""

    @property
    def blocked(self) -> bool:
        return not self.allowed


@dataclass
class Routing:
    """
    Dónde quedó cada estudiante después de consultar todos los destinos.

    Es la vista fusionada de los planes: un estudiante aparece en el plan de
    cada destino consultado, pero solo en uno de ellos con una acción real.
    """

    #: cédula → destino donde el roster oficial la reconoce.
    ubicados: dict[str, Destination] = field(default_factory=dict)
    #: CU → todas las cédulas de ese centro universitario en los xlsx.
    por_cu: dict[str, set[str]] = field(default_factory=dict)
    #: cédula → nombre, para poder nombrar a quien se quedó sin destino.
    nombres: dict[str, str] = field(default_factory=dict)

    def sin_destino(self) -> set[str]:
        todas = {c for cedulas in self.por_cu.values() for c in cedulas}
        return todas - set(self.ubicados)

    def sin_destino_del_cu(self, cu: str) -> set[str]:
        return self.por_cu.get(cu, set()) - set(self.ubicados)

    def cus_sin_ningun_destino(self) -> list[str]:
        """Centros universitarios donde **ningún** estudiante emparejó."""
        return sorted(
            cu
            for cu, cedulas in self.por_cu.items()
            if cedulas and self.sin_destino_del_cu(cu) == cedulas
        )

    @property
    def todo_sin_destino(self) -> bool:
        return bool(self.por_cu) and len(self.cus_sin_ningun_destino()) == len(self.por_cu)


@dataclass
class RoutingVerdict:
    """El fallo del discriminador de patrón, para la corrida completa."""

    allowed: bool
    #: CU que hay que detener por no haber resuelto ningún destino.
    cus_bloqueados: tuple[str, ...] = ()
    reason: str = ""
    remedy: str = ""

    @property
    def blocked(self) -> bool:
        return not self.allowed


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------
def summarize_plan(plan_path: Path, destination: Destination) -> PlanSummary:
    """Cuenta las filas de un plan por acción."""
    por_accion: Counter[str] = Counter()
    total = 0
    with plan_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            total += 1
            por_accion[(row.get("accion") or "").strip()] += 1
    return PlanSummary(
        destination=destination, total=total, por_accion=por_accion, plan_path=plan_path
    )


def merge_routing(planes: list[tuple[Destination, Path]]) -> Routing:
    """
    Fusiona los planes de todos los destinos en una sola vista.

    Un estudiante aparece en todos los planes; en el de su destino con una
    acción real, en los demás como ``skip_not_in_roster``. Quien no aparece con
    acción real en ninguno se quedó sin destino (specs/001, D-07).
    """
    routing = Routing()

    for destination, plan_path in planes:
        with plan_path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                cedula = (row.get("cedula") or "").strip()
                if not cedula:
                    continue
                cu = (row.get("cu") or "").strip()
                if cu:
                    routing.por_cu.setdefault(cu, set()).add(cedula)
                nombre = (row.get("nombre") or "").strip()
                if nombre:
                    routing.nombres.setdefault(cedula, nombre)
                if (row.get("accion") or "").strip() != ACCION_SIN_ROSTER:
                    routing.ubicados[cedula] = destination

    return routing


# ---------------------------------------------------------------------------
# Los frenos
# ---------------------------------------------------------------------------
def check_routing(routing: Routing) -> RoutingVerdict:
    """
    El discriminador de patrón (specs/002, R-09).

    Se revisa antes que nada: si la configuración está mal, el recuento de
    cambios ni siquiera significa lo que parece.
    """
    if not routing.por_cu:
        return RoutingVerdict(
            allowed=False,
            reason="La descarga de Moodle no trajo ningún estudiante.",
            remedy=(
                "Revisá que el curso y los grupos de Moodle sean los correctos, y que "
                "tengás permiso para ver sus calificaciones."
            ),
        )

    if routing.todo_sin_destino:
        return RoutingVerdict(
            allowed=False,
            cus_bloqueados=tuple(routing.cus_sin_ningun_destino()),
            reason=(
                "Ningún estudiante apareció en los rosters oficiales, en ninguno de los "
                f"{len(routing.por_cu)} centros universitarios."
            ),
            remedy=(
                "Cuando falla todo a la vez, el problema no es la matrícula: son los "
                "códigos de la asignatura. Revisá «asignatura», «modelo», «pac» y «ano» "
                "en courses.yml contra los menús de la página de Captura de Notas. El "
                "sistema no da error cuando esos códigos no corresponden: simplemente "
                "devuelve tablas vacías. No se escribió nada."
            ),
        )

    ciegos = routing.cus_sin_ningun_destino()
    if ciegos:
        detalle = ", ".join(
            f"CU {cu} ({len(routing.por_cu[cu])} estudiante(s))" for cu in ciegos
        )
        return RoutingVerdict(
            allowed=False,
            cus_bloqueados=tuple(ciegos),
            reason=f"Hay centros universitarios donde ningún estudiante emparejó: {detalle}.",
            remedy=(
                "Que falle un centro universitario entero apunta a un destino mal "
                "resuelto, no a estudiantes sin matrícula. Los demás centros sí se "
                "procesaron. Verificá que esos estudiantes estén matriculados en esta "
                "asignatura y que su grupo exista en Notas Parciales."
            ),
        )

    return RoutingVerdict(allowed=True)


def check(summary: PlanSummary, policy: Policy) -> GuardVerdict:
    """Decide si este destino puede escribir."""
    return _check_blast_radius(summary, policy)


def _check_blast_radius(summary: PlanSummary, policy: Policy) -> GuardVerdict:
    if not policy.blast_radius_enabled or summary.cambios <= policy.max_changes:
        return GuardVerdict(allowed=True)

    return GuardVerdict(
        allowed=False,
        reason=(
            f"Esta corrida cambiaría {summary.cambios} notas del destino "
            f"«{summary.destination.label}», y el límite configurado es {policy.max_changes}."
        ),
        remedy=(
            f"No se escribió nada en ese destino. Abrí «{summary.plan_path.name}» en Excel "
            "y revisá la columna «accion». Si los cambios son correctos, subí «max_changes» "
            "en courses.yml (o poné 0 para quitar el freno) y volvé a ejecutar."
        ),
    )
