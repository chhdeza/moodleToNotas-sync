"""
Averiguaciones previas a sincronizar, sin escribir nada.

Es el cerebro del asistente de configuración, escrito aparte de la interfaz y
sin imprimir nada: cada función devuelve datos, y quien los muestre decide cómo
(specs/003, etapa 2). Así la lógica se puede probar contra el servidor falso sin
un solo píxel de por medio, y la línea de comandos y la ventana enseñan
exactamente lo mismo.

Hay dos preguntas que el profesor necesita responder antes de subir nada:

1. **¿Son correctos los nueve códigos de la asignatura?** El sistema de la UNED
   no da error cuando no lo son: devuelve listas vacías. Sin comprobarlo, uno se
   entera semanas después (specs/003, A-05).
2. **¿Qué columna de Moodle corresponde a qué instrumento?** El programa lo
   propone solo, pero la última palabra es del profesor (A-07).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import Course, Credentials, Destination, MoodleGroupRef
from .errors import MnsyncError
from .moodle_export import Estudiante, MoodleSession, identidad
from .sync import fetch_groups, plan_de, resolve_destinations, split_por_cu
from .uploader import Uploader

#: Cómo el script anuncia el emparejamiento de cada columna.
#:
#:     'Tarea 1 (Real)'
#:       -> Tar1 (Tarea 1 (2))
#:       -> SIN MAPEO (no se subirá esta columna)
_RE_MAPEO = re.compile(
    r"^\s*'(?P<columna>[^']*)'\s*\n\s*->\s*(?P<destino>.+?)\s*$",
    re.MULTILINE,
)
_RE_INSTRUMENTO = re.compile(r"^(?P<codigo>\S+)\s*\((?P<nombre>.*)\)\s*$")


# ---------------------------------------------------------------------------
# Emparejamiento de instrumentos
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InstrumentMatch:
    """Una columna de Moodle y el instrumento al que va a parar."""

    columna: str
    codigo: str = ""
    nombre: str = ""

    @property
    def emparejada(self) -> bool:
        return bool(self.codigo)

    @property
    def descripcion(self) -> str:
        if not self.emparejada:
            return "sin emparejar — no se subirá"
        return f"{self.codigo} ({self.nombre})" if self.nombre else self.codigo


def parse_instrument_matches(salida: str) -> list[InstrumentMatch]:
    """
    Lee el emparejamiento que el script anuncia al planificar.

    El script ya resuelve esto —con tres pases, incluido el ``item_map``
    explícito— así que se le pregunta a él en vez de reimplementarlo y arriesgar
    que la ventana muestre un emparejamiento distinto del que se va a usar.
    """
    matches: list[InstrumentMatch] = []
    for m in _RE_MAPEO.finditer(salida or ""):
        columna = m.group("columna")
        destino = m.group("destino")
        if destino.upper().startswith("SIN MAPEO"):
            matches.append(InstrumentMatch(columna=columna))
            continue
        detalle = _RE_INSTRUMENTO.match(destino)
        if detalle:
            matches.append(
                InstrumentMatch(
                    columna=columna,
                    codigo=detalle.group("codigo"),
                    nombre=detalle.group("nombre"),
                )
            )
        else:
            matches.append(InstrumentMatch(columna=columna, codigo=destino))
    return matches


# ---------------------------------------------------------------------------
# Verificación de los códigos de la asignatura
# ---------------------------------------------------------------------------
@dataclass
class ContextCheck:
    """
    Lo que se sabe después de probar los nueve códigos contra el servidor.

    ``ok`` significa que al menos un estudiante apareció en el sistema oficial.
    Es la única prueba posible de que los códigos son los correctos: el servidor
    responde igual de bien a una asignatura que no existe.
    """

    ok: bool
    mensaje: str
    remedio: str = ""
    estudiantes: int = 0
    ubicados: int = 0
    destinos: tuple[Destination, ...] = ()
    cus: tuple[str, ...] = ()
    instrumentos: tuple[InstrumentMatch, ...] = ()
    sin_destino: tuple[tuple[str, str, str], ...] = ()
    #: Destinos por centro universitario, para mostrarlos agrupados.
    por_cu: dict[str, list[Destination]] = field(default_factory=dict)
    #: ``(cu, grupo)`` → cuántos estudiantes van a ese destino.
    estudiantes_en: dict[tuple[str, int], int] = field(default_factory=dict)

    @property
    def columnas_sin_emparejar(self) -> tuple[InstrumentMatch, ...]:
        return tuple(i for i in self.instrumentos if not i.emparejada)

    @property
    def resumen(self) -> str:
        """La línea que confirma que los códigos sirven."""
        if not self.ok:
            return self.mensaje
        return (
            f"{self.ubicados} estudiante(s) en {len(self.destinos)} grupo(s) "
            f"de Notas Parciales, de {len(self.cus)} centro(s) universitario(s)"
        )


def verificar_contexto(
    course: Course,
    creds: Credentials,
    work_dir: Path,
    *,
    groups: list[MoodleGroupRef] | None = None,
    session: MoodleSession | None = None,
) -> ContextCheck:
    """
    Comprueba los códigos de la asignatura contra el servidor real, sin escribir.

    Baja los grupos de Moodle, pregunta al sistema oficial dónde está cada
    estudiante y cuenta. Si no aparece nadie, los códigos están mal: el servidor
    nunca lo dice: hay que deducirlo del silencio (specs/003, A-05).

    Devuelve además el emparejamiento de columnas propuesto, porque se obtiene
    de la misma consulta y el asistente lo necesita en el paso siguiente.
    """
    try:
        exports = fetch_groups(course, creds, groups=groups, session=session)
    except MnsyncError as e:
        return ContextCheck(ok=False, mensaje=e.mensaje, remedio=e.remedio or "")

    estudiantes = sum(len(ge.export) for ge in exports)
    if not estudiantes:
        return ContextCheck(
            ok=False,
            mensaje="Los grupos de Moodle no trajeron ningún estudiante.",
            remedio=(
                "Revisá que los grupos seleccionados sean los tuyos y que tengan "
                "estudiantes matriculados."
            ),
        )

    xlsx_por_cu = split_por_cu(exports, work_dir, course.id, conservar=set(course.item_map))
    cus = tuple(sorted(xlsx_por_cu))

    uploader = Uploader(course, creds, work_dir)
    destinos = resolve_destinations(course, uploader, xlsx_por_cu)

    if not destinos:
        return ContextCheck(
            ok=False,
            mensaje=(
                f"Ninguno de los {estudiantes} estudiantes apareció en Notas Parciales, "
                f"en ninguno de los {len(cus)} centros universitarios."
            ),
            remedio=(
                "Cuando falla todo a la vez, el problema no es la matrícula: son los "
                "códigos con los que se consultó. Revisá «asignatura», «modelo», «pac» "
                "y «ano» contra los menús de la página de Captura de Notas, y volvé al "
                "primer paso a revisar tu cédula: con ella el sistema decide cuáles "
                "grupos son tuyos. El sistema no da error cuando alguno no corresponde: "
                "devuelve listas vacías."
            ),
            estudiantes=estudiantes,
            cus=cus,
        )

    ubicadas: set[str] = set()
    instrumentos: tuple[InstrumentMatch, ...] = ()
    por_cu: dict[str, list[Destination]] = {}
    estudiantes_en: dict[tuple[str, int], int] = {}

    for destino, sondeado in destinos.items():
        plan_path, res = plan_de(uploader, destino, sondeado, xlsx_por_cu)
        propias = uploader.cedulas_del_plan(plan_path)
        ubicadas |= propias
        estudiantes_en[destino.key] = len(propias)
        por_cu.setdefault(destino.cu, []).append(destino)
        # El emparejamiento es del curso, no del destino: alcanza con leerlo
        # una vez, y se toma la primera lectura que traiga algo.
        if not instrumentos:
            instrumentos = tuple(parse_instrument_matches(res.salida()))

    # Se cuenta a todos los que bajaron de Moodle, incluso a quien no trae
    # centro universitario: no va a subir a ningún lado, y por eso mismo tiene
    # que aparecer en la lista.
    por_cedula = _por_cedula(exports)
    huerfanas = sorted(set(por_cedula) - ubicadas)

    return ContextCheck(
        ok=True,
        mensaje="Los códigos de la asignatura son correctos.",
        estudiantes=estudiantes,
        ubicados=len(ubicadas),
        destinos=tuple(destinos),
        cus=cus,
        instrumentos=instrumentos,
        sin_destino=tuple((c, por_cedula[c].nombre, por_cedula[c].cu) for c in huerfanas),
        por_cu=por_cu,
        estudiantes_en=estudiantes_en,
    )


def _por_cedula(exports) -> dict[str, Estudiante]:
    """Cada cédula descargada, con la primera fila en la que apareció."""
    out: dict[str, Estudiante] = {}
    for ge in exports:
        for est in map(identidad, ge.export.rows):
            if est.cedula:
                out.setdefault(est.cedula, est)
    return out
