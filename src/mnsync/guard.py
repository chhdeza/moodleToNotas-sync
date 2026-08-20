"""
Frenos de seguridad, entre planificar y escribir.

El plan ya viene calculado; acá se decide si **dejarlo correr**. Dos frenos,
que atienden dos accidentes distintos:

1. **Radio de daño** — una corrida que va a cambiar muchísimas notas de golpe
   suele ser un error grande, no un cuatrimestre atareado.
2. **Emparejamiento equivocado** — un plan lleno de ``skip_not_in_roster`` es
   la huella de un ``cu``/``grupo`` mal puesto en ``courses.yml``. Sin esta
   comprobación, ese caso no da error: simplemente deja al grupo entero sin
   subir, en silencio, y el profesor se entera cuando ya es tarde.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .config import Group, Policy
from .uploader import ACCIONES_DE_ESCRITURA

#: Si más de esta proporción de las filas quedó fuera del roster oficial,
#: se asume configuración equivocada y no falta de matrícula.
UMBRAL_FUERA_DE_ROSTER = 0.5

#: Por debajo de esta cantidad de filas no se juzga la proporción: en un
#: grupo diminuto, dos ausencias legítimas ya superarían cualquier umbral.
MINIMO_FILAS_PARA_JUZGAR = 4


@dataclass
class PlanSummary:
    """Recuento de un ``plan.csv``, por acción."""

    group: Group
    total: int
    por_accion: Counter[str]
    plan_path: Path

    @property
    def cambios(self) -> int:
        """Filas que escribirían algo en el sistema de la UNED."""
        return sum(self.por_accion[a] for a in ACCIONES_DE_ESCRITURA)

    @property
    def fuera_de_roster(self) -> int:
        return self.por_accion.get("skip_not_in_roster", 0)

    @property
    def ya_estaban(self) -> int:
        return self.por_accion.get("skip_already_set", 0)

    @property
    def sobrescribirian(self) -> int:
        return self.por_accion.get("would_overwrite", 0)

    @property
    def a_revisar(self) -> int:
        return self.por_accion.get("review", 0)


@dataclass
class GuardVerdict:
    """El fallo de los frenos para un grupo."""

    allowed: bool
    reason: str = ""
    remedy: str = ""

    @property
    def blocked(self) -> bool:
        return not self.allowed


def summarize_plan(plan_path: Path, group: Group) -> PlanSummary:
    """Cuenta las filas de un plan por acción."""
    por_accion: Counter[str] = Counter()
    total = 0
    with plan_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            total += 1
            por_accion[(row.get("accion") or "").strip()] += 1
    return PlanSummary(group=group, total=total, por_accion=por_accion, plan_path=plan_path)


def check(summary: PlanSummary, policy: Policy) -> GuardVerdict:
    """
    Decide si este grupo puede escribir.

    Se revisa primero el emparejamiento: si la configuración está mal, el
    recuento de cambios ni siquiera significa lo que parece.
    """
    veredicto = _check_roster_match(summary)
    if veredicto.blocked:
        return veredicto
    return _check_blast_radius(summary, policy)


def _check_roster_match(summary: PlanSummary) -> GuardVerdict:
    if summary.total < MINIMO_FILAS_PARA_JUZGAR or summary.fuera_de_roster == 0:
        return GuardVerdict(allowed=True)

    proporcion = summary.fuera_de_roster / summary.total
    if proporcion < UMBRAL_FUERA_DE_ROSTER:
        return GuardVerdict(allowed=True)

    g = summary.group
    return GuardVerdict(
        allowed=False,
        reason=(
            f"{summary.fuera_de_roster} de {summary.total} filas corresponden a "
            f"estudiantes que no aparecen en el grupo oficial "
            f"(CU {g.cu} / grupo {g.grupo})."
        ),
        remedy=(
            "Casi siempre significa que «cu» o «grupo» están mal en courses.yml "
            f"para el grupo de Moodle {g.moodle_group_id}. Verificá esos dos valores "
            "contra los menús de la página de Captura de Notas. La otra causa "
            "posible es que la columna «Número de ID» de Moodle no tenga las "
            "cédulas correctas."
        ),
    )


def _check_blast_radius(summary: PlanSummary, policy: Policy) -> GuardVerdict:
    if not policy.blast_radius_enabled or summary.cambios <= policy.max_changes:
        return GuardVerdict(allowed=True)

    return GuardVerdict(
        allowed=False,
        reason=(
            f"Esta corrida cambiaría {summary.cambios} notas del grupo "
            f"«{summary.group.label}», y el límite configurado es {policy.max_changes}."
        ),
        remedy=(
            f"No se escribió nada. Abrí «{summary.plan_path.name}» en Excel y revisá "
            "la columna «accion». Si los cambios son correctos, subí «max_changes» en "
            "courses.yml (o poné 0 para quitar el freno) y volvé a ejecutar."
        ),
    )
