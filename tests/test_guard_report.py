"""
Pruebas de los frenos y del reporte.

Los frenos se prueban en aislamiento, sobre planes escritos a mano, para poder
recorrer los bordes exactos (justo en el límite, uno por encima, uno por debajo)
sin depender de un servidor.

Son dos frenos distintos y se prueban por separado:

- El **radio de daño** mira un destino y cuenta cuántas notas cambiaría.
- El **discriminador de patrón** mira la corrida entera y juzga la *forma* de
  los estudiantes sin destino, no su proporción (specs/002, R-09).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from mnsync.config import Course, Destination, MoodleGroupRef, NotasParcialesCtx, Policy
from mnsync.errors import RoutingError
from mnsync.guard import Routing, check, check_routing, merge_routing, summarize_plan
from mnsync.report import DestinationOutcome, RunReport

CAMPOS = [
    "cu", "grupo", "cedula", "nombre",
    "instrumento", "instrumento_nombre",
    "nota_local", "nota_remota",
    "accion", "motivo", "fuente",
]

DESTINO = Destination(cu="42", grupo=1)
GRUPO_MOODLE = MoodleGroupRef(moodle_group_id=38525, name="Grupo 1")


def escribir_plan(
    tmp_path: Path,
    acciones: list[str],
    *,
    destino: Destination = DESTINO,
    nombre: str = "notas_plan_g.csv",
    cedulas: list[str] | None = None,
) -> Path:
    """Un plan.csv con una fila por acción indicada."""
    p = tmp_path / nombre
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        for i, accion in enumerate(acciones):
            cedula = cedulas[i] if cedulas else f"01{i:08d}"
            w.writerow(
                {
                    "cu": destino.cu, "grupo": str(destino.grupo),
                    "cedula": cedula, "nombre": f"ESTUDIANTE {i}",
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
    veredicto = check(summarize_plan(plan, DESTINO), politica(limite))
    assert veredicto.allowed is permitido


def test_el_freno_cuenta_las_tres_acciones_que_escriben(tmp_path):
    """upload, mark_not_presented y would_overwrite escriben; las demás no."""
    plan = escribir_plan(
        tmp_path,
        ["upload", "mark_not_presented", "would_overwrite", "skip_already_set", "skip_retirado"],
    )
    resumen = summarize_plan(plan, DESTINO)
    assert resumen.cambios == 3
    assert resumen.total == 5


def test_el_freno_explica_como_seguir(tmp_path):
    plan = escribir_plan(tmp_path, ["upload"] * 10)
    veredicto = check(summarize_plan(plan, DESTINO), politica(2))
    assert "No se escribió nada" in veredicto.remedy
    assert "max_changes" in veredicto.remedy


def test_el_radio_se_cuenta_por_destino(tmp_path):
    """
    El límite es por destino, no por corrida (specs/002, R-12).

    Con diez CU y varios instrumentos, un límite global saltaría en toda corrida
    legítima. Un freno que grita siempre se termina apagando.
    """
    a = summarize_plan(escribir_plan(tmp_path, ["upload"] * 3, nombre="a.csv"), DESTINO)
    b = summarize_plan(
        escribir_plan(tmp_path, ["upload"] * 3, nombre="b.csv", destino=Destination("01", 1)),
        Destination("01", 1),
    )
    assert check(a, politica(4)).allowed
    assert check(b, politica(4)).allowed


# ---------------------------------------------------------------------------
# Discriminador de patrón
# ---------------------------------------------------------------------------
def routing_de(por_cu: dict[str, list[str]], ubicados: list[str]) -> Routing:
    """Arma un enrutamiento a mano: quién hay por CU, y quiénes encontraron destino."""
    r = Routing()
    for cu, cedulas in por_cu.items():
        r.por_cu[cu] = set(cedulas)
    for cedula in ubicados:
        r.ubicados[cedula] = DESTINO
    return r


def test_unas_pocas_ausencias_no_bloquean():
    """
    Un estudiante en Moodle sin matrícula oficial es un caso de borde legítimo.

    Poco frecuente, pero real (specs/001, D-07): se nombra y se sigue (R-06).
    """
    r = routing_de({"42": ["a", "b", "c", "d"]}, ubicados=["a", "b", "c"])
    assert check_routing(r).allowed
    assert r.sin_destino() == {"d"}


def test_grupo_diminuto_no_se_juzga_por_proporcion():
    """
    Con dos estudiantes, una ausencia legítima ya sería el 50%.

    Es exactamente el caso que un umbral por proporción convertía en falsa
    alarma. La forma, no la proporción.
    """
    r = routing_de({"42": ["a", "b"]}, ubicados=["a"])
    assert check_routing(r).allowed


def test_un_cu_entero_sin_destino_bloquea_ese_cu():
    """Que falle un centro universitario completo apunta a un destino mal resuelto."""
    r = routing_de({"42": ["a", "b", "c"], "01": ["x", "y"]}, ubicados=["a", "b", "c"])
    veredicto = check_routing(r)

    assert veredicto.blocked
    assert veredicto.cus_bloqueados == ("01",)
    assert "CU 01" in veredicto.reason
    assert "Los demás centros sí se procesaron" in veredicto.remedy


def test_todos_los_cu_sin_destino_apuntan_a_los_codigos():
    """
    Cuando falla todo a la vez, el problema no es la matrícula.

    El servidor no da error ante códigos de asignatura equivocados: devuelve
    tablas vacías. Convertir ese silencio en un alto es el trabajo de este freno.
    """
    r = routing_de({"42": ["a", "b"], "01": ["x", "y"]}, ubicados=[])
    veredicto = check_routing(r)

    assert veredicto.blocked
    assert set(veredicto.cus_bloqueados) == {"42", "01"}
    assert "asignatura" in veredicto.remedy and "modelo" in veredicto.remedy
    assert "No se escribió nada" in veredicto.remedy


def test_sin_estudiantes_no_se_da_por_bueno():
    """Una descarga vacía no es una corrida exitosa sin nada que hacer."""
    veredicto = check_routing(Routing())
    assert veredicto.blocked
    assert "ningún estudiante" in veredicto.reason


def test_una_cedula_en_dos_rosters_es_un_fallo_del_sondeo(tmp_path):
    """
    Un estudiante pertenece a un solo grupo oficial (specs/001, D-05).

    Si aparece en dos, el sondeo está mal —probablemente el servidor respondió a
    un grupo inexistente con datos de otro— y el mensaje va dirigido a quien
    desarrolla, nunca al profesor como si su estudiante tuviera un problema
    (specs/002, R-08).
    """
    d1, d2 = Destination("42", 1), Destination("42", 2)
    p1 = escribir_plan(tmp_path, ["upload"], destino=d1, nombre="p1.csv", cedulas=["aaa"])
    p2 = escribir_plan(tmp_path, ["upload"], destino=d2, nombre="p2.csv", cedulas=["aaa"])

    with pytest.raises(RoutingError) as ex:
        merge_routing([(d1, p1), (d2, p2)])

    mensaje = str(ex.value)
    assert "CU 42 / grupo 1" in mensaje and "CU 42 / grupo 2" in mensaje
    assert "Error interno" in mensaje
    assert "no un problema de matrícula" in mensaje
    assert "No se escribió nada" in mensaje


def test_merge_fusiona_los_planes_de_cada_destino(tmp_path):
    """
    Un estudiante ubicado en un destino no cuenta como ausente en los demás.

    Es la razón de fusionar antes de juzgar: mirado plan por plan, todo el mundo
    parece faltar en algún lado.
    """
    d1, d2 = Destination("42", 1), Destination("42", 2)
    p1 = escribir_plan(
        tmp_path, ["upload", "skip_not_in_roster"], destino=d1, nombre="p1.csv",
        cedulas=["aaa", "bbb"],
    )
    p2 = escribir_plan(
        tmp_path, ["skip_not_in_roster", "upload"], destino=d2, nombre="p2.csv",
        cedulas=["aaa", "bbb"],
    )

    routing = merge_routing([(d1, p1), (d2, p2)])

    assert routing.ubicados["aaa"] == d1
    assert routing.ubicados["bbb"] == d2
    assert routing.sin_destino() == set()
    assert check_routing(routing).allowed


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------
def curso_de_prueba() -> Course:
    return Course(
        id="curso-a",
        moodle_course_id=8067,
        np=NotasParcialesCtx(
            ano="2026", pac="3", asignatura="00883", escuela="03",
            catedra=253, encargado="X", modelo=4,
        ),
        groups=(GRUPO_MOODLE,),
        destinations=(DESTINO,),
        policy=politica(),
    )


def test_reporte_de_prueba_deja_claro_que_no_escribio(tmp_path):
    plan = escribir_plan(tmp_path, ["upload"] * 3)
    resumen = summarize_plan(plan, DESTINO)
    reporte = RunReport(course=curso_de_prueba(), commit=False)
    reporte.outcomes.append(
        DestinationOutcome(summary=resumen, verdict=check(resumen, politica()))
    )

    md = reporte.to_markdown()
    assert "no se escribió nada" in md.lower()
    # El destino queda escrito para auditoría.
    assert "CU 42 / grupo 1" in md
    assert "`42`" in md


def test_reporte_nombra_a_quien_se_quedo_sin_destino(tmp_path):
    """
    Es poco frecuente, y por eso mismo va con nombre y arriba (specs/002, R-07).

    Enterrarlo entre filas de detalle equivaldría a no reportarlo.
    """
    plan = escribir_plan(tmp_path, ["upload"] * 2, cedulas=["aaa", "bbb"])
    resumen = summarize_plan(plan, DESTINO)
    routing = routing_de({"42": ["aaa", "bbb", "ccc"]}, ubicados=["aaa", "bbb"])
    routing.nombres["ccc"] = "MARIA SOLANO MORA"

    reporte = RunReport(course=curso_de_prueba(), commit=True, routing=routing)
    reporte.outcomes.append(
        DestinationOutcome(summary=resumen, verdict=check(resumen, politica()), escrito=True)
    )

    md = reporte.to_markdown()
    assert "Estudiantes sin grupo oficial" in md
    assert "MARIA SOLANO MORA" in md
    assert "ccc" in md
    assert reporte.necesita_atencion
    # Y queda claro que el resto sí subió.
    assert "El resto sí se procesó" in md


def test_reporte_destaca_los_casos_a_revisar(tmp_path):
    plan = escribir_plan(tmp_path, ["would_overwrite", "review", "upload"])
    resumen = summarize_plan(plan, DESTINO)
    reporte = RunReport(course=curso_de_prueba(), commit=True)
    reporte.outcomes.append(
        DestinationOutcome(summary=resumen, verdict=check(resumen, politica()), escrito=True)
    )

    md = reporte.to_markdown()
    assert "conviene revisar" in md
    assert "decisión final sobre estos casos es del profesor" in md
    assert reporte.necesita_atencion


def test_un_estudiante_de_otro_destino_no_es_un_caso_a_revisar(tmp_path):
    """
    Con el abanico, casi toda fila ``skip_not_in_roster`` es alguien de otro
    destino. Marcarlas como problema ahogaría el reporte en ruido y escondería
    lo que sí importa.
    """
    plan = escribir_plan(tmp_path, ["upload", "skip_not_in_roster"])
    resumen = summarize_plan(plan, DESTINO)
    routing = routing_de({"42": ["010000000", "010000001"]}, ubicados=["010000000", "010000001"])
    reporte = RunReport(course=curso_de_prueba(), commit=True, routing=routing)
    reporte.outcomes.append(
        DestinationOutcome(summary=resumen, verdict=check(resumen, politica()), escrito=True)
    )

    assert not reporte.necesita_atencion
    assert "conviene revisar" not in reporte.to_markdown()


def test_reporte_muestra_el_destino_frenado_con_su_motivo(tmp_path):
    plan = escribir_plan(tmp_path, ["upload"] * 10)
    resumen = summarize_plan(plan, DESTINO)
    veredicto = check(resumen, politica(2))
    reporte = RunReport(course=curso_de_prueba(), commit=True)
    reporte.outcomes.append(DestinationOutcome(summary=resumen, verdict=veredicto))

    md = reporte.to_markdown()
    assert "No se escribió nada en este destino" in md
    assert "¿Qué hacer?" in md
    assert reporte.hubo_bloqueos


def test_la_columna_que_dice_que_hacer_esta_presente(tmp_path):
    """El profesor necesita saber qué acción tomar, no solo qué pasó."""
    plan = escribir_plan(tmp_path, ["would_overwrite", "skip_already_set"])
    resumen = summarize_plan(plan, DESTINO)
    reporte = RunReport(course=curso_de_prueba(), commit=False)
    reporte.outcomes.append(
        DestinationOutcome(summary=resumen, verdict=check(resumen, politica()))
    )

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
    resumen = summarize_plan(plan, DESTINO)
    reporte = RunReport(course=curso_de_prueba(), commit=True)
    reporte.outcomes.append(
        DestinationOutcome(
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
    resumen = summarize_plan(plan, DESTINO)
    reporte = RunReport(course=curso_de_prueba(), commit=True)
    reporte.outcomes.append(
        DestinationOutcome(
            summary=resumen,
            verdict=check(resumen, politica()),
            escrito=True,
            allow_update=True,
        )
    )

    assert reporte.total_escritas == 3
    assert "Se escribieron **3** notas" in reporte.to_markdown()
