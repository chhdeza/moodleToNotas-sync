"""
Puente hacia ``notasparciales_upload.py``.

Se lo trata como una **caja negra con contrato de archivos**: entra un xlsx
con formato de exportación de Moodle, sale un ``plan.csv`` y luego un
``resultados.csv``. No se importan sus funciones internas.

Esa decisión es deliberada. El motor de planificación de ese script vive en
funciones privadas (``_build_plan``, ``_fetch_server_state``) que pueden
cambiar sin aviso; y, sobre todo, entrar por la puerta del CLI conserva
intactas sus cuatro capas de seguridad —prueba por defecto, ``--allow-update``
obligatorio para sobrescribir, justificación registrada y verificación
posterior— en vez de sortearlas.

Una sola invocación por grupo: cada grupo tiene su propio plan y sus propios
resultados, para que el alcance de cada escritura quede demostrable y el
fallo de un grupo no contamine a los demás.
"""

from __future__ import annotations

import csv
import os
import re
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .config import Course, Credentials, Destination
from .errors import UploaderError

#: Acciones del plan que implican escribir en el sistema de la UNED.
ACCIONES_DE_ESCRITURA = frozenset({"upload", "mark_not_presented", "would_overwrite"})

#: La acción con la que el script marca a quien no está en el roster consultado.
ACCION_SIN_ROSTER = "skip_not_in_roster"

#: La acción de una nota que **ya estaba puesta** y quedaría distinta.
#:
#: Es la única que exige permiso explícito, y en la aplicación de escritorio ese
#: permiso se da fila por fila (specs/003, A-11).
ACCION_SOBRESCRIBIRIA = "would_overwrite"

#: Hasta qué número de grupo se sondea al descubrir destinos (specs/001, D-09).
MAX_GRUPO_SONDEO = 5

#: «DESAMPARADOS (42)» → «42» (specs/001, D-08).
_RE_INSTITUCION = re.compile(r"\((\d{1,3})\)\s*$")


@dataclass
class RunResult:
    """Lo que dejó una corrida del script."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def salida(self) -> str:
        return (self.stdout or "") + (("\n" + self.stderr) if self.stderr else "")


class Uploader:
    """Ejecuta el script de Notas Parciales para un curso concreto."""

    def __init__(
        self,
        course: Course,
        creds: Credentials,
        work_dir: Path,
        *,
        fence_journal: Path | None = None,
        timeout: float = 900.0,
    ):
        self.course = course
        self.creds = creds
        self.work_dir = work_dir
        self.fence_journal = fence_journal
        self.timeout = timeout
        self.work_dir.mkdir(parents=True, exist_ok=True)

    # --- construcción de argumentos --------------------------------------
    def _context_args(self) -> list[str]:
        np = self.course.np
        return [
            "--ano", np.ano,
            "--pac", np.pac,
            "--tipo", np.tipo,
            "--asignatura", np.asignatura,
            "--escuela", np.escuela,
            "--catedra", str(np.catedra),
            "--encargado", np.encargado,
            "--tutor", self.creds.np_tutor,
            "--modelo", str(np.modelo),
        ]

    def _env(self) -> dict[str, str]:
        """
        Entorno del subproceso.

        Las credenciales viajan por variables de entorno, nunca por la línea
        de comandos: un argumento es visible para cualquier proceso de la
        máquina que liste procesos.
        """
        env = dict(os.environ)
        env.update(
            {
                "NP_NTLM_USER": self.creds.np_user,
                "NP_NTLM_PASSWORD": self.creds.np_password,
                "NP_BASE_URL": self.creds.np_base_url,
                "PYTHONIOENCODING": "utf-8",
            }
        )
        src = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")

        if self.fence_journal is not None:
            env["MNSYNC_FENCE_WRITES"] = "1"
            env["MNSYNC_FENCE_JOURNAL"] = str(self.fence_journal)
        else:
            env.pop("MNSYNC_FENCE_WRITES", None)
        return env

    def _run(self, args: list[str]) -> RunResult:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "mnsync._uploader_shim", *args],
                cwd=str(self.work_dir),
                env=self._env(),
                capture_output=True,
                check=False,  # el codigo de salida se revisa abajo
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise UploaderError(
                f"El script de Notas Parciales no respondió en {self.timeout:.0f} segundos.",
                remedio="Puede ser la red o el servidor de la UNED. Probá de nuevo más tarde.",
            ) from e
        return RunResult(proc.returncode, proc.stdout or "", proc.stderr or "")

    # --- modos -----------------------------------------------------------
    def plan(self, destination: Destination, xlsx_paths: Sequence[Path]) -> tuple[Path, RunResult]:
        """
        Genera el plan de **un destino**, sobre el conjunto completo de xlsx.

        Se le pasan todos los archivos del tutor a la vez, y un solo
        ``--cu-grupo``. Así el script resuelve, con su propio emparejamiento
        contra el roster oficial, cuáles de todos esos estudiantes pertenecen a
        este destino: los demás quedan como ``skip_not_in_roster`` y los recoge
        el plan de su propio destino (specs/002, R-01).

        El ``--cu-grupo`` va explícito, de modo que el script nunca recurre a su
        autodetección, que devuelve un solo grupo por centro universitario y
        perdería a los estudiantes del segundo (specs/001, D-04).
        """
        salida = self.work_dir / f"notas_plan_{destination.slug}.csv"
        args = ["plan", *self._context_args()]
        for x in xlsx_paths:
            args += ["--xlsx", str(x)]
        args += [
            "--cu-grupo", f"{destination.cu}={destination.grupo}",
            "--output", str(salida),
        ]
        for header, codigo in self.course.item_map.items():
            args += ["--map", f"{header}={codigo}"]

        res = self._run(args)
        if not res.ok or not salida.exists():
            raise UploaderError(
                f"No se pudo generar el plan del destino «{destination.label}».",
                remedio=_pista_de_error(res.salida()),
            )

        _recortar_al_destino(salida, destination)
        return salida, res

    def apply(
        self,
        destination: Destination,
        plan_path: Path,
        *,
        commit: bool,
        allow_update: bool,
    ) -> tuple[Path | None, RunResult]:
        """Ejecuta el plan de un destino. Sin ``commit=True`` no escribe nada."""
        args = ["apply", *self._context_args(), "--plan", str(plan_path)]
        args.append("--commit" if commit else "--dry-run")

        if allow_update:
            args += [
                "--allow-update",
                "--justificacion-codigo",
                str(self.course.policy.justificacion_codigo),
            ]

        res = self._run(args)
        if not res.ok:
            raise UploaderError(
                f"Falló la carga de notas del destino «{destination.label}».",
                remedio=_pista_de_error(res.salida()),
            )

        resultados = plan_path.with_name(plan_path.stem + "_resultados.csv")
        return (resultados if resultados.exists() else None), res

    # --- comprobaciones ---------------------------------------------------
    def probar_ingreso(self) -> RunResult:
        """
        Comprueba que Notas Parciales acepta las credenciales. Solo lectura.

        Usa el ``probe`` del script, que entra por NTLM y pide los instrumentos
        del modelo. Es la comprobación más barata que existe contra el servidor
        real, y la única que no necesita haber bajado nada de Moodle todavía:
        por eso sirve para avisar de una contraseña vencida al arrancar, y no a
        mitad de una sincronización (specs/003, A-14).
        """
        res = self._run(["probe", *self._context_args()])
        if not res.ok:
            raise UploaderError(
                "Notas Parciales no aceptó tus datos.",
                remedio=_pista_de_error(res.salida()),
            )
        return res

    @contextmanager
    def reja_de_escritura(self, diario: Path) -> Iterator[None]:
        """
        Tapia la escritura mientras dure el bloque, anotando lo interceptado.

        Existe para poder ensayar **sobre planes ya calculados**: sin esto, un
        ensayo obligaría a volver a bajar todo de Moodle y a sondear de nuevo el
        servidor solo para cambiar una variable de entorno, y un ensayo que
        cuesta cinco minutos es un ensayo que nadie hace.
        """
        anterior = self.fence_journal
        self.fence_journal = diario
        try:
            yield
        finally:
            self.fence_journal = anterior

    # --- descubrimiento ---------------------------------------------------
    def discover_destinations(
        self,
        cus: Sequence[str],
        xlsx_por_cu: dict[str, Path],
        *,
        max_grupo: int = MAX_GRUPO_SONDEO,
    ) -> list[Destination]:
        """
        Averigua a qué destinos van a parar los estudiantes, sondeando.

        Por cada centro universitario presente se prueban los grupos 1 a
        ``max_grupo`` y se conservan los que contienen a alguien
        (specs/002, R-05). Un CU puede aportar varios destinos: es el caso
        normal, no una anomalía (specs/001, D-04).

        Todo son planes, y un plan nunca escribe. El sondeo es seguro por
        construcción, no por promesa.

        Se detiene el sondeo de un CU en cuanto todos sus estudiantes tienen
        destino: en la práctica, casi siempre en el primer o segundo intento.
        """
        encontrados: list[Destination] = []

        for cu in cus:
            xlsx = xlsx_por_cu.get(cu)
            if xlsx is None:
                continue
            pendientes = _cedulas_del_xlsx(xlsx)
            for numero in range(1, max_grupo + 1):
                if not pendientes:
                    break
                candidato = Destination(cu=cu, grupo=numero)
                ubicados = self.cedulas_en_destino(candidato, [xlsx])
                if not ubicados:
                    continue
                encontrados.append(candidato)
                pendientes -= ubicados

        return encontrados

    def cedulas_en_destino(
        self, destination: Destination, xlsx_paths: Sequence[Path]
    ) -> set[str]:
        """
        Las cédulas que el servidor reconoce como propias de este destino.

        Se usa el propio ``plan`` como sonda: las filas que **no** son
        ``skip_not_in_roster`` son exactamente las que el roster oficial
        contiene. No se reimplementa el emparejamiento; se le pregunta al
        código que ya lo hace bien (specs/002, R-13).
        """
        try:
            plan_path, _ = self.plan(destination, xlsx_paths)
        except UploaderError:
            # Un grupo que no existe no es un error del sondeo: es una respuesta.
            return set()

        try:
            return self.cedulas_del_plan(plan_path)
        finally:
            plan_path.unlink(missing_ok=True)

    @staticmethod
    def cedulas_del_plan(plan_path: Path) -> set[str]:
        """
        Las cédulas que el roster oficial reconoce como propias de ese plan.

        Son las filas que **no** son ``skip_not_in_roster``: el resto son
        estudiantes que pertenecen a otro destino.
        """
        return {
            fila["cedula"]
            for fila in leer_plan(plan_path)
            if fila.get("accion") != ACCION_SIN_ROSTER and fila.get("cedula")
        }


def _cedulas_del_xlsx(path: Path) -> set[str]:
    """Las cédulas que contiene un archivo de calificaciones."""
    try:
        import openpyxl
    except ImportError:  # pragma: no cover - dependencia declarada
        return set()

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        filas = wb.active.iter_rows(values_only=True)
        encabezados = [str(c or "").strip() for c in next(filas, ())]
        try:
            i_id = encabezados.index("Número de ID")
        except ValueError:
            return set()
        return {
            str(fila[i_id]).strip()
            for fila in filas
            if i_id < len(fila) and str(fila[i_id] or "").strip()
        }
    finally:
        wb.close()


def _recortar_al_destino(plan_path: Path, destination: Destination) -> None:
    """
    Deja en el plan únicamente las filas de **este** destino.

    Hace falta porque el script, además del ``--cu-grupo`` que le pasamos,
    autodetecta un destino para cada centro universitario que encuentra en el
    xlsx y planifica también esos. Esa autodetección devuelve **un solo grupo
    por CU**, así que sus filas son justamente las que pierden a los estudiantes
    del segundo grupo (specs/001, D-04): son datos en los que no se puede
    confiar, y además duplicarían lo que ya planifica el destino que sí les
    corresponde.

    Recortando cada plan a lo suyo, la unión de todos los planes cubre a cada
    estudiante exactamente una vez, y cada fila proviene de una consulta hecha
    con el destino correcto y explícito.
    """
    with plan_path.open("r", encoding="utf-8-sig", newline="") as f:
        lector = csv.DictReader(f)
        campos = lector.fieldnames or []
        propias = [
            fila
            for fila in lector
            if (fila.get("cu") or "").strip() == destination.cu
            and (fila.get("grupo") or "").strip() == str(destination.grupo)
        ]

    with plan_path.open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=campos)
        escritor.writeheader()
        escritor.writerows(propias)


def escribir_plan_autorizado(
    plan_path: Path, autorizadas: set[tuple[str, str]], salida: Path
) -> Path:
    """
    Copia el plan dejando fuera las sobrescrituras que nadie autorizó (specs/003, A-11).

    El permiso para cambiar una nota ya puesta es una sola bandera del script, y
    vale para el plan entero. Autorizar fila por fila se consigue entonces del
    otro lado: se le entrega un plan que **solo contiene** las sobrescrituras
    autorizadas, y la bandera global pasa a ser exacta.

    Preferimos esto a inventarle una bandera nueva al script: el archivo que se
    ejecuta es el mismo que se puede abrir en Excel y comparar con lo que se vio
    en pantalla, y queda como respaldo de qué se autorizó (specs/002, R-13).

    Las filas que no escriben nada se conservan, para que el plan siga siendo el
    relato completo de la corrida y no solo su parte ejecutable.
    """
    filas = leer_plan(plan_path)
    conservadas = [
        f
        for f in filas
        if f.get("accion") != ACCION_SOBRESCRIBIRIA
        or (f.get("cedula", ""), f.get("instrumento", "")) in autorizadas
    ]

    with plan_path.open("r", encoding="utf-8-sig", newline="") as f:
        campos = csv.DictReader(f).fieldnames or []

    with salida.open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=campos)
        escritor.writeheader()
        escritor.writerows(conservadas)
    return salida


def leer_plan(plan_path: Path) -> list[dict[str, str]]:
    """Lee un ``plan.csv`` como filas ya limpias."""
    with plan_path.open("r", encoding="utf-8-sig", newline="") as f:
        return [{k: (v or "").strip() for k, v in fila.items()} for fila in csv.DictReader(f)]


def reja_verificada(tmp_dir: Path) -> bool:
    """
    Comprueba en un subproceso que la reja se instala y deja su marca.

    Se ejercita el mecanismo de verdad —no se confía en que "debería andar"—
    porque de esta comprobación depende que un ensayo no escriba. Una reja que
    no puede demostrar que está puesta no protege nada.
    """
    codigo = (
        "import os,sys;"
        "sys.path.insert(0, r'" + str(Path(__file__).resolve().parents[1]) + "');"
        "from mnsync._uploader_shim import install_write_fence, FENCE_SENTINEL_ENV;"
        "from pathlib import Path;"
        "install_write_fence(Path(r'" + str(tmp_dir / ".fence_check.jsonl") + "'));"
        "import requests;"
        "s=requests.Session();"
        "r=s.post('http://127.0.0.1:9/x/actualizarNotas', data='{}');"
        "print('SENTINEL=' + os.environ.get(FENCE_SENTINEL_ENV,''));"
        "print('BLOCKED=' + str(r.status_code == 200))"
    )
    try:
        p = subprocess.run(
            [sys.executable, "-c", codigo],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    salida = p.stdout or ""
    return "SENTINEL=1" in salida and "BLOCKED=True" in salida


def cu_de_institucion(valor: str) -> str:
    """
    Extrae el código de centro universitario de la columna «Institución».

    Moodle la escribe siempre como ``DESAMPARADOS (42)`` (specs/001, D-08).
    """
    m = _RE_INSTITUCION.search(valor or "")
    return m.group(1) if m else ""


def _pista_de_error(salida: str) -> str:
    """
    Traduce los fallos frecuentes del script a una instrucción concreta.

    Se busca la causa en la salida real; si no se reconoce, se muestran las
    últimas líneas en vez de inventar un diagnóstico.
    """
    texto = salida or ""
    bajo = texto.lower()

    if "faltan credenciales ntlm" in bajo or "http 401" in bajo:
        return (
            "Notas Parciales rechazó el usuario o la contraseña. Revisá "
            "NP_NTLM_USER y NP_NTLM_PASSWORD en el archivo .env (el usuario va "
            "sin @uned.ac.cr)."
        )
    if "no es json" in bajo or "respuesta no-json" in bajo:
        return "Se venció la sesión con el servidor de la UNED. Volvé a ejecutar el comando."
    if "0 estudiantes" in bajo or "ningún instrumento" in bajo:
        return (
            "El servidor no devolvió estudiantes para ese grupo. Revisá «cu», «grupo», "
            "«asignatura», «modelo» y «pac» en courses.yml: el sistema no da error "
            "cuando esos códigos no corresponden, simplemente devuelve tablas vacías."
        )
    if "no existe en este modelo" in bajo:
        return (
            "Una columna de Moodle se emparejó con un instrumento que no existe. "
            "Usá «item_map» en courses.yml para indicar el código correcto."
        )
    if "requiere --justificacion-codigo" in bajo:
        return "Poné «justificacion_codigo» en la política del curso en courses.yml."

    ultimas = [ln for ln in texto.strip().splitlines() if ln.strip()][-6:]
    return "Salida del script:\n    " + "\n    ".join(ultimas) if ultimas else "Sin más detalle."
