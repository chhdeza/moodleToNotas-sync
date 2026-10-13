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

La frontera entre B y C es además un lugar donde se puede **parar**. Por eso hay
dos funciones y no una: ``preparar`` deja la corrida averiguada y quieta, y
``aplicar`` la ejecuta. La línea de comandos las llama seguidas —eso es
``sync_course``—; la ventana se mete en el medio para enseñar lo que va a pasar
y esperar a que el profesor autorice las sobrescrituras una por una
(specs/003, A-11). Las dos recorren exactamente el mismo camino.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .acciones import SOBRESCRIBIRIA
from .config import Course, Credentials, Destination, MoodleGroupRef
from .errors import ConfigError, MnsyncError, ScopeError
from .guard import (
    GuardVerdict,
    PlanSummary,
    Routing,
    RoutingVerdict,
    check,
    check_routing,
    merge_routing,
    summarize_plan,
)
from .moodle_export import GradeExport, MoodleSession, identidad, write_moodle_xlsx
from .report import DestinationOutcome, RunReport
from .uploader import RunResult, Uploader, escribir_plan_autorizado, leer_plan


@dataclass
class GroupExport:
    """Lo descargado para un grupo de Moodle, antes de tocar Notas Parciales."""

    group: MoodleGroupRef
    export: GradeExport


def fetch_groups(
    course: Course,
    creds: Credentials,
    *,
    groups: list[MoodleGroupRef] | None = None,
    session: MoodleSession | None = None,
) -> list[GroupExport]:
    """
    Descarga las notas de cada grupo de Moodle y verifica que los alcances difieran.

    Una sola sesión de Moodle para todos los grupos. No escribe ningún archivo:
    la corrida trabaja con los archivos por centro universitario
    (``split_por_cu``), y solo ``mnsync fetch`` deja uno por grupo para abrirlo
    en Excel. Un archivo con cédulas que nadie va a leer no tiene por qué
    quedar en el disco.
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
        session = MoodleSession.iniciar(creds)

    exports = [
        GroupExport(g, session.export_group(course.moodle_course_id, g.moodle_group_id))
        for g in objetivo
    ]

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
            est = identidad(row)
            if not est.cedula or not est.cu:
                continue
            # Un estudiante en dos grupos de Moodle se cuenta una sola vez.
            if (est.cu, est.cedula) in vistas:
                continue
            vistas.add((est.cu, est.cedula))
            filas_por_cu.setdefault(est.cu, []).append(row)

    salidas: dict[str, Path] = {}
    for cu, filas in filas_por_cu.items():
        export = GradeExport(headers=list(headers), rows=filas, grade_headers=list(grade_headers))
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
) -> dict[Destination, tuple[Path, RunResult] | None]:
    """
    Los destinos a los que van a parar los estudiantes de esta corrida.

    Si ``courses.yml`` los declara, se usan tal cual —es lo que escribe el
    asistente—. Si no, se descubren sondeando los grupos 1 a 5 de cada centro
    universitario presente, todo con operaciones de lectura (specs/002, R-05).

    Se ignoran los destinos declarados de centros universitarios que esta
    corrida no trajo: consultarlos sería tiempo perdido.

    Cada destino viene con su plan si el sondeo ya lo calculó, o con ``None``
    si todavía hay que pedirlo: ``plan_de`` resuelve los dos casos.
    """
    declarados = {d: None for d in course.destinations if d.cu in xlsx_por_cu}
    return declarados or uploader.discover_destinations(sorted(xlsx_por_cu), xlsx_por_cu)


def plan_de(
    uploader: Uploader,
    destino: Destination,
    sondeado: tuple[Path, RunResult] | None,
    xlsx_por_cu: dict[str, Path],
) -> tuple[Path, RunResult]:
    """El plan de un destino: el que ya dejó el sondeo, o uno nuevo."""
    return sondeado or uploader.plan(destino, [xlsx_por_cu[destino.cu]])


@dataclass(frozen=True)
class FilaPlan:
    """
    Una fila del plan, con su destino puesto, lista para enseñarse.

    Es lo que ve el profesor en la vista de diferencias: un estudiante, un
    instrumento, qué hay ahora y qué habría después. Se separa del ``dict`` que
    devuelve el CSV para que la ventana no tenga que saber cómo se llaman las
    columnas de un archivo del script.
    """

    destino: Destination
    cedula: str
    nombre: str
    instrumento: str
    instrumento_nombre: str
    nota_local: str
    nota_remota: str
    accion: str
    motivo: str

    @property
    def clave(self) -> tuple[str, str]:
        """Lo que identifica a la fila: una nota de una persona (specs/003, A-11)."""
        return (self.cedula, self.instrumento)

    @property
    def es_sobrescritura(self) -> bool:
        return self.accion == SOBRESCRIBIRIA


@dataclass
class Preparacion:
    """
    La corrida detenida justo antes de escribir: todo averiguado, nada tocado.

    Existe porque autorizar fila por fila (specs/003, A-11) exige que alguien
    mire los planes **entre** calcularlos y ejecutarlos. La línea de comandos
    hace las dos cosas seguidas; la ventana se mete en el medio, enseña lo que
    va a pasar y espera. Las dos usan el mismo motor: acá no se decide nada
    nuevo, solo se guarda el resultado de la fase B.
    """

    course: Course
    uploader: Uploader
    exports: list[GroupExport]
    destinos: tuple[Destination, ...]
    #: ``(destino, plan.csv)`` de cada destino que sí llegó a planificarse.
    planes: list[tuple[Destination, Path]]
    routing: Routing
    routing_verdict: RoutingVerdict
    #: Destinos que fallaron al planificar. Ya vienen como resultado.
    fallidos: list[DestinationOutcome] = field(default_factory=list)

    @property
    def detenida(self) -> bool:
        """
        Si el patrón de fallos desaconseja escribir **nada** (specs/002, R-09).

        Cuando ningún estudiante emparejó en ningún lado, el recuento de
        cambios no significa lo que parece, y ofrecer un botón de sincronizar
        sobre esos números sería ofrecer un desastre prolijamente presentado.
        """
        return self.routing_verdict.blocked and self.routing.todo_sin_destino

    def filas(self) -> list[FilaPlan]:
        """Todas las filas de todos los planes, para la vista de diferencias."""
        salida: list[FilaPlan] = []
        for destino, plan_path in self.planes:
            for f in leer_plan(plan_path):
                salida.append(
                    FilaPlan(
                        destino=destino,
                        cedula=f.get("cedula", ""),
                        nombre=f.get("nombre", ""),
                        instrumento=f.get("instrumento", ""),
                        instrumento_nombre=f.get("instrumento_nombre", ""),
                        nota_local=f.get("nota_local", ""),
                        nota_remota=f.get("nota_remota", ""),
                        accion=f.get("accion", ""),
                        motivo=f.get("motivo", ""),
                    )
                )
        return salida


def preparar(
    course: Course,
    creds: Credentials,
    work_dir: Path,
    *,
    groups: list[MoodleGroupRef] | None = None,
    fence_journal: Path | None = None,
    session: MoodleSession | None = None,
) -> Preparacion:
    """
    Fases A y B: recolectar, agrupar y enrutar. **No escribe nada.**

    Todo lo que hace son lecturas y planes, así que se puede llamar tantas
    veces como haga falta sin consecuencias.
    """
    # --- Fase A: recolectar y agrupar ---------------------------------------
    exports = fetch_groups(course, creds, groups=groups, session=session)
    xlsx_por_cu = split_por_cu(exports, work_dir, course.id, conservar=set(course.item_map))

    uploader = Uploader(course, creds, work_dir, fence_journal=fence_journal)

    # --- Fase B: enrutar ----------------------------------------------------
    destinos = resolve_destinations(course, uploader, xlsx_por_cu)

    planes: list[tuple[Destination, Path]] = []
    fallidos: list[DestinationOutcome] = []
    for destino, sondeado in destinos.items():
        try:
            plan_path, _ = plan_de(uploader, destino, sondeado, xlsx_por_cu)
        except MnsyncError as e:
            fallidos.append(
                DestinationOutcome(
                    summary=_resumen_vacio(destino),
                    verdict=GuardVerdict(allowed=False),
                    error=str(e),
                )
            )
            continue
        planes.append((destino, plan_path))

    routing = merge_routing(planes) if planes else Routing()
    routing = _completar_con_moodle(routing, exports)

    return Preparacion(
        course=course,
        uploader=uploader,
        exports=exports,
        destinos=tuple(destinos),
        planes=planes,
        routing=routing,
        routing_verdict=check_routing(routing),
        fallidos=fallidos,
    )


def aplicar(
    prep: Preparacion,
    *,
    commit: bool = False,
    allow_update: bool | None = None,
    autorizadas: set[tuple[str, str]] | None = None,
) -> RunReport:
    """
    Fase C: ejecutar los planes ya calculados.

    ``allow_update`` autoriza **todas** las sobrescrituras de golpe: es lo que
    usa la línea de comandos, donde quien ejecuta ya abrió el plan en Excel.

    ``autorizadas`` autoriza una por una, por ``(cédula, instrumento)``, y es lo
    que usa la ventana (specs/003, A-11). Si se pasa, manda sobre
    ``allow_update``: una lista explícita de permisos no puede quedar ampliada
    por una bandera puesta en otro lado. Pasar un conjunto vacío es una
    respuesta válida —«ninguna»— y distinta de no pasar nada.
    """
    course = prep.course
    if allow_update is None:
        allow_update = course.policy.allow_update
    if autorizadas is not None:
        allow_update = False

    # La marca de ensayo sale de la misma reja que lo hace ensayo: así el
    # reporte no puede decir que escribió mientras la escritura está tapiada.
    report = RunReport(
        course=course, commit=commit, ensayo=prep.uploader.fence_journal is not None
    )
    report.grupos_moodle = tuple(ge.group for ge in prep.exports)
    report.destinos = prep.destinos
    report.routing = prep.routing
    report.routing_verdict = prep.routing_verdict
    report.outcomes.extend(prep.fallidos)

    # Un patrón que delata códigos de contexto equivocados detiene todo: si
    # ningún estudiante emparejó en ningún lado, el recuento de cambios no
    # significa lo que parece (specs/002, R-09).
    if prep.detenida:
        v = prep.routing_verdict
        for destino, plan_path in prep.planes:
            report.outcomes.append(
                DestinationOutcome(
                    summary=summarize_plan(plan_path, destino),
                    verdict=GuardVerdict(allowed=False, reason=v.reason, remedy=v.remedy),
                    escrito=False,
                    allow_update=allow_update,
                )
            )
        return report

    for destino, plan_path in prep.planes:
        report.outcomes.append(
            _sync_one_destination(
                prep.uploader,
                destino,
                plan_path,
                commit=commit,
                allow_update=allow_update,
                autorizadas=autorizadas,
                course=course,
            )
        )

    return report


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

    Es la corrida completa de un tirón: preparar y aplicar sin pausa. Sin
    ``commit=True`` no se escribe nada, que es el modo por defecto heredado del
    script de Notas Parciales.
    """
    prep = preparar(
        course, creds, work_dir, groups=groups, fence_journal=fence_journal, session=session
    )
    return aplicar(prep, commit=commit, allow_update=allow_update)


def _completar_con_moodle(routing: Routing, exports: list[GroupExport]) -> Routing:
    """
    Añade al enrutamiento los estudiantes que ningún plan llegó a mencionar.

    Si un destino no se pudo planificar, sus estudiantes no aparecerían en
    ninguna fila y el discriminador de patrón los pasaría por alto. La verdad
    sobre quién existe la tiene Moodle, no los planes.
    """
    for ge in exports:
        for est in map(identidad, ge.export.rows):
            if not est.cedula:
                continue
            if est.cu:
                routing.por_cu.setdefault(est.cu, set()).add(est.cedula)
            if est.nombre:
                routing.nombres.setdefault(est.cedula, est.nombre)
    return routing


def _sync_one_destination(
    uploader: Uploader,
    destination: Destination,
    plan_path: Path,
    *,
    commit: bool,
    allow_update: bool,
    autorizadas: set[tuple[str, str]] | None,
    course: Course,
) -> DestinationOutcome:
    """
    Aplica el plan de un destino.

    Si algo falla, el fallo queda encerrado en este destino: los demás siguen.
    Que un grupo oficial tenga un problema no es razón para dejar los otros sin
    subir.
    """
    propuesto = summarize_plan(plan_path, destination)

    if autorizadas is None:
        # La línea de comandos: se entrega el plan tal cual, y la bandera vale
        # para todo el archivo.
        ejecutable, aplicado, permitir = plan_path, propuesto, allow_update
    else:
        # Autorizaciones fila por fila: se escribe una copia recortada, y
        # entonces la bandera global ya es exacta, porque solo puede alcanzar a
        # las filas que quedaron adentro (specs/003, A-11). Se enciende
        # únicamente si alguna sobrevivió, para que un plan sin sobrescrituras
        # autorizadas ni siquiera pida el permiso.
        ejecutable = escribir_plan_autorizado(
            plan_path, autorizadas, plan_path.with_name(plan_path.stem + "_autorizado.csv")
        )
        aplicado = summarize_plan(ejecutable, destination)
        permitir = aplicado.sobrescribirian > 0

    # El radio de daño se mide sobre lo que de verdad se va a escribir, no
    # sobre lo que el plan llegó a proponer: una sobrescritura que el profesor
    # no autorizó no cambia ninguna nota, y contarla haría saltar el freno por
    # algo que no va a pasar.
    verdict = check(aplicado, course.policy)

    if verdict.blocked:
        return DestinationOutcome(
            summary=propuesto,
            verdict=verdict,
            escrito=False,
            allow_update=permitir,
            aplicado=aplicado,
        )

    try:
        resultados, _ = uploader.apply(
            destination, ejecutable, commit=commit, allow_update=permitir
        )
    except MnsyncError as e:
        return DestinationOutcome(
            summary=propuesto,
            verdict=verdict,
            allow_update=permitir,
            aplicado=aplicado,
            error=str(e),
        )

    return DestinationOutcome(
        summary=propuesto,
        verdict=verdict,
        escrito=commit,
        allow_update=permitir,
        aplicado=aplicado,
        resultados_path=resultados,
    )


def _resumen_vacio(destination: Destination) -> PlanSummary:
    """Un resumen sin filas, para reportar un destino que ni llegó a planificar."""
    return PlanSummary(
        destination=destination, total=0, por_accion=Counter(), plan_path=Path("(sin plan)")
    )


# ---------------------------------------------------------------------------
# Ensayo
# ---------------------------------------------------------------------------
def ruta_diario(work_dir: Path, course_id: str) -> Path:
    """
    Dónde deja un ensayo lo que interceptó.

    Es el mismo nombre desde la terminal y desde la ventana: lo cubre el
    .gitignore y lo lee «mnsync leer-ensayo» sin tener que enseñar dos patrones.
    """
    return work_dir / f"rehearsal_{course_id}_{datetime.now():%Y%m%d_%H%M}.jsonl"


def contar_interceptadas(diario: Path) -> int:
    """Cuántas escrituras quedaron anotadas en el diario de un ensayo."""
    if not diario.exists():
        return 0
    return sum(1 for ln in diario.read_text(encoding="utf-8").splitlines() if ln.strip())
