"""
Carga y validación de la configuración.

Dos fuentes, deliberadamente separadas:

  - ``.env``        → credenciales. Nunca se commitea, nunca se imprime.
  - ``courses.yml`` → códigos de curso. Sin secretos, seguro de commitear.

La validación es estricta a propósito. Un ``cu``/``grupo`` mal puesto no
produce un error en el sistema de la UNED: produce una corrida que
"funciona" y deja las notas de un grupo entero sin subir, en silencio.
Preferimos negarnos a arrancar antes que fallar así.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError

# Valor por defecto del servidor real. Se puede redirigir a un servidor
# falso local durante las pruebas mediante NP_BASE_URL.
NP_BASE_URL_DEFAULT = "https://produccion.uned.ac.cr/notasparciales"

# El Moodle de la UNED. Es una constante del programa, no algo que se deduzca
# de datos del usuario: es la dirección a la que se le va a mandar una
# contraseña, y adivinarla a partir de una entrada cualquiera sería regalarla.
# `mnsync doctor` siempre muestra cuál quedó en uso.
MOODLE_URL_DEFAULT = "https://aprende.uned.ac.cr"


# ---------------------------------------------------------------------------
# Credenciales
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Credentials:
    """
    Quién es el profesor y con qué entra. Nunca se registra en logs ni reportes.

    ``np_tutor`` es su cédula. No es un secreto —el sistema de la UNED la
    muestra en un menú— pero sí es un dato personal, y vive acá y no en
    ``courses.yml`` por eso: la configuración se comparte y se commitea, y lo
    que entra al historial de git no sale nunca (specs/002, R-15).
    """

    moodle_url: str
    moodle_username: str
    moodle_password: str
    np_user: str
    np_password: str
    np_tutor: str = ""
    np_base_url: str = NP_BASE_URL_DEFAULT

    @property
    def np_is_real_server(self) -> bool:
        """True si apuntamos al sistema real de la UNED (no a un servidor de prueba)."""
        return self.np_base_url.rstrip("/") == NP_BASE_URL_DEFAULT

    def __repr__(self) -> str:  # pragma: no cover - trivial
        # Defensa en profundidad: que un repr accidental en un log o en un
        # traceback nunca revele una contraseña.
        return (
            f"Credentials(moodle_url={self.moodle_url!r}, "
            f"moodle_username={self.moodle_username!r}, moodle_password='***', "
            f"np_user={self.np_user!r}, np_password='***', "
            f"np_tutor={self.np_tutor!r}, "
            f"np_base_url={self.np_base_url!r})"
        )


def load_credentials(env_path: Path | None = None, *, require: bool = True) -> Credentials:
    """
    Arma las credenciales a partir de los tres orígenes posibles.

    En este orden de precedencia, y por una razón en cada escalón:

    1. **Variables de entorno.** Es como llegan los secretos en GitHub Actions,
       y tienen que ganarle a cualquier cosa que haya en el disco del runner.
    2. **El archivo ``.env``.** El camino de quien desarrolla.
    3. **El Administrador de credenciales de Windows.** El de la aplicación de
       escritorio, y el único de los tres que cifra en reposo.

    Con ``require=False`` no exige que estén completas: sirve para que
    ``mnsync doctor`` pueda decir *cuáles* faltan en vez de morir en la
    primera.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependencia declarada
        raise ConfigError(
            "Falta la librería python-dotenv.",
            remedio="Ejecutá:  pip install -e .",
        ) from None

    path = env_path or Path(".env")
    if path.exists():
        load_dotenv(path, override=False)

    def get(name: str) -> str:
        return (os.environ.get(name) or "").strip()

    guardadas = _credenciales_guardadas()

    def con_respaldo(name: str, respaldo: str) -> str:
        """Lo del entorno, y si no hay, lo del almacén del sistema."""
        return get(name) or respaldo

    creds = Credentials(
        moodle_url=(get("MOODLE_URL") or MOODLE_URL_DEFAULT).rstrip("/"),
        moodle_username=con_respaldo("MOODLE_USERNAME", guardadas["moodle_username"]),
        moodle_password=con_respaldo("MOODLE_PASSWORD", guardadas["moodle_password"]),
        np_user=con_respaldo("NP_NTLM_USER", guardadas["np_user"]),
        np_password=con_respaldo("NP_NTLM_PASSWORD", guardadas["np_password"]),
        np_tutor=con_respaldo("NP_TUTOR", guardadas["np_tutor"]),
        np_base_url=(get("NP_BASE_URL") or NP_BASE_URL_DEFAULT).rstrip("/"),
    )

    if require:
        faltan = missing_credentials(creds)
        if faltan:
            raise ConfigError(
                "Faltan credenciales en el archivo .env: " + ", ".join(faltan),
                remedio=(
                    f"Abrí «{path}» y completá esos valores. "
                    "Si el archivo no existe, copiá «.env.example» como «.env»."
                ),
            )
    return creds


def _credenciales_guardadas() -> dict[str, str]:
    """
    Lo que haya en el almacén del sistema, o cadenas vacías.

    Se consulta una sola vez por carga y nunca levanta: si no hay almacén, las
    credenciales vienen de los otros dos orígenes y nadie se entera.
    """
    from . import credstore

    moodle = credstore.leer(credstore.SERVICIO_MOODLE)
    np = credstore.leer(credstore.SERVICIO_NP)
    return {
        "moodle_username": moodle.username if moodle else "",
        "moodle_password": moodle.password if moodle else "",
        "np_user": np.username if np else "",
        "np_password": np.password if np else "",
        "np_tutor": credstore.leer_tutor(),
    }


def _credential_fields(creds: Credentials) -> list[tuple[str, str]]:
    """Pares (nombre de variable, valor) de lo que es obligatorio."""
    return [
        ("MOODLE_URL", creds.moodle_url),
        ("MOODLE_USERNAME", creds.moodle_username),
        ("MOODLE_PASSWORD", creds.moodle_password),
        ("NP_NTLM_USER", creds.np_user),
        ("NP_NTLM_PASSWORD", creds.np_password),
        ("NP_TUTOR", creds.np_tutor),
    ]


def missing_credentials(creds: Credentials) -> list[str]:
    """Nombres de las variables del .env que están vacías."""
    return [name for name, value in _credential_fields(creds) if not value]


# ---------------------------------------------------------------------------
# courses.yml
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MoodleGroupRef:
    """
    Un grupo de Moodle que imparte el profesor.

    Es un **ámbito de recolección**, no un destino: dice *de quién* son los
    estudiantes que hay que bajar. Una vez recolectados, el grupo del que
    vinieron no influye en dónde se escriben sus notas (specs/001, D-11).
    """

    moodle_group_id: int
    name: str = ""

    @property
    def label(self) -> str:
        """Etiqueta legible para reportes."""
        return self.name or f"grupo de Moodle {self.moodle_group_id}"

    @property
    def slug(self) -> str:
        """Fragmento seguro para nombres de archivo."""
        return f"g{self.moodle_group_id}"


@dataclass(frozen=True)
class Destination:
    """
    Un destino en Notas Parciales: el par ``(cu, grupo)``.

    Es la **unidad de escritura**. Un centro universitario puede tener varios
    (specs/001, D-04), así que un CU por sí solo no identifica un destino.

    No lleva cédulas ni ningún dato personal: qué estudiante va a qué destino se
    resuelve contra el servidor en cada corrida y no se persiste (specs/002, R-15).
    """

    cu: str
    grupo: int

    @property
    def label(self) -> str:
        return f"CU {self.cu} / grupo {self.grupo}"

    @property
    def slug(self) -> str:
        return f"cu{self.cu}_gr{self.grupo}"

    @property
    def key(self) -> tuple[str, int]:
        return (self.cu, self.grupo)


@dataclass(frozen=True)
class Policy:
    """Reglas de seguridad por curso."""

    allow_update: bool = False
    justificacion_codigo: int = 2005
    max_changes: int = 40

    @property
    def blast_radius_enabled(self) -> bool:
        return self.max_changes > 0


@dataclass(frozen=True)
class NotasParcialesCtx:
    """Códigos que identifican la pantalla de Captura de Notas (sin cu/grupo)."""

    ano: str
    pac: str
    asignatura: str
    escuela: str
    catedra: int
    encargado: str
    modelo: int
    tipo: str = "O"


@dataclass(frozen=True)
class Course:
    """
    Un curso de Moodle, los grupos que de él imparte el profesor, y los destinos
    de Notas Parciales a los que van a parar sus estudiantes.

    Las dos listas son independientes y de tamaños distintos: un grupo de Moodle
    alimenta varios destinos, y un destino se alimenta de varios grupos
    (specs/001, D-02 y D-11).
    """

    id: str
    moodle_course_id: int
    np: NotasParcialesCtx
    groups: tuple[MoodleGroupRef, ...]
    #: Vacío significa «descubrirlos sondeando» (specs/002, R-05).
    destinations: tuple[Destination, ...] = ()
    item_map: dict[str, str] = field(default_factory=dict)
    policy: Policy = field(default_factory=Policy)

    def group_by_moodle_id(self, moodle_group_id: int) -> MoodleGroupRef:
        for g in self.groups:
            if g.moodle_group_id == moodle_group_id:
                return g
        disponibles = ", ".join(str(g.moodle_group_id) for g in self.groups)
        raise ConfigError(
            f"El curso «{self.id}» no tiene configurado el grupo {moodle_group_id}.",
            remedio=f"Los grupos configurados son: {disponibles}",
        )


@dataclass(frozen=True)
class Config:
    """El archivo courses.yml entero, ya validado."""

    courses: tuple[Course, ...]
    source: Path | None = None

    def course(self, course_id: str) -> Course:
        for c in self.courses:
            if c.id == course_id:
                return c
        disponibles = ", ".join(c.id for c in self.courses) or "(ninguno)"
        raise ConfigError(
            f"No hay ningún curso con id «{course_id}» en la configuración.",
            remedio=f"Los cursos configurados son: {disponibles}",
        )


# --- helpers de lectura estricta -------------------------------------------


def _req(data: dict[str, Any], key: str, where: str) -> Any:
    if key not in data or data[key] is None or data[key] == "":
        raise ConfigError(
            f"Falta «{key}» en {where}.",
            remedio="Mirá «courses.example.yml»: ahí está explicado campo por campo.",
        )
    return data[key]


def _as_int(value: Any, key: str, where: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ConfigError(
            f"«{key}» en {where} debe ser un número entero, pero dice «{value}»."
        ) from None


def _as_str(value: Any) -> str:
    """
    Convierte a texto preservando ceros a la izquierda.

    Importa: el CU «01» y la asignatura «00883» son cadenas. Si YAML los
    interpretara como números, «01» se volvería «1» y el servidor devolvería
    una tabla vacía sin dar ningún error.
    """
    return str(value).strip()


def _parse_moodle_group(raw: Any, *, course_id: str, index: int) -> MoodleGroupRef:
    """
    Lee una entrada de ``moodle.groups``.

    Se acepta tanto el número suelto como el bloque con «id» y «name»: el
    asistente escribe la forma larga, pero a mano la corta es más cómoda.
    """
    where = f"el grupo #{index + 1} del curso «{course_id}»"

    if isinstance(raw, (int, str)) and not isinstance(raw, bool):
        return MoodleGroupRef(moodle_group_id=_as_int(raw, "id", where))

    if not isinstance(raw, dict):
        raise ConfigError(f"{where} debería ser un número o un bloque con «id», no «{raw!r}».")

    return MoodleGroupRef(
        moodle_group_id=_as_int(_req(raw, "id", where), "id", where),
        name=_as_str(raw.get("name") or ""),
    )


def _parse_destination(raw: Any, *, course_id: str, index: int) -> Destination:
    where = f"el destino #{index + 1} del curso «{course_id}»"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} debería ser un bloque con «cu» y «grupo», no «{raw!r}».")
    return Destination(
        cu=_as_str(_req(raw, "cu", where)),
        grupo=_as_int(_req(raw, "grupo", where), "grupo", where),
    )


def _validate_moodle_groups(groups: tuple[MoodleGroupRef, ...], *, course_id: str) -> None:
    """Un grupo de Moodle repetido descargaría dos veces a los mismos estudiantes."""
    vistos: set[int] = set()
    for g in groups:
        if g.moodle_group_id in vistos:
            raise ConfigError(
                f"En el curso «{course_id}», el grupo de Moodle {g.moodle_group_id} "
                "está configurado dos veces.",
                remedio="Dejá una sola entrada por cada grupo de Moodle.",
            )
        vistos.add(g.moodle_group_id)


def _validate_destinations(destinations: tuple[Destination, ...], *, course_id: str) -> None:
    """
    Un destino repetido significa escribir dos veces sobre el mismo grupo oficial.

    Que un mismo CU aparezca con grupos distintos es normal y esperado
    (specs/001, D-04); lo que no puede repetirse es el par completo.
    """
    vistos: set[tuple[str, int]] = set()
    for d in destinations:
        if d.key in vistos:
            raise ConfigError(
                f"En el curso «{course_id}», el destino {d.label} está configurado dos veces.",
                remedio=(
                    "Dejá una sola entrada por cada par de centro universitario y grupo. "
                    "Que el mismo CU aparezca con grupos distintos sí es correcto."
                ),
            )
        vistos.add(d.key)


def _parse_course(raw: Any, index: int) -> Course:
    if not isinstance(raw, dict):
        raise ConfigError(f"El curso #{index + 1} debería ser una lista de campos, no «{raw!r}».")

    course_id = _as_str(_req(raw, "id", f"el curso #{index + 1}"))
    where = f"el curso «{course_id}»"

    moodle = _req(raw, "moodle", where)
    if not isinstance(moodle, dict):
        raise ConfigError(f"«moodle» en {where} debería tener campos adentro.")
    moodle_course_id = _as_int(
        _req(moodle, "course_id", f"la sección «moodle» de {where}"), "course_id", where
    )

    np_raw = _req(raw, "notas_parciales", where)
    if not isinstance(np_raw, dict):
        raise ConfigError(f"«notas_parciales» en {where} debería tener campos adentro.")
    np_where = f"la sección «notas_parciales» de {where}"
    np = NotasParcialesCtx(
        ano=_as_str(_req(np_raw, "ano", np_where)),
        pac=_as_str(_req(np_raw, "pac", np_where)),
        asignatura=_as_str(_req(np_raw, "asignatura", np_where)),
        escuela=_as_str(_req(np_raw, "escuela", np_where)),
        catedra=_as_int(_req(np_raw, "catedra", np_where), "catedra", np_where),
        encargado=_as_str(_req(np_raw, "encargado", np_where)),
        modelo=_as_int(_req(np_raw, "modelo", np_where), "modelo", np_where),
        tipo=_as_str(np_raw.get("tipo") or "O"),
    )

    # Los grupos del tutor viven bajo «moodle:», que es donde pertenecen.
    #
    # Se admite que la lista esté vacía, y a propósito: el comando que sirve
    # para averiguar los números de grupo necesita poder leer el archivo antes
    # de que estén escritos. Los comandos que sí los necesitan se quejan al
    # usarlos, no acá.
    groups_raw = moodle.get("groups") or []
    if not isinstance(groups_raw, list):
        raise ConfigError(
            f"«moodle.groups» en {where} debería ser una lista de grupos.",
            remedio="Mirá «courses.example.yml»: ahí está explicado campo por campo.",
        )
    groups = tuple(
        _parse_moodle_group(g, course_id=course_id, index=i) for i, g in enumerate(groups_raw)
    )
    _validate_moodle_groups(groups, course_id=course_id)

    # Los destinos son opcionales: si faltan, se descubren sondeando el servidor
    # (specs/002, R-05). El asistente los escribe; a mano se pueden omitir.
    destinos_raw = raw.get("destinos") or []
    if not isinstance(destinos_raw, list):
        raise ConfigError(
            f"«destinos» en {where} debería ser una lista de bloques con «cu» y «grupo»."
        )
    destinations = tuple(
        _parse_destination(d, course_id=course_id, index=i) for i, d in enumerate(destinos_raw)
    )
    _validate_destinations(destinations, course_id=course_id)

    pol_raw = raw.get("policy") or {}
    if not isinstance(pol_raw, dict):
        raise ConfigError(f"«policy» en {where} debería tener campos adentro.")
    policy = Policy(
        allow_update=bool(pol_raw.get("allow_update", False)),
        justificacion_codigo=_as_int(
            pol_raw.get("justificacion_codigo", 2005), "justificacion_codigo", where
        ),
        max_changes=_as_int(pol_raw.get("max_changes", 40), "max_changes", where),
    )
    if policy.max_changes < 0:
        raise ConfigError(f"«max_changes» en {where} no puede ser negativo.")
    if policy.allow_update and not policy.justificacion_codigo:
        raise ConfigError(
            f"{where} permite sobrescribir notas pero no tiene «justificacion_codigo».",
            remedio="Agregá  justificacion_codigo: 2005  (= «Error de digitación»).",
        )

    item_map_raw = raw.get("item_map") or {}
    if not isinstance(item_map_raw, dict):
        raise ConfigError(f"«item_map» en {where} debería ser una lista de «columna: código».")
    item_map = {_as_str(k): _as_str(v) for k, v in item_map_raw.items()}

    return Course(
        id=course_id,
        moodle_course_id=moodle_course_id,
        np=np,
        groups=groups,
        destinations=destinations,
        item_map=item_map,
        policy=policy,
    )


def load_config(path: Path | None = None) -> Config:
    """Lee y valida ``courses.yml``."""
    try:
        import yaml
    except ImportError:  # pragma: no cover - dependencia declarada
        raise ConfigError(
            "Falta la librería PyYAML.",
            remedio="Ejecutá:  pip install -e .",
        ) from None

    p = path or Path("courses.yml")
    if not p.exists():
        raise ConfigError(
            f"No se encontró el archivo de configuración «{p}».",
            remedio=(
                "Copiá «courses.example.yml» como «courses.yml» y editalo con "
                "los datos de tus cursos."
            ),
        )

    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(
            f"El archivo «{p}» tiene un error de formato y no se pudo leer.\n{e}",
            remedio=(
                "Suele ser un problema de sangría o un «:» faltante. Compará con "
                "«courses.example.yml»."
            ),
        ) from e

    if not isinstance(data, dict) or "courses" not in data:
        raise ConfigError(
            f"El archivo «{p}» no tiene una sección «courses:» en el nivel principal.",
            remedio="Mirá «courses.example.yml» para ver la estructura esperada.",
        )

    courses_raw = data.get("courses") or []
    if not isinstance(courses_raw, list) or not courses_raw:
        raise ConfigError(f"El archivo «{p}» no tiene ningún curso configurado.")

    courses = tuple(_parse_course(c, i) for i, c in enumerate(courses_raw))

    vistos: set[str] = set()
    for c in courses:
        if c.id in vistos:
            raise ConfigError(
                f"Hay dos cursos con el mismo id «{c.id}».",
                remedio="El «id» es el apodo con el que llamás al curso: tiene que ser único.",
            )
        vistos.add(c.id)

    return Config(courses=courses, source=p)


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------
def course_to_dict(course: Course) -> dict[str, Any]:
    """
    Un curso, en la forma exacta que vuelve a leer ``load_config``.

    Lo que **no** aparece acá es tan importante como lo que sí: ni credenciales,
    ni la cédula del tutor, ni una sola cédula de estudiante. Este archivo se
    comparte y se sube al repositorio para que el flujo automático lo use, y lo
    que entra al historial de git no sale nunca (specs/002, R-15).

    El enrutamiento individual no se guarda: se recalcula contra el servidor en
    cada corrida. Solo se conserva la lista de destinos, que no es un dato
    personal de nadie.
    """
    datos: dict[str, Any] = {
        "id": course.id,
        "moodle": {
            "course_id": course.moodle_course_id,
            "groups": [
                {"id": g.moodle_group_id, **({"name": g.name} if g.name else {})}
                for g in course.groups
            ],
        },
        "notas_parciales": {
            "ano": course.np.ano,
            "pac": course.np.pac,
            "tipo": course.np.tipo,
            "asignatura": course.np.asignatura,
            "escuela": course.np.escuela,
            "catedra": course.np.catedra,
            "encargado": course.np.encargado,
            "modelo": course.np.modelo,
        },
    }
    if course.destinations:
        datos["destinos"] = [
            {"cu": d.cu, "grupo": d.grupo} for d in course.destinations
        ]
    if course.item_map:
        datos["item_map"] = dict(course.item_map)
    datos["policy"] = {
        "allow_update": course.policy.allow_update,
        "justificacion_codigo": course.policy.justificacion_codigo,
        "max_changes": course.policy.max_changes,
    }
    return datos


def write_config(courses: tuple[Course, ...] | list[Course], path: Path) -> Path:
    """
    Escribe ``courses.yml``. Es lo que produce el asistente al terminar.

    Se conserva el orden de los campos y se fuerzan las comillas en los códigos
    que llevan ceros a la izquierda: si «01» se guardara como número, volvería
    como «1» y el servidor devolvería tablas vacías sin dar ningún error.
    """
    try:
        import yaml
    except ImportError:  # pragma: no cover - dependencia declarada
        raise ConfigError(
            "Falta la librería PyYAML.",
            remedio="Ejecutá:  pip install -e .",
        ) from None

    cuerpo = yaml.safe_dump(
        {"courses": [course_to_dict(c) for c in courses]},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )

    encabezado = (
        "# Generado por mnsync. Se puede editar a mano.\n"
        "#\n"
        "# NO lleva contraseñas, ni tu cédula, ni las de tus estudiantes:\n"
        "# eso vive aparte, con las credenciales.\n\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encabezado + cuerpo, encoding="utf-8")
    return path
