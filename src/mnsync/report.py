"""
Reporte de cambios, en español y para el profesor.

Una sección por **destino** de Notas Parciales —que es donde aterriza una
escritura— y dentro de cada una lo que de verdad importa: qué se escribió, qué
no, y qué conviene mirar con calma.

Antes de todo eso va el reparto: a cuántos destinos fueron a parar los
estudiantes y, sobre todo, **quién se quedó sin ninguno**. Eso último es poco
frecuente (specs/001, D-07) y por eso mismo va nombrado y arriba, no sepultado
entre filas de detalle.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Course, Destination, MoodleGroupRef
from .guard import GuardVerdict, PlanSummary, Routing, RoutingVerdict

#: Cómo se llama cada acción en castellano llano, y qué debe hacer el profesor.
ACCIONES = {
    "upload": ("Nota nueva subida", "Nada, es lo normal"),
    "mark_not_presented": ("Marcada como «no presentó»", "Confirmá que de verdad no entregó"),
    "skip_already_set": ("Ya estaba igual", "Nada. Ver muchas de estas es buena señal"),
    "would_overwrite": ("Cambiaría una nota existente", "🛑 Revisar"),
    "skip_not_in_roster": ("De otro grupo oficial", "Nada: lo recoge el plan de su grupo"),
    "skip_retirado": ("Estudiante retirado", "Nada"),
    "review": ("Caso raro, no se tocó", "🛑 Revisar"),
}

#: Acciones que el profesor debería mirar sí o sí.
#:
#: ``skip_not_in_roster`` NO está acá: con el abanico de destinos, casi todas
#: esas filas son estudiantes de otro destino, y marcarlas como problema
#: ahogaría el reporte. Los que de verdad no aparecen en ningún lado se
#: reportan aparte, con nombre y apellido (specs/002, R-07).
REQUIEREN_ATENCION = ("would_overwrite", "review")


@dataclass
class DestinationOutcome:
    """Todo lo que pasó con un destino de Notas Parciales en una corrida."""

    summary: PlanSummary
    verdict: GuardVerdict
    escrito: bool = False
    #: Si la corrida llevaba permiso para cambiar notas ya existentes. Sin él,
    #: las filas `would_overwrite` NO se escriben, y el reporte no debe decir
    #: que sí: un registro que exagera lo que hizo no sirve como respaldo.
    allow_update: bool = False
    resultados_path: Path | None = None
    error: str | None = None
    #: El recuento del plan que de verdad se le entregó al script.
    #:
    #: Difiere de ``summary`` cuando el profesor autorizó las sobrescrituras
    #: una por una (specs/003, A-11): ``summary`` cuenta lo que se propuso y
    #: este cuenta lo que se ejecutó. Sin esta separación el reporte diría que
    #: escribió notas que el profesor dejó pasar.
    aplicado: PlanSummary | None = None

    @property
    def destination(self) -> Destination:
        return self.summary.destination

    @property
    def label(self) -> str:
        return self.summary.destination.label

    @property
    def acciones_escritas(self) -> tuple[str, ...]:
        """Las acciones que de verdad llegaron a escribirse en esta corrida."""
        base = ("upload", "mark_not_presented")
        return (*base, "would_overwrite") if self.allow_update else base

    @property
    def ejecutado(self) -> PlanSummary:
        """El plan que se ejecutó, que no siempre es el que se propuso."""
        return self.aplicado or self.summary

    @property
    def n_escritas(self) -> int:
        if not self.escrito:
            return 0
        return sum(self.ejecutado.por_accion.get(a, 0) for a in self.acciones_escritas)

    @property
    def sobrescrituras_declinadas(self) -> int:
        """Notas ya puestas que cambiarían y que el profesor decidió no tocar."""
        return max(0, self.summary.sobrescribirian - self.ejecutado.sobrescribirian)


@dataclass
class RunReport:
    """El reporte completo de una corrida."""

    course: Course
    commit: bool
    outcomes: list[DestinationOutcome] = field(default_factory=list)
    started: datetime = field(default_factory=datetime.now)
    #: Los grupos de Moodle de los que se recolectó.
    grupos_moodle: tuple[MoodleGroupRef, ...] = ()
    #: Los destinos a los que se repartió.
    destinos: tuple[Destination, ...] = ()
    #: Dónde quedó cada estudiante.
    routing: Routing | None = None
    #: El fallo del discriminador de patrón.
    routing_verdict: RoutingVerdict | None = None
    #: Si la corrida llevaba la reja de escritura puesta.
    #:
    #: Un ensayo recorre el camino de escritura entero con ``commit=True``, así
    #: que sin esta marca su reporte sería indistinguible del de una corrida
    #: real. Y este archivo es el respaldo de qué se tocó: decir que escribió lo
    #: que no escribió lo vuelve inservible justo cuando hace falta.
    ensayo: bool = False

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
        if self.routing_verdict is not None and self.routing_verdict.blocked:
            return True
        return any(o.verdict.blocked for o in self.outcomes)

    @property
    def hubo_errores(self) -> bool:
        return any(o.error for o in self.outcomes)

    @property
    def sin_destino(self) -> list[tuple[str, str, str]]:
        """
        Los estudiantes que no aparecieron en ningún roster oficial.

        Devuelve ``(cédula, nombre, cu)``. Es poco frecuente (specs/001, D-07),
        y justamente por eso cada uno merece que se lo nombre.
        """
        if self.routing is None:
            return []
        cu_por_cedula = {
            cedula: cu for cu, cedulas in self.routing.por_cu.items() for cedula in cedulas
        }
        return sorted(
            (cedula, self.routing.nombres.get(cedula, ""), cu_por_cedula.get(cedula, ""))
            for cedula in self.routing.sin_destino()
        )

    @property
    def necesita_atencion(self) -> bool:
        return (
            self.hubo_bloqueos
            or self.hubo_errores
            or bool(self.sin_destino)
            or any(
                o.summary.por_accion.get(a, 0) for o in self.outcomes for a in REQUIEREN_ATENCION
            )
        )

    # --- salida ---------------------------------------------------------
    @property
    def modo(self) -> str:
        """Cómo hay que leer los números de este reporte."""
        if self.ensayo:
            return "ENSAYO — la escritura quedó tapiada, no se escribió nada"
        if self.commit:
            return "SE ESCRIBIERON las notas"
        return "prueba (no se escribió nada)"

    def to_markdown(self) -> str:
        L: list[str] = []
        modo = self.modo
        L.append(f"# Sincronización — {self.course.id}")
        L.append("")
        L.append(f"**Fecha:** {self.started:%d/%m/%Y %H:%M}  ")
        L.append(f"**Modo:** {modo}  ")
        if self.grupos_moodle:
            nombres = ", ".join(g.label for g in self.grupos_moodle)
            L.append(f"**Grupos de Moodle:** {nombres}  ")
        L.append(f"**Destinos en Notas Parciales:** {len(self.outcomes)}")
        L.append("")

        L.extend(self._routing_section())

        L.append("## Resumen")
        L.append("")
        L.append("| Destino | Suyos | Cambios | Ya estaban | A revisar | Resultado |")
        L.append("|---|---:|---:|---:|---:|---|")
        for o in self.outcomes:
            if o.error:
                estado = "❌ error"
            elif o.verdict.blocked:
                estado = "🛑 detenido por un freno"
            elif o.escrito and self.ensayo:
                estado = f"🧪 ensayo ({o.n_escritas} se habrían escrito)"
            elif o.escrito:
                estado = f"✅ escrito ({o.n_escritas})"
            else:
                estado = "🧪 prueba"
            atencion = sum(o.summary.por_accion.get(a, 0) for a in REQUIEREN_ATENCION)
            L.append(
                f"| {o.label} | {o.summary.propias} | {o.summary.cambios} | "
                f"{o.summary.ya_estaban} | {atencion} | {estado} |"
            )
        L.append("")

        for o in self.outcomes:
            L.extend(self._destination_section(o))

        L.append("---")
        L.append("")
        if self.ensayo:
            L.append(
                f"Esto fue un **ensayo**: se recorrió el camino de escritura completo "
                f"contra el sistema real, pero cada envío quedó interceptado. Se "
                f"habrían escrito **{self.total_escritas}** nota(s)."
            )
        elif self.commit and not self.hubo_bloqueos and not self.hubo_errores:
            L.append(f"Se escribieron **{self.total_escritas}** notas en Notas Parciales.")
            pendientes = self.total_cambios - self.total_escritas
            if pendientes > 0:
                L.append("")
                L.append(
                    f"Quedaron **{pendientes}** sin escribir porque cambiarían una nota "
                    "que ya estaba puesta. Hay que autorizarlas: en la ventana, con la "
                    "columna «Autorizo» de cada fila; desde la terminal, con "
                    "`--allow-update`."
                )
        elif not self.commit:
            L.append(
                f"Esto fue una prueba: **no se escribió nada**. "
                f"Habría cambiado {self.total_cambios} nota(s)."
            )
        L.append("")
        return "\n".join(L)

    def _routing_section(self) -> list[str]:
        """El reparto, y quién se quedó fuera. Va antes que nada."""
        L: list[str] = []
        v = self.routing_verdict

        if v is not None and v.blocked:
            L += [
                "> 🛑 **Un freno detuvo la corrida.**",
                f"> {v.reason}",
                ">",
                f"> **¿Qué hacer?** {v.remedy}",
                "",
            ]

        huerfanos = self.sin_destino
        if huerfanos:
            L.append("## Estudiantes sin grupo oficial")
            L.append("")
            L.append(
                f"Estos **{len(huerfanos)}** estudiante(s) están en Moodle pero no "
                "aparecen en ningún grupo de Notas Parciales, así que **sus notas no "
                "se subieron**. El resto sí se procesó con normalidad."
            )
            L.append("")
            L.append("| Cédula | Nombre | CU |")
            L.append("|---|---|---|")
            for cedula, nombre, cu in huerfanos:
                L.append(f"| {cedula} | {nombre or '—'} | {cu or '—'} |")
            L.append("")
            L.append(
                "> Suele significar que la persona no quedó matriculada en esta "
                "asignatura. Consultalo con registro antes del cierre de actas."
            )
            L.append("")

        return L

    def _destination_section(self, o: DestinationOutcome) -> list[str]:
        d = o.summary.destination
        L = [f"## Destino: {o.label}", ""]
        L.append(f"Notas Parciales · centro universitario `{d.cu}`, grupo `{d.grupo}`")
        L.append("")

        if o.error:
            L += [f"> ❌ **Falló:** {o.error}", ""]
            return L

        if o.verdict.blocked:
            L += [
                "> 🛑 **No se escribió nada en este destino.**",
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
            L.append("### Notas que se habrían escrito" if self.ensayo else "### Notas escritas")
            L.append("")
            L.append("| Cédula | Instrumento | Antes | Ahora |")
            L.append("|---|---|---|---|")
            for fila in escritas:
                L.append(
                    f"| {fila['cedula']} | {fila['instrumento']} | "
                    f"{fila['nota_remota'] or '—'} | {fila['nota_local']} |"
                )
            L.append("")

        if o.sobrescrituras_declinadas:
            L += [
                f"> {o.sobrescrituras_declinadas} nota(s) ya puestas se dejaron como "
                "estaban porque no se autorizó cambiarlas. No es un error: es la "
                "decisión que se tomó fila por fila antes de escribir.",
                "",
            ]

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

    def _leer_plan(
        self, o: DestinationOutcome, path: Path | None = None
    ) -> list[dict[str, str]]:
        ruta = path or o.summary.plan_path
        if not ruta.exists():
            return []
        with ruta.open("r", encoding="utf-8-sig", newline="") as f:
            return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]

    def _filas_escritas(self, o: DestinationOutcome) -> list[dict[str, str]]:
        if not o.escrito:
            return []
        return [
            r
            for r in self._leer_plan(o, o.ejecutado.plan_path)
            if r.get("accion") in o.acciones_escritas
        ]

    def _filas_de_atencion(self, o: DestinationOutcome) -> list[dict[str, str]]:
        return [r for r in self._leer_plan(o) if r.get("accion") in REQUIEREN_ATENCION]

    # --- resumen corto para la terminal ---------------------------------
    def to_console(self) -> str:
        L: list[str] = []
        for o in self.outcomes:
            if o.error:
                icono, detalle = "❌", o.error
            elif o.verdict.blocked:
                icono, detalle = "🛑", o.verdict.reason
            elif o.escrito and self.ensayo:
                icono, detalle = "🧪", f"{o.n_escritas} nota(s) se habrían escrito"
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
