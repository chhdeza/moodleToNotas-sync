"""
Pruebas de extremo a extremo: todo menos una escritura real.

Acá corre el proceso completo —descarga de Moodle, planificación, y
``--commit`` de verdad— usando el script real de Notas Parciales contra el
servidor falso. Cada llamada de escritura queda anotada en el diario del
servidor falso, y sobre ese diario se afirma.

La prueba que más pesa de todas es ``test_dry_run_dice_la_verdad``: si el modo
prueba no coincidiera exactamente con lo que hace ``--commit``, todas las
demás capas de seguridad serían una promesa sin respaldo.
"""

from __future__ import annotations

from pathlib import Path

import responses

from mnsync.cli import main as cli_main
from tests.fake_np.state import NOTA_NO_PRESENTO, NOTA_RETIRADO
from tests.fixtures import moodle_html as fx
from tests.fixtures.gen import make_students

BASE_MOODLE = "http://127.0.0.1:9/moodle"
CURSO_MOODLE = 8067
COLUMNA = "Tarea 1 (Real)"


# ---------------------------------------------------------------------------
# Andamiaje
# ---------------------------------------------------------------------------
def escribir_courses_yml(
    tmp_path: Path,
    grupos: list[tuple[int, str, int]],
    *,
    max_changes: int = 40,
    allow_update: bool = False,
) -> Path:
    """``grupos``: lista de (moodle_group_id, cu, grupo_np)."""
    lineas = [
        "courses:",
        "  - id: curso-prueba",
        "    moodle:",
        f"      course_id: {CURSO_MOODLE}",
        "    notas_parciales:",
        '      ano: "2026"',
        '      pac: "3"',
        '      tipo: "O"',
        '      asignatura: "00883"',
        '      escuela: "03"',
        "      catedra: 253",
        "      encargado: ARODRIGUEZP",
        '      tutor: "0401780367"',
        "      modelo: 4",
        "    groups:",
    ]
    for gid, cu, gnum in grupos:
        lineas += [
            f"      - moodle_group_id: {gid}",
            f'        name: "Grupo {gnum}"',
            f'        cu: "{cu}"',
            f"        grupo: {gnum}",
        ]
    lineas += [
        "    policy:",
        f"      allow_update: {'true' if allow_update else 'false'}",
        "      justificacion_codigo: 2005",
        f"      max_changes: {max_changes}",
    ]
    p = tmp_path / "courses.yml"
    p.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return p


def mock_moodle(rsps, grupos_csv: dict[int, str], *, grupos_moodle=None):
    """Simula Moodle: ingreso, página de exportación y descarga, por grupo."""
    grupos_moodle = grupos_moodle or [(gid, f"Grupo {i+1}") for i, gid in enumerate(grupos_csv)]

    rsps.add(responses.GET, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_PAGE, status=200)
    rsps.add(responses.POST, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_OK, status=200)

    def index_callback(request):
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(request.url).query)
        gid = int(qs.get("group", ["0"])[0])
        return (
            200,
            {},
            fx.export_page(
                course_id=CURSO_MOODLE,
                groups=grupos_moodle,
                selected_group=gid if gid else None,
            ),
        )

    rsps.add_callback(
        responses.GET, f"{BASE_MOODLE}/grade/export/txt/index.php", callback=index_callback
    )

    def export_callback(request):
        from urllib.parse import parse_qs

        body = parse_qs(request.body or "")
        gid = int((body.get("group") or ["0"])[0])
        return (200, {}, grupos_csv.get(gid, ""))

    rsps.add_callback(
        responses.POST, f"{BASE_MOODLE}/grade/export/txt/export.php", callback=export_callback
    )


def correr(tmp_path: Path, cfg: Path, *args: str) -> int:
    return cli_main(["--config", str(cfg), "--out", str(tmp_path / "salida"), *args])


# ---------------------------------------------------------------------------
# La matriz completa de acciones
# ---------------------------------------------------------------------------
#: (nombre del caso, valor en Moodle, valor en el servidor, acción esperada)
MATRIZ = [
    ("numero_sobre_vacio", "89", 999, "upload"),
    ("numero_sobre_no_presento", "89", NOTA_NO_PRESENTO, "would_overwrite"),
    ("numero_igual", "89", 8.9, "skip_already_set"),
    ("numero_distinto", "89", 7.5, "would_overwrite"),
    ("numero_sobre_retirado", "89", NOTA_RETIRADO, "skip_retirado"),
    ("guion_sobre_vacio", "-", 999, "mark_not_presented"),
    ("guion_sobre_no_presento", "-", NOTA_NO_PRESENTO, "skip_already_set"),
    ("guion_sobre_nota_real", "-", 8.0, "review"),
    ("guion_sobre_retirado", "-", NOTA_RETIRADO, "skip_retirado"),
]


@responses.activate
def test_matriz_completa_de_acciones(fake_np, tmp_path):
    """Los nueve cruces posibles entre lo que dice Moodle y lo que dice el servidor."""
    estudiantes = make_students(len(MATRIZ), cu="42")
    caso_por_cedula = {}
    notas_moodle = {}

    for st, (nombre_caso, valor_moodle, valor_servidor, esperado) in zip(estudiantes, MATRIZ, strict=True):
        caso_por_cedula[st.cedula] = (nombre_caso, esperado)
        notas_moodle[st.cedula] = {COLUMNA: valor_moodle}
        # El "retirado" se detecta cuando TODOS sus instrumentos son 994.
        if valor_servidor == NOTA_RETIRADO:
            notas = {"Tar1": NOTA_RETIRADO, "Tar2": NOTA_RETIRADO, "Proy1": NOTA_RETIRADO}
        else:
            notas = {"Tar1": valor_servidor}
        fake_np.scenario.add_student("42", 1, st.cedula, st.nombre_completo, notas)

    csv_body = fx.export_csv(estudiantes, notas_moodle, columnas=(COLUMNA,))
    mock_moodle(responses.mock, {38525: csv_body})

    cfg = escribir_courses_yml(tmp_path, [(38525, "42", 1)])
    assert correr(tmp_path, cfg, "plan", "--course", "curso-prueba") == 0

    plan = _leer_plan(tmp_path)
    fallos = []
    for fila in plan:
        nombre_caso, esperado = caso_por_cedula[fila["cedula"]]
        if fila["accion"] != esperado:
            fallos.append(f"{nombre_caso}: esperado {esperado}, obtenido {fila['accion']}")
    assert not fallos, "\n".join(fallos)

    # Un plan nunca escribe.
    assert fake_np.journal == []


def _leer_plan(tmp_path: Path) -> list[dict[str, str]]:
    import csv

    planes = list((tmp_path / "salida").glob("notas_plan_*.csv"))
    planes = [p for p in planes if "resultados" not in p.name]
    assert planes, "no se generó ningún plan"
    with planes[0].open(encoding="utf-8-sig", newline="") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]


# ---------------------------------------------------------------------------
# La prueba que sostiene a todas las demás
# ---------------------------------------------------------------------------
@responses.activate
def test_dry_run_dice_la_verdad(fake_np, tmp_path):
    """
    Lo que el modo prueba anuncia es exactamente lo que --commit escribe.

    Si esto no se cumpliera, revisar el plan antes de subir no serviría de
    nada, y con él se caerían todas las demás protecciones.
    """
    estudiantes = make_students(5, cu="42")
    notas = {st.cedula: {COLUMNA: str(70 + i)} for i, st in enumerate(estudiantes)}
    for st in estudiantes:
        fake_np.scenario.add_student("42", 1, st.cedula, st.nombre_completo)

    csv_body = fx.export_csv(estudiantes, notas, columnas=(COLUMNA,))
    mock_moodle(responses.mock, {38525: csv_body})
    cfg = escribir_courses_yml(tmp_path, [(38525, "42", 1)])

    # 1) Prueba: anuncia, no escribe.
    assert correr(tmp_path, cfg, "sync", "--course", "curso-prueba") == 0
    anunciado = sorted(
        (f["cedula"], f["instrumento"], f["nota_local"])
        for f in _leer_plan(tmp_path)
        if f["accion"] == "upload"
    )
    assert fake_np.journal == [], "el modo prueba escribió algo"
    assert anunciado, "no hubo nada que anunciar; la prueba no probaría nada"

    # 2) De verdad.
    assert correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit") == 0
    escrito = sorted((w.cedula, w.instrumento, w.nota) for w in fake_np.journal)

    assert escrito == anunciado


# ---------------------------------------------------------------------------
# Dos grupos
# ---------------------------------------------------------------------------
@responses.activate
def test_dos_grupos_en_el_mismo_cu_no_se_pisan(fake_np, tmp_path):
    """
    La regresión del error que la autodetección del script original esconde.

    Cuando un profesor da dos grupos en el mismo centro universitario,
    ``_discover_grupo_for_cu`` devuelve un solo grupo por CU y los estudiantes
    del otro terminan en ``skip_not_in_roster``, sin que nadie avise. Acá se
    exige que cada grupo escriba lo suyo y solo lo suyo.
    """
    g1 = make_students(3, cu="42", seed=1)
    g2 = make_students(3, cu="42", seed=2)
    assert not ({s.cedula for s in g1} & {s.cedula for s in g2})

    for st in g1:
        fake_np.scenario.add_student("42", 1, st.cedula, st.nombre_completo)
    for st in g2:
        fake_np.scenario.add_student("42", 2, st.cedula, st.nombre_completo)

    mock_moodle(
        responses.mock,
        {
            38525: fx.export_csv(g1, {s.cedula: {COLUMNA: "80"} for s in g1}, columnas=(COLUMNA,)),
            38526: fx.export_csv(g2, {s.cedula: {COLUMNA: "90"} for s in g2}, columnas=(COLUMNA,)),
        },
        grupos_moodle=[(38525, "Grupo 1"), (38526, "Grupo 2")],
    )

    # Mismo CU «42», grupos distintos: el caso exacto que fallaba en silencio.
    cfg = escribir_courses_yml(tmp_path, [(38525, "42", 1), (38526, "42", 2)])
    assert correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit") == 0

    escrituras_g1 = [w for w in fake_np.journal if w.grupo == 1]
    escrituras_g2 = [w for w in fake_np.journal if w.grupo == 2]

    assert {w.cedula for w in escrituras_g1} == {s.cedula for s in g1}
    assert {w.cedula for w in escrituras_g2} == {s.cedula for s in g2}
    assert all(w.nota == "8.0" for w in escrituras_g1)
    assert all(w.nota == "9.0" for w in escrituras_g2)


@responses.activate
def test_aborta_si_los_dos_grupos_traen_los_mismos_estudiantes(fake_np, tmp_path):
    """Si Moodle ignora el filtro, los dos grupos vienen iguales: no se escribe nada."""
    estudiantes = make_students(3, cu="42")
    for st in estudiantes:
        fake_np.scenario.add_student("42", 1, st.cedula, st.nombre_completo)
        fake_np.scenario.add_student("42", 2, st.cedula, st.nombre_completo)

    mismo = fx.export_csv(
        estudiantes, {s.cedula: {COLUMNA: "80"} for s in estudiantes}, columnas=(COLUMNA,)
    )
    mock_moodle(responses.mock, {38525: mismo, 38526: mismo})

    cfg = escribir_courses_yml(tmp_path, [(38525, "42", 1), (38526, "42", 2)])
    assert correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit") == 1
    assert fake_np.journal == []


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------
@responses.activate
def test_correr_dos_veces_no_escribe_dos_veces(fake_np, tmp_path):
    """La segunda corrida no encuentra nada que hacer: todo queda «ya estaba»."""
    estudiantes = make_students(4, cu="42")
    notas = {st.cedula: {COLUMNA: "75"} for st in estudiantes}
    for st in estudiantes:
        fake_np.scenario.add_student("42", 1, st.cedula, st.nombre_completo)

    mock_moodle(
        responses.mock, {38525: fx.export_csv(estudiantes, notas, columnas=(COLUMNA,))}
    )
    cfg = escribir_courses_yml(tmp_path, [(38525, "42", 1)])

    assert correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit") == 0
    assert len(fake_np.journal) == 4

    fake_np.reset_journal()

    assert correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit") == 0
    assert fake_np.journal == [], "la segunda corrida volvió a escribir"
    assert all(f["accion"] == "skip_already_set" for f in _leer_plan(tmp_path))


# ---------------------------------------------------------------------------
# Sobrescritura
# ---------------------------------------------------------------------------
@responses.activate
def test_no_sobrescribe_sin_permiso_explicito(fake_np, tmp_path):
    estudiantes = make_students(2, cu="42")
    for st in estudiantes:
        fake_np.scenario.add_student("42", 1, st.cedula, st.nombre_completo, {"Tar1": 5.0})

    mock_moodle(
        responses.mock,
        {
            38525: fx.export_csv(
                estudiantes, {s.cedula: {COLUMNA: "90"} for s in estudiantes}, columnas=(COLUMNA,)
            )
        },
    )
    cfg = escribir_courses_yml(tmp_path, [(38525, "42", 1)])

    assert correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit") == 0
    assert fake_np.journal == [], "sobrescribió una nota existente sin permiso"
    assert all(f["accion"] == "would_overwrite" for f in _leer_plan(tmp_path))


@responses.activate
def test_sobrescribe_con_permiso_y_deja_justificacion(fake_np, tmp_path):
    estudiantes = make_students(2, cu="42")
    for st in estudiantes:
        fake_np.scenario.add_student("42", 1, st.cedula, st.nombre_completo, {"Tar1": 5.0})

    mock_moodle(
        responses.mock,
        {
            38525: fx.export_csv(
                estudiantes, {s.cedula: {COLUMNA: "90"} for s in estudiantes}, columnas=(COLUMNA,)
            )
        },
    )
    cfg = escribir_courses_yml(tmp_path, [(38525, "42", 1)], allow_update=True)

    assert (
        correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit", "--allow-update") == 0
    )
    assert len(fake_np.journal) == 2
    for w in fake_np.journal:
        assert w.nota == "9.0"
        # El cambio queda registrado con su código de justificación.
        assert w.observacion_codigo == 2005


# ---------------------------------------------------------------------------
# Frenos
# ---------------------------------------------------------------------------
@responses.activate
def test_freno_de_radio_detiene_antes_de_escribir(fake_np, tmp_path):
    estudiantes = make_students(6, cu="42")
    for st in estudiantes:
        fake_np.scenario.add_student("42", 1, st.cedula, st.nombre_completo)

    mock_moodle(
        responses.mock,
        {
            38525: fx.export_csv(
                estudiantes, {s.cedula: {COLUMNA: "80"} for s in estudiantes}, columnas=(COLUMNA,)
            )
        },
    )
    cfg = escribir_courses_yml(tmp_path, [(38525, "42", 1)], max_changes=2)

    codigo = correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit")
    assert codigo == 2
    assert fake_np.journal == [], "el freno no impidió la escritura"

    reporte = next((tmp_path / "salida").glob("reporte_*.md")).read_text(encoding="utf-8")
    assert "freno" in reporte.lower()
    assert "max_changes" in reporte


@responses.activate
def test_un_grupo_frenado_no_detiene_al_otro(fake_np, tmp_path):
    """Un grupo con un problema no debe dejar al resto sin subir."""
    g1 = make_students(6, cu="42", seed=11)
    g2 = make_students(2, cu="01", seed=22)
    for st in g1:
        fake_np.scenario.add_student("42", 1, st.cedula, st.nombre_completo)
    for st in g2:
        fake_np.scenario.add_student("01", 1, st.cedula, st.nombre_completo)

    mock_moodle(
        responses.mock,
        {
            38525: fx.export_csv(g1, {s.cedula: {COLUMNA: "80"} for s in g1}, columnas=(COLUMNA,)),
            38526: fx.export_csv(g2, {s.cedula: {COLUMNA: "90"} for s in g2}, columnas=(COLUMNA,)),
        },
        grupos_moodle=[(38525, "Grupo 1"), (38526, "Grupo 2")],
    )
    cfg = escribir_courses_yml(tmp_path, [(38525, "42", 1), (38526, "01", 1)], max_changes=3)

    correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit")

    escritas = {w.cedula for w in fake_np.journal}
    assert escritas == {s.cedula for s in g2}, "el grupo sano no se subió, o el frenado sí"


@responses.activate
def test_cu_grupo_equivocado_se_detecta_en_vez_de_fallar_callado(fake_np, tmp_path):
    """
    Configurar mal «cu»/«grupo» no debe dar una corrida "exitosa" y vacía.

    El servidor devuelve un roster de otros estudiantes; el plan se llena de
    ``skip_not_in_roster``; eso se trata como error, no como ruido.
    """
    mios = make_students(5, cu="42", seed=101)
    ajenos = make_students(5, cu="42", seed=202)
    # El grupo 7 del servidor tiene gente que no es la mía.
    for st in ajenos:
        fake_np.scenario.add_student("42", 7, st.cedula, st.nombre_completo)

    mock_moodle(
        responses.mock,
        {38525: fx.export_csv(mios, {s.cedula: {COLUMNA: "80"} for s in mios}, columnas=(COLUMNA,))},
    )
    cfg = escribir_courses_yml(tmp_path, [(38525, "42", 7)])

    codigo = correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit")
    assert codigo == 2
    assert fake_np.journal == []

    reporte = next((tmp_path / "salida").glob("reporte_*.md")).read_text(encoding="utf-8")
    assert "courses.yml" in reporte


# ---------------------------------------------------------------------------
# Fallas del servidor
# ---------------------------------------------------------------------------
@responses.activate
def test_sesion_vencida_a_mitad_de_camino_no_pasa_desapercibida(fake_np, tmp_path):
    estudiantes = make_students(4, cu="42")
    for st in estudiantes:
        fake_np.scenario.add_student("42", 1, st.cedula, st.nombre_completo)
    fake_np.scenario.session_dies_after = 2  # muere tras dos escrituras

    mock_moodle(
        responses.mock,
        {
            38525: fx.export_csv(
                estudiantes, {s.cedula: {COLUMNA: "80"} for s in estudiantes}, columnas=(COLUMNA,)
            )
        },
    )
    cfg = escribir_courses_yml(tmp_path, [(38525, "42", 1)])

    codigo = correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit")
    assert codigo != 0, "una sesión vencida se reportó como éxito"


@responses.activate
def test_roster_vacio_no_se_confunde_con_exito(fake_np, tmp_path):
    """El caso «0 estudiantes»: el servidor no da error, devuelve tablas vacías."""
    estudiantes = make_students(4, cu="42")
    fake_np.scenario.force_empty_roster = True

    mock_moodle(
        responses.mock,
        {
            38525: fx.export_csv(
                estudiantes, {s.cedula: {COLUMNA: "80"} for s in estudiantes}, columnas=(COLUMNA,)
            )
        },
    )
    cfg = escribir_courses_yml(tmp_path, [(38525, "42", 1)])

    codigo = correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit")
    assert codigo != 0
    assert fake_np.journal == []
