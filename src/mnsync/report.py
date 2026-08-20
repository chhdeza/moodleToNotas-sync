"""
Reporte de cambios, en español y para el profesor.

Una sección por grupo, y dentro de cada una lo que de verdad importa: qué se
escribió, qué no, y qué conviene mirar con calma. La sección de casos a
revisar sigue la convención de «casos límite» que ya usa el flujo de
calificación asistida.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Course
from .guard import GuardVerdict, PlanSummary

#: Cómo se llama cada acción en castellano llano, y qué debe hacer el profesor.
ACCIONES = {
    "upload": ("Nota nueva subida", "Nada, es lo normal"),
    "mark_not_presented": ("Marcada como «no presentó»", "Confirmá que de verdad no entregó"),
    "skip_already_set": ("Ya estaba igual", "Nada. Ver muchas de estas es buena señal"),
    "would_overwrite": ("Cambiaría una nota existente", "🛑 Revisar"),
    "skip_not_in_roster": ("No está en el grupo oficial", "🛑 Revisar cédula o grupo"),
    "skip_retirado": ("Estudiante retirado", "Nada"),
    "review": ("Caso raro, no se tocó", "🛑 Revisar"),
}

#: Acciones que el profesor debería mirar sí o sí.
REQUIEREN_ATENCION = ("would_overwrite", "skip_not_in_roster", "review")


@dataclass
class GroupOutcome:
    """Todo lo que pasó con un grupo en una corrida."""

    summary: PlanSummary
    verdict: GuardVerdict
    escrito: bool = False
    #: Si la corrida llevaba permiso para cambiar notas ya existentes. Sin él,
    #: las filas `would_overwrite` NO se escriben, y el reporte no debe decir
    #: que sí: un registro que exagera lo que hizo no sirve como respaldo.
    allow_update: bool = False
    resultados_path: Path | None = None
    error: str | None = None

    @property
    def label(self) -> str:
        return self.summary.group.label

    @property
    def acciones_escritas(self) -> tuple[str, ...]:
        """Las acciones que de verdad llegaron a escribirse en esta corrida."""
        base = ("upload", "mark_not_presented")
        return (*base, "would_overwrite") if self.allow_update else base

    @property
    def n_escritas(self) -> int:
        if not self.escrito:
            return 0
        return sum(self.summary.por_accion.get(a, 0) for a in self.acciones_escritas)


@dataclass
class RunReport:
    """El reporte completo de una corrida."""

    course: Course
    commit: bool
    outcomes: list[GroupOutcome] = field(default_factory=list)
    started: datetime = field(default_factory=datetime.now)

    @property
    def total_cambios(self) -> int:
        """Cambios que el plan propuso (incluye los que esperan autorización)."""
        return sum(o.summary.cambios for o in self.outcomes if o.summary)

    @property
    def total_escritas(self) -> int:
        """Notas efectivamente escritas en el sistema de la UNED."""
        return sum(o.n_escritas for o in self.outcomes)

    @property
    def hubo_bloqueos(self) -> bool:
        return any(o.verdict.blocked for o in self.outcomes)

    @property
    def hubo_errores(self) -> bool:
        return any(o.error for o in self.outcomes)

    @property
    def necesita_atencion(self) -> bool:
        return self.hubo_bloqueos or self.hubo_errores or any(
            o.summary.por_accion.get(a, 0) for o in self.outcomes for a in REQUIEREN_ATENCION
        )

    # --- salida ---------------------------------------------------------
    def to_markdown(self) -> str:
        L: list[str] = []
        modo = "SE ESCRIBIERON las notas" if self.commit else "prueba (no se escribió nada)"
        L.append(f"# Sincronización — {self.course.id}")
        L.append("")
        L.append(f"**Fecha:** {self.started:%d/%m/%Y %H:%M}  ")
        L.append(f"**Modo:** {modo}  ")
        L.append(f"**Grupos procesados:** {len(self.outcomes)}")
        L.append("")

        L.append("## Resumen")
        L.append("")
        L.append("| Grupo | En el plan | Cambios | Ya estaban | A revisar | Resultado |")
        L.append("|---|---:|---:|---:|---:|---|")
        for o in self.outcomes:
            if o.error:
                estado = "❌ error"
            elif o.verdict.blocked:
                estado = "🛑 detenido por un freno"
            elif o.escrito:
                estado = f"✅ escrito ({o.n_escritas})"
            else:
                estado = "🧪 prueba"
            atencion = sum(o.summary.por_accion.get(a, 0) for a in REQUIEREN_ATENCION)
            L.append(
                f"| {o.label} | {o.summary.total} | {o.summary.cambios} | "
                f"{o.summary.ya_estaban} | {atencion} | {estado} |"
            )
        L.append("")

        for o in self.outcomes:
            L.extend(self._group_section(o))

        L.append("---")
        L.append("")
        if self.commit and not self.hubo_bloqueos and not self.hubo_errores:
            L.append(f"Se escribieron **{self.total_escritas}** notas en Notas Parciales.")
            pendientes = self.total_cambios - self.total_escritas
            if pendientes > 0:
                L.append("")
                L.append(
                    f"Quedaron **{pendientes}** sin escribir porque cambiarían una nota "
                    "que ya estaba puesta. Para aplicarlas hay que autorizarlo con "
                    "`--allow-update`."
                )
        elif not self.commit:
            L.append(
                f"Esto fue una prueba: **no se escribió nada**. "
                f"Habría cambiado {self.total_cambios} nota(s)."
            )
        L.append("")
        return "\n".join(L)

    def _group_section(self, o: GroupOutcome) -> list[str]:
        g = o.summary.group
        L = [f"## Grupo: {o.label}", ""]
        L.append(
            f"Moodle grupo `{g.moodle_group_id}` → Notas Parciales CU `{g.cu}`, grupo `{g.grupo}`"
        )
        L.append("")

        if o.error:
            L += [f"> ❌ **Falló:** {o.error}", ""]
            return L

        if o.verdict.blocked:
            L += [
                "> 🛑 **No se escribió nada en este grupo.**",
                f"> {o.verdict.reason}",
                ">",
                f"> **¿Qué hacer?** {o.verdict.remedy}",
                "",
            ]

        L.append("| Qué pasó | Cuántas | ¿Qué hago yo? |")
        L.append("|---|---:|---|")
        for accion, cantidad in o.summary.por_accion.most_common():
            if not cantidad:
                continue
            etiqueta, que_hacer = ACCIONES.get(accion, (accion, ""))
            L.append(f"| {etiqueta} | {cantidad} | {que_hacer} |")
        L.append("")

        escritas = self._filas_escritas(o)
        if escritas:
            L.append("### Notas escritas")
            L.append("")
            L.append("| Cédula | Instrumento | Antes | Ahora |")
            L.append("|---|---|---|---|")
            for fila in escritas:
                L.append(
                    f"| {fila['cedula']} | {fila['instrumento']} | "
                    f"{fila['nota_remota'] or '—'} | {fila['nota_local']} |"
                )
            L.append("")

        atencion = self._filas_de_atencion(o)
        if atencion:
            L.append("### ⚠️ Casos que conviene revisar")
            L.append("")
            L.append("| Cédula | Nombre | Instrumento | Qué pasó | Motivo |")
            L.append("|---|---|---|---|---|")
            for fila in atencion:
                etiqueta = ACCIONES.get(fila["accion"], (fila["accion"], ""))[0]
                L.append(
                    f"| {fila['cedula']} | {fila['nombre']} | {fila['instrumento']} | "
                    f"{etiqueta} | {fila['motivo']} |"
                )
            L.append("")
            L.append("> La decisión final sobre estos casos es del profesor.")
            L.append("")

        return L

    def _leer_plan(self, o: GroupOutcome) -> list[dict[str, str]]:
        if not o.summary.plan_path.exists():
            return []
        with o.summary.plan_path.open("r", encoding="utf-8-sig", newline="") as f:
            return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]

    def _filas_escritas(self, o: GroupOutcome) -> list[dict[str, str]]:
        if not o.escrito:
            return []
        return [r for r in self._leer_plan(o) if r.get("accion") in o.acciones_escritas]

    def _filas_de_atencion(self, o: GroupOutcome) -> list[dict[str, str]]:
        return [r for r in self._leer_plan(o) if r.get("accion") in REQUIEREN_ATENCION]

    # --- resumen corto para la terminal ---------------------------------
    def to_console(self) -> str:
        L: list[str] = []
        for o in self.outcomes:
            if o.error:
                icono, detalle = "❌", o.error
            elif o.verdict.blocked:
                icono, detalle = "🛑", o.verdict.reason
            elif o.escrito:
                icono, detalle = "✅", f"{o.n_escritas} nota(s) escritas"
            else:
                icono, detalle = "🧪", f"{o.summary.cambios} nota(s) se escribirían"
            L.append(f"  {icono} {o.label}: {detalle}")
        return "\n".join(L)


def write_report(report: RunReport, out_dir: Path) -> Path:
    """Guarda el reporte en markdown y devuelve la ruta."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"reporte_{report.course.id}_{report.started:%Y%m%d_%H%M}.md"
    path.write_text(report.to_markdown(), encoding="utf-8")
    return path
