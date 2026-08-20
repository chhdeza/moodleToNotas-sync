"""
Pruebas de los frenos y del reporte.

Los frenos se prueban en aislamiento, sobre planes escritos a mano, para
poder recorrer los bordes exactos (justo en el límite, uno por encima, uno por
debajo) sin depender de un servidor.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from mnsync.config import Course, Group, NotasParcialesCtx, Policy
from mnsync.guard import check, summarize_plan
from mnsync.report import GroupOutcome, RunReport

CAMPOS = [
    "cu", "grupo", "cedula", "nombre",
    "instrumento", "instrumento_nombre",
    "nota_local", "nota_remota",
    "accion", "motivo", "fuente",
]

GRUPO = Group(moodle_group_id=38525, cu="42", grupo=1, name="Grupo 1")


def escribir_plan(tmp_path: Path, acciones: list[str]) -> Path:
    """Un plan.csv con una fila por acción indicada."""
    p = tmp_path / "notas_plan_g.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        for i, accion in enumerate(acciones):
            w.writerow(
                {
                    "cu": "42", "grupo": "1",
                    "cedula": f"01{i:08d}", "nombre": f"ESTUDIANTE {i}",
                    "instrumento": "Tar1", "instrumento_nombre": "Tarea 1",
                    "nota_local": "8.0", "nota_remota": "",
                    "accion": accion, "motivo": "motivo de prueba", "fuente": "x.xlsx",
                }
            )
    return p


def politica(max_changes: int = 40) -> Policy:
    return Policy(allow_update=False, justificacion_codigo=2005, max_changes=max_changes)


# ---------------------------------------------------------------------------
# Freno de radio de daño
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "cantidad,limite,permitido",
    [
        (2, 3, True),   # por debajo
        (3, 3, True),   # justo en el límite
        (4, 3, False),  # uno por encima
        (99, 0, True),  # 0 = sin freno
    ],
)
def test_freno_de_radio_en_los_bordes(tmp_path, cantidad, limite, permitido):
    plan = escribir_plan(tmp_path, ["upload"] * cantidad)
    veredicto = check(summarize_plan(plan, GRUPO), politica(limite))
    assert veredicto.allowed is permitido


def test_el_freno_cuenta_las_tres_acciones_que_escriben(tmp_path):
    """upload, mark_not_presented y would_overwrite escriben; las demás no."""
    plan = escribir_plan(
        tmp_path,
        ["upload", "mark_not_presented", "would_overwrite", "skip_already_set", "skip_retirado"],
    )
    resumen = summarize_plan(plan, GRUPO)
    assert resumen.cambios == 3
    assert resumen.total == 5


def test_el_freno_explica_como_seguir(tmp_path):
    plan = escribir_plan(tmp_path, ["upload"] * 10)
    veredicto = check(summarize_plan(plan, GRUPO), politica(2))
    assert "No se escribió nada" in veredicto.remedy
    assert "max_changes" in veredicto.remedy


# ---------------------------------------------------------------------------
# Freno de emparejamiento
# ---------------------------------------------------------------------------
def test_detecta_cu_grupo_equivocado(tmp_path):
    """Casi todas las filas fuera del roster: la configuración está mal."""
    plan = escribir_plan(tmp_path, ["skip_not_in_roster"] * 8 + ["upload"])
    veredicto = check(summarize_plan(plan, GRUPO), politica())
    assert veredicto.blocked
    assert "courses.yml" in veredicto.remedy
    assert "38525" in veredicto.remedy


def test_unas_pocas_ausencias_no_bloquean(tmp_path):
    """Un par de estudiantes sin matrícula oficial es normal, no un error."""
    plan = escribir_plan(tmp_path, ["upload"] * 9 + ["skip_not_in_roster"])
    assert check(summarize_plan(plan, GRUPO), politica()).allowed


def test_grupo_diminuto_no_se_juzga_por_proporcion(tmp_path):
    """
    Con dos filas, una ausencia legítima ya sería el 50%.

    Juzgar proporciones en grupos minúsculos daría falsas alarmas.
    """
    plan = escribir_plan(tmp_path, ["upload", "skip_not_in_roster"])
    assert check(summarize_plan(plan, GRUPO), politica()).allowed


def test_el_emparejamiento_se_revisa_antes_que_el_radio(tmp_path):
    """
    Si la configuración está mal, el recuento de cambios no significa nada.

    El motivo reportado tiene que ser la causa raíz, no el síntoma.
    """
    plan = escribir_plan(tmp_path, ["skip_not_in_roster"] * 8 + ["upload"] * 5)
    veredicto = check(summarize_plan(plan, GRUPO), politica(1))
    assert veredicto.blocked
    assert "no aparecen en el grupo oficial" in veredicto.reason


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------
def curso_de_prueba() -> Course:
    return Course(
        id="curso-a",
        moodle_course_id=8067,
        np=NotasParcialesCtx(
            ano="2026", pac="3", asignatura="00883", escuela="03",
            catedra=253, encargado="X", tutor="0", modelo=4,
        ),
        groups=(GRUPO,),
        policy=politica(),
    )


def test_reporte_de_prueba_deja_claro_que_no_escribio(tmp_path):
    plan = escribir_plan(tmp_path, ["upload"] * 3)
    resumen = summarize_plan(plan, GRUPO)
    reporte = RunReport(course=curso_de_prueba(), commit=False)
    reporte.outcomes.append(GroupOutcome(summary=resumen, verdict=check(resumen, politica())))

    md = reporte.to_markdown()
    assert "no se escribió nada" in md.lower()
    assert "Grupo 1" in md
    # La correspondencia Moodle → Notas Parciales queda escrita para auditoría.
    assert "38525" in md and "CU `42`" in md


def test_reporte_destaca_los_casos_a_revisar(tmp_path):
    plan = escribir_plan(tmp_path, ["would_overwrite", "review", "skip_not_in_roster", "upload"])
    resumen = summarize_plan(plan, GRUPO)
    reporte = RunReport(course=curso_de_prueba(), commit=True)
    reporte.outcomes.append(
        GroupOutcome(summary=resumen, verdict=check(resumen, politica()), escrito=True)
    )

    md = reporte.to_markdown()
    assert "conviene revisar" in md
    assert "decisión final sobre estos casos es del profesor" in md
    assert reporte.necesita_atencion


def test_reporte_muestra_el_grupo_frenado_con_su_motivo(tmp_path):
    plan = escribir_plan(tmp_path, ["upload"] * 10)
    resumen = summarize_plan(plan, GRUPO)
    veredicto = check(resumen, politica(2))
    reporte = RunReport(course=curso_de_prueba(), commit=True)
    reporte.outcomes.append(GroupOutcome(summary=resumen, verdict=veredicto))

    md = reporte.to_markdown()
    assert "No se escribió nada en este grupo" in md
    assert "¿Qué hacer?" in md
    assert reporte.hubo_bloqueos


def test_la_columna_que_dice_que_hacer_esta_presente(tmp_path):
    """El profesor necesita saber qué acción tomar, no solo qué pasó."""
    plan = escribir_plan(tmp_path, ["would_overwrite", "skip_already_set"])
    resumen = summarize_plan(plan, GRUPO)
    reporte = RunReport(course=curso_de_prueba(), commit=False)
    reporte.outcomes.append(GroupOutcome(summary=resumen, verdict=check(resumen, politica())))

    md = reporte.to_markdown()
    assert "¿Qué hago yo?" in md
    assert "🛑 Revisar" in md
    assert "buena señal" in md


# ---------------------------------------------------------------------------
# El reporte no puede exagerar lo que hizo
# ---------------------------------------------------------------------------
def test_no_cuenta_como_escrita_una_nota_que_no_se_autorizo(tmp_path):
    """
    Sin `--allow-update`, las filas `would_overwrite` NO se escriben.

    El reporte es el respaldo de lo ocurrido: si dijera que sobrescribió una
    nota que en realidad no tocó, el profesor creería haber cambiado algo que
    sigue igual en el sistema oficial.
    """
    plan = escribir_plan(tmp_path, ["upload", "upload", "would_overwrite"])
    resumen = summarize_plan(plan, GRUPO)
    reporte = RunReport(course=curso_de_prueba(), commit=True)
    reporte.outcomes.append(
        GroupOutcome(
            summary=resumen,
            verdict=check(resumen, politica()),
            escrito=True,
            allow_update=False,
        )
    )

    assert reporte.total_escritas == 2, "conto la sobrescritura como escrita"
    md = reporte.to_markdown()
    assert "Se escribieron **2** notas" in md
    # Y explica qué pasó con la que quedó pendiente.
    assert "Quedaron **1** sin escribir" in md
    assert "--allow-update" in md


def test_con_autorizacion_si_cuenta_la_sobrescritura(tmp_path):
    plan = escribir_plan(tmp_path, ["upload", "upload", "would_overwrite"])
    resumen = summarize_plan(plan, GRUPO)
    reporte = RunReport(course=curso_de_prueba(), commit=True)
    reporte.outcomes.append(
        GroupOutcome(
            summary=resumen,
            verdict=check(resumen, politica()),
            escrito=True,
            allow_update=True,
        )
    )

    assert reporte.total_escritas == 3
    assert "Se escribieron **3** notas" in reporte.to_markdown()
