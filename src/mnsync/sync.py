"""
Orquestación: de Moodle a Notas Parciales.

El orden importa y es deliberado. Primero se descargan y se verifican **todos**
los grupos; solo cuando todos pasaron las rejas se escribe algo. Así una
configuración equivocada en el segundo grupo se descubre antes de haber
escrito nada del primero, y no queda una corrida a medio aplicar.

La unidad de trabajo es el **grupo**, no el curso: cada grupo tiene su propia
descarga, su propio plan y sus propios resultados, para que el alcance de
cada escritura sea demostrable después.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Course, Credentials, Group
from .errors import ScopeError
from .guard import GuardVerdict, check, summarize_plan
from .moodle_export import GradeExport, MoodleSession, write_moodle_xlsx
from .report import GroupOutcome, RunReport
from .uploader import Uploader


@dataclass
class GroupExport:
    """Lo descargado para un grupo, antes de tocar Notas Parciales."""

    group: Group
    export: GradeExport
    xlsx: Path


def fetch_groups(
    course: Course,
    creds: Credentials,
    work_dir: Path,
    *,
    groups: list[Group] | None = None,
    session: MoodleSession | None = None,
) -> list[GroupExport]:
    """
    Descarga las notas de cada grupo y verifica que los alcances sean distintos.

    Una sola sesión de Moodle para todos los grupos.
    """
    objetivo = list(groups if groups is not None else course.groups)

    if session is None:
        session = MoodleSession(creds.moodle_url)
        session.login(creds.moodle_username, creds.moodle_password)

    exports: list[GroupExport] = []
    for g in objetivo:
        export = session.export_group(course.moodle_course_id, g.moodle_group_id)
        xlsx = write_moodle_xlsx(export, work_dir / f"calificaciones_{course.id}_{g.slug}.xlsx")
        exports.append(GroupExport(group=g, export=export, xlsx=xlsx))

    _assert_groups_are_distinct(exports)
    return exports


def _assert_groups_are_distinct(exports: list[GroupExport]) -> None:
    """
    Si dos grupos traen exactamente los mismos estudiantes, el filtro no se aplicó.

    Es la segunda red bajo la reja de alcance de ``moodle_export``: aquella
    comprueba que Moodle *dijo* haber aceptado el grupo; esta comprueba que los
    datos realmente difieren. Subir el mismo listado dos veces escribiría las
    notas de un grupo dentro del otro.
    """
    for i, a in enumerate(exports):
        for b in exports[i + 1 :]:
            if not a.export.cedulas or not b.export.cedulas:
                continue
            if a.export.cedulas == b.export.cedulas:
                raise ScopeError(
                    f"Los grupos de Moodle {a.group.moodle_group_id} y "
                    f"{b.group.moodle_group_id} devolvieron exactamente los mismos "
                    f"{len(a.export.cedulas)} estudiantes.",
                    remedio=(
                        "Moodle no está separando los grupos, así que no se puede saber "
                        "qué nota va a qué grupo del sistema oficial. Revisá el «Modo de "
                        "grupo» del curso en Moodle y los «moodle_group_id» de "
                        "courses.yml. No se escribió nada."
                    ),
                )


def sync_course(
    course: Course,
    creds: Credentials,
    work_dir: Path,
    *,
    commit: bool = False,
    groups: list[Group] | None = None,
    allow_update: bool | None = None,
    fence_journal: Path | None = None,
    session: MoodleSession | None = None,
) -> RunReport:
    """
    Sincroniza un curso entero (o los grupos indicados).

    Sin ``commit=True`` no se escribe nada: es el modo por defecto, heredado
    del script de Notas Parciales.
    """
    if allow_update is None:
        allow_update = course.policy.allow_update

    report = RunReport(course=course, commit=commit)

    # --- Fase A: descargar y verificar TODO antes de escribir NADA -------
    exports = fetch_groups(course, creds, work_dir, groups=groups, session=session)

    # --- Fase B: planificar y aplicar, grupo por grupo -------------------
    uploader = Uploader(course, creds, work_dir, fence_journal=fence_journal)

    for ge in exports:
        outcome = _sync_one_group(
            uploader, ge, commit=commit, allow_update=allow_update, course=course
        )
        report.outcomes.append(outcome)

    return report


def _sync_one_group(
    uploader: Uploader,
    ge: GroupExport,
    *,
    commit: bool,
    allow_update: bool,
    course: Course,
) -> GroupOutcome:
    """
    Planifica y aplica un grupo.

    Si algo falla, el fallo queda encerrado en este grupo: los demás siguen.
    Que un grupo tenga un problema no es razón para dejar los otros sin subir.
    """
    from .errors import MnsyncError

    try:
        plan_path, _ = uploader.plan(ge.group, ge.xlsx)
    except MnsyncError as e:
        vacio = summarize_plan_vacio(ge.group)
        return GroupOutcome(
            summary=vacio,
            verdict=GuardVerdict(allowed=False),
            allow_update=allow_update,
            error=str(e),
        )

    summary = summarize_plan(plan_path, ge.group)
    verdict = check(summary, course.policy)

    if verdict.blocked:
        return GroupOutcome(
            summary=summary, verdict=verdict, escrito=False, allow_update=allow_update
        )

    try:
        resultados, _ = uploader.apply(
            ge.group, plan_path, commit=commit, allow_update=allow_update
        )
    except MnsyncError as e:
        return GroupOutcome(
            summary=summary, verdict=verdict, allow_update=allow_update, error=str(e)
        )

    return GroupOutcome(
        summary=summary,
        verdict=verdict,
        escrito=commit,
        allow_update=allow_update,
        resultados_path=resultados,
    )


def summarize_plan_vacio(group: Group):
    """Un resumen sin filas, para poder reportar un grupo que ni llegó a planificar."""
    from collections import Counter

    from .guard import PlanSummary

    return PlanSummary(group=group, total=0, por_accion=Counter(), plan_path=Path("(sin plan)"))
