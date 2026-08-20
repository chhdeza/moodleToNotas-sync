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

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Course, Credentials, Group
from .errors import UploaderError

#: Acciones del plan que implican escribir en el sistema de la UNED.
ACCIONES_DE_ESCRITURA = frozenset({"upload", "mark_not_presented", "would_overwrite"})


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
            "--tutor", np.tutor,
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
    def plan(self, group: Group, xlsx: Path) -> tuple[Path, RunResult]:
        """
        Genera el plan de un grupo.

        El ``--cu-grupo`` va **explícito**, tomado de ``courses.yml``. Así el
        script nunca recurre a su autodetección, que devuelve un solo grupo
        por centro universitario y perdería en silencio a los estudiantes del
        segundo grupo cuando el profesor da dos en el mismo CU.
        """
        salida = self.work_dir / f"notas_plan_{group.slug}.csv"
        args = [
            "plan",
            *self._context_args(),
            "--xlsx", str(xlsx),
            "--cu-grupo", f"{group.cu}={group.grupo}",
            "--output", str(salida),
        ]
        for header, codigo in self.course.item_map.items():
            args += ["--map", f"{header}={codigo}"]

        res = self._run(args)
        if not res.ok or not salida.exists():
            raise UploaderError(
                f"No se pudo generar el plan del grupo «{group.label}».",
                remedio=_pista_de_error(res.salida()),
            )
        return salida, res

    def apply(
        self,
        group: Group,
        plan_path: Path,
        *,
        commit: bool,
        allow_update: bool,
    ) -> tuple[Path | None, RunResult]:
        """Ejecuta el plan de un grupo. Sin ``commit=True`` no escribe nada."""
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
                f"Falló la carga de notas del grupo «{group.label}».",
                remedio=_pista_de_error(res.salida()),
            )

        resultados = plan_path.with_name(plan_path.stem + "_resultados.csv")
        return (resultados if resultados.exists() else None), res


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
