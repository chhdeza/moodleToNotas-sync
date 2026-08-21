"""
Orquestación: de Moodle a Notas Parciales.

El orden importa y es deliberado.

**Fase A — recolectar.** Se descargan todos los grupos de Moodle del tutor y se
verifica el alcance de cada descarga. Los grupos de Moodle son ámbitos de
recolección: dicen *de quién* son los estudiantes. Una vez descargados, se
juntan en un solo conjunto y el grupo del que vinieron deja de importar
(specs/001, D-11).

**Fase B — enrutar.** Se averigua a qué destino de Notas Parciales pertenece
cada estudiante. El centro universitario acota la búsqueda; la pertenencia al
roster oficial decide. Un mismo CU puede repartirse en varios destinos
(specs/001, D-04), así que se consulta uno por uno y se fusionan los resultados.

**Fase C — escribir.** Recién cuando todo lo anterior pasó las rejas se escribe
algo, y siempre por destino, que es la unidad en la que aterriza una escritura
(specs/002, R-04).

Nada se escribe hasta que la fase B terminó completa: así una configuración
equivocada se descubre antes de haber escrito la primera nota, y no queda una
corrida a medio aplicar.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Course, Credentials, Destination, MoodleGroupRef
from .errors import ConfigError, MnsyncError, ScopeError
from .guard import (
    GuardVerdict,
    Routing,
    check,
    check_routing,
    merge_routing,
    summarize_plan,
)
from .moodle_export import (
    COL_APELLIDOS,
    COL_CEDULA,
    COL_INSTITUCION,
    COL_NOMBRE,
    GradeExport,
    MoodleSession,
    write_moodle_xlsx,
)
from .report import DestinationOutcome, RunReport
from .uploader import Uploader, cu_de_institucion


@dataclass
class GroupExport:
    """Lo descargado para un grupo de Moodle, antes de tocar Notas Parciales."""

    group: MoodleGroupRef
    export: GradeExport
    xlsx: Path


def fetch_groups(
    course: Course,
    creds: Credentials,
    work_dir: Path,
    *,
    groups: list[MoodleGroupRef] | None = None,
    session: MoodleSession | None = None,
) -> list[GroupExport]:
    """
    Descarga las notas de cada grupo de Moodle y verifica que los alcances difieran.

    Una sola sesión de Moodle para todos los grupos.
    """
    objetivo = list(groups if groups is not None else course.groups)
    if not objetivo:
        raise ConfigError(
            f"El curso «{course.id}» todavía no tiene ningún grupo de Moodle configurado.",
            remedio=(
                "Averiguá cuáles son los tuyos y copiálos a courses.yml, bajo "
                f"«moodle: groups:». Ejecutá:  mnsync groups --course {course.id}"
            ),
        )

    if session is None:
        session = MoodleSession(creds.moodle_url)
        session.login(creds.moodle_username, creds.moodle_password)

    exports: list[GroupExport] = []
    for g in objetivo:
        export = session.export_group(course.moodle_course_id, g.moodle_group_id)
        xlsx = write_moodle_xlsx(
            export,
            work_dir / f"calificaciones_{course.id}_{g.slug}.xlsx",
            conservar=set(course.item_map),
        )
        exports.append(GroupExport(group=g, export=export, xlsx=xlsx))

    _assert_groups_are_distinct(exports)
    return exports


def _assert_groups_are_distinct(exports: list[GroupExport]) -> None:
    """
    Si dos grupos traen exactamente los mismos estudiantes, el filtro no se aplicó.

    Es la segunda red bajo la reja de alcance de ``moodle_export``: aquella
    comprueba que Moodle *dijo* haber aceptado el grupo; esta comprueba que los
    datos realmente difieren. Un curso de Moodle contiene estudiantes de otros
    profesores (specs/001, D-12), así que descargar de más no es un detalle.
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
                        "de quién es cada estudiante. Revisá el «Modo de grupo» del curso "
                        "en Moodle y los identificadores de courses.yml. No se escribió nada."
                    ),
                )


def _cus_presentes(exports: list[GroupExport]) -> list[str]:
    """Los centros universitarios que aparecen en las descargas, en orden estable."""
    vistos: list[str] = []
    for ge in exports:
        for institucion in ge.export.instituciones:
            cu = cu_de_institucion(institucion)
            if cu and cu not in vistos:
                vistos.append(cu)
    return sorted(vistos)


def split_por_cu(
    exports: list[GroupExport],
    work_dir: Path,
    course_id: str,
    *,
    conservar: set[str] | None = None,
) -> dict[str, Path]:
    """
    Junta los grupos de Moodle y reparte a los estudiantes por centro universitario.

    Acá ocurren dos cosas de golpe. Primero, el **agrupamiento**: los estudiantes
    de todos los grupos del tutor quedan en un solo conjunto, y de qué grupo
    vinieron deja de importar (specs/001, D-11). Segundo, el reparto por CU.

    Lo segundo no es solo prolijidad. Al planificar un destino, el script
    autodetecta un destino para **cada** centro universitario que encuentre en
    el archivo, sondeando servidor por medio: con diez CU eso son cientos de
    consultas por plan, y todas terminan descartadas porque ese CU tiene su
    propio plan. Dándole un archivo con un solo centro universitario, no hay
    nada que autodetectar y la consulta se hace una sola vez, donde corresponde.
    """
    filas_por_cu: dict[str, list[dict[str, str]]] = {}
    headers: list[str] = []
    grade_headers: list[str] = []
    vistas: set[tuple[str, str]] = set()

    for ge in exports:
        headers = headers or list(ge.export.headers)
        for h in ge.export.grade_headers:
            if h not in grade_headers:
                grade_headers.append(h)

        for row in ge.export.rows:
            cedula = (row.get(COL_CEDULA) or "").strip()
            cu = cu_de_institucion(row.get(COL_INSTITUCION) or "")
            if not cedula or not cu:
                continue
            # Un estudiante en dos grupos de Moodle se cuenta una sola vez.
            if (cu, cedula) in vistas:
                continue
            vistas.add((cu, cedula))
            filas_por_cu.setdefault(cu, []).append(row)

    salidas: dict[str, Path] = {}
    for cu, filas in filas_por_cu.items():
        export = GradeExport(
            headers=list(headers),
            rows=filas,
            group_id=0,
            grade_headers=list(grade_headers),
        )
        salidas[cu] = write_moodle_xlsx(
            export,
            work_dir / f"calificaciones_{course_id}_cu{cu}.xlsx",
            conservar=conservar,
        )
    return salidas


def resolve_destinations(
    course: Course,
    uploader: Uploader,
    xlsx_por_cu: dict[str, Path],
) -> list[Destination]:
    """
    Los destinos a los que van a parar los estudiantes de esta corrida.

    Si ``courses.yml`` los declara, se usan tal cual —es lo que escribe el
    asistente—. Si no, se descubren sondeando los grupos 1 a 5 de cada centro
    universitario presente, todo con operaciones de lectura (specs/002, R-05).

    Se ignoran los destinos declarados de centros universitarios que esta
    corrida no trajo: consultarlos sería tiempo perdido.
    """
    if course.destinations:
        declarados = [d for d in course.destinations if d.cu in xlsx_por_cu]
        if declarados:
            return declarados

    return uploader.discover_destinations(sorted(xlsx_por_cu), xlsx_por_cu)


def sync_course(
    course: Course,
    creds: Credentials,
    work_dir: Path,
    *,
    commit: bool = False,
    groups: list[MoodleGroupRef] | None = None,
    allow_update: bool | None = None,
    fence_journal: Path | None = None,
    session: MoodleSession | None = None,
) -> RunReport:
    """
    Sincroniza un curso entero (o solo los grupos de Moodle indicados).

    Sin ``commit=True`` no se escribe nada: es el modo por defecto, heredado
    del script de Notas Parciales.
    """
    if allow_update is None:
        allow_update = course.policy.allow_update

    report = RunReport(course=course, commit=commit)

    # --- Fase A: recolectar y agrupar ---------------------------------------
    exports = fetch_groups(course, creds, work_dir, groups=groups, session=session)
    report.grupos_moodle = tuple(ge.group for ge in exports)
    xlsx_por_cu = split_por_cu(exports, work_dir, course.id, conservar=set(course.item_map))

    uploader = Uploader(course, creds, work_dir, fence_journal=fence_journal)

    # --- Fase B: enrutar ----------------------------------------------------
    destinos = resolve_destinations(course, uploader, xlsx_por_cu)
    report.destinos = tuple(destinos)

    planes: list[tuple[Destination, Path]] = []
    for destino in destinos:
        xlsx = xlsx_por_cu.get(destino.cu)
        if xlsx is None:
            continue
        try:
            plan_path, _ = uploader.plan(destino, [xlsx])
        except MnsyncError as e:
            report.outcomes.append(
                DestinationOutcome(
                    summary=_resumen_vacio(destino),
                    verdict=GuardVerdict(allowed=False),
                    allow_update=allow_update,
                    error=str(e),
                )
            )
            continue
        planes.append((destino, plan_path))

    routing = merge_routing(planes) if planes else Routing()
    routing = _completar_con_moodle(routing, exports)
    report.routing = routing

    veredicto = check_routing(routing)
    report.routing_verdict = veredicto

    # Un patrón que delata códigos de contexto equivocados detiene todo: si
    # ningún estudiante emparejó en ningún lado, el recuento de cambios no
    # significa lo que parece (specs/002, R-09).
    if veredicto.blocked and routing.todo_sin_destino:
        for destino, plan_path in planes:
            report.outcomes.append(
                DestinationOutcome(
                    summary=summarize_plan(plan_path, destino),
                    verdict=GuardVerdict(
                        allowed=False, reason=veredicto.reason, remedy=veredicto.remedy
                    ),
                    escrito=False,
                    allow_update=allow_update,
                )
            )
        return report

    # --- Fase C: escribir ---------------------------------------------------
    for destino, plan_path in planes:
        report.outcomes.append(
            _sync_one_destination(
                uploader,
                destino,
                plan_path,
                commit=commit,
                allow_update=allow_update,
                course=course,
            )
        )

    return report


def _completar_con_moodle(routing: Routing, exports: list[GroupExport]) -> Routing:
    """
    Añade al enrutamiento los estudiantes que ningún plan llegó a mencionar.

    Si un destino no se pudo planificar, sus estudiantes no aparecerían en
    ninguna fila y el discriminador de patrón los pasaría por alto. La verdad
    sobre quién existe la tiene Moodle, no los planes.
    """
    for ge in exports:
        for row in ge.export.rows:
            cedula = (row.get(COL_CEDULA) or "").strip()
            if not cedula:
                continue
            cu = cu_de_institucion(row.get(COL_INSTITUCION) or "")
            if cu:
                routing.por_cu.setdefault(cu, set()).add(cedula)
            nombre = f"{row.get(COL_NOMBRE, '')} {row.get(COL_APELLIDOS, '')}".strip()
            if nombre:
                routing.nombres.setdefault(cedula, nombre)
    return routing


def _sync_one_destination(
    uploader: Uploader,
    destination: Destination,
    plan_path: Path,
    *,
    commit: bool,
    allow_update: bool,
    course: Course,
) -> DestinationOutcome:
    """
    Aplica el plan de un destino.

    Si algo falla, el fallo queda encerrado en este destino: los demás siguen.
    Que un grupo oficial tenga un problema no es razón para dejar los otros sin
    subir.
    """
    summary = summarize_plan(plan_path, destination)
    verdict = check(summary, course.policy)

    if verdict.blocked:
        return DestinationOutcome(
            summary=summary, verdict=verdict, escrito=False, allow_update=allow_update
        )

    try:
        resultados, _ = uploader.apply(
            destination, plan_path, commit=commit, allow_update=allow_update
        )
    except MnsyncError as e:
        return DestinationOutcome(
            summary=summary, verdict=verdict, allow_update=allow_update, error=str(e)
        )

    return DestinationOutcome(
        summary=summary,
        verdict=verdict,
        escrito=commit,
        allow_update=allow_update,
        resultados_path=resultados,
    )


def _resumen_vacio(destination: Destination):
    """Un resumen sin filas, para reportar un destino que ni llegó a planificar."""
    from collections import Counter

    from .guard import PlanSummary

    return PlanSummary(
        destination=destination, total=0, por_accion=Counter(), plan_path=Path("(sin plan)")
    )
