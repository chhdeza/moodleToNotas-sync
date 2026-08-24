"""
Autorización fila por fila de las notas ya puestas (specs/003, A-11).

Cambiar una nota que alguien ya puso es una decisión individual —«este
estudiante tiene 7,5 y Moodle dice 8,9»— y el programa no puede tomarla en
bloque. La regla tiene dos mitades, y las dos se prueban acá:

1. Lo autorizado **se escribe**.
2. Lo no autorizado **no se escribe**, aunque una bandera puesta en otro lado
   diga que sí.

La segunda mitad es la que importa. Un permiso explícito que puede quedar
ampliado por una configuración vieja no es un permiso: es una sugerencia.

Todo corre contra el servidor falso, con el script real de Notas Parciales y
con ``--commit`` de verdad. Las escrituras quedan anotadas en el diario del
servidor falso, y sobre ese diario se afirma: no sobre lo que el programa dice
haber hecho, sino sobre lo que el servidor vio llegar.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import responses

from mnsync.config import load_config, load_credentials
from mnsync.sync import aplicar, preparar
from mnsync.uploader import escribir_plan_autorizado
from tests.fixtures import moodle_html as fx
from tests.fixtures.gen import make_students

BASE_MOODLE = "http://127.0.0.1:9/moodle"
CURSO_MOODLE = 8067
GRUPO_MOODLE = 38525
COLUMNA = "Tarea 1 (Real)"
INSTRUMENTO = "Tar1"
CU = "42"
GRUPO_NP = 1

#: Lo que dice Moodle y lo que ya está en el sistema. Distintos a propósito.
NOTA_MOODLE = "89"
NOTA_PUESTA = 7.5


# ---------------------------------------------------------------------------
# Andamiaje
# ---------------------------------------------------------------------------
def escribir_courses_yml(tmp_path: Path, *, allow_update: bool = False) -> Path:
    lineas = [
        "courses:",
        "  - id: curso-prueba",
        "    moodle:",
        f"      course_id: {CURSO_MOODLE}",
        "      groups:",
        f"        - id: {GRUPO_MOODLE}",
        '          name: "Grupo 1"',
        "    notas_parciales:",
        '      ano: "2026"',
        '      pac: "3"',
        '      tipo: "O"',
        '      asignatura: "00883"',
        '      escuela: "03"',
        "      catedra: 253",
        "      encargado: ARODRIGUEZP",
        "      modelo: 4",
        "    destinos:",
        f'      - cu: "{CU}"',
        f"        grupo: {GRUPO_NP}",
        "    policy:",
        f"      allow_update: {'true' if allow_update else 'false'}",
        "      justificacion_codigo: 2005",
        "      max_changes: 40",
    ]
    p = tmp_path / "courses.yml"
    p.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return p


def mock_moodle(rsps, csv_grupo: str) -> None:
    rsps.add(responses.GET, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_PAGE, status=200)
    rsps.add(responses.POST, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_OK, status=200)
    rsps.add(
        responses.GET,
        f"{BASE_MOODLE}/grade/export/txt/index.php",
        body=fx.export_page(
            course_id=CURSO_MOODLE,
            groups=[(GRUPO_MOODLE, "Grupo 1")],
            selected_group=GRUPO_MOODLE,
        ),
        status=200,
    )
    rsps.add(
        responses.POST, f"{BASE_MOODLE}/grade/export/txt/export.php", body=csv_grupo, status=200
    )


@pytest.fixture
def poblacion(fake_np):
    """
    Tres estudiantes con nota puesta y uno sin nota.

    Los tres primeros dan ``would_overwrite`` —hay algo escrito y Moodle dice
    otra cosa— y el cuarto da ``upload``. Están juntos a propósito: probar solo
    con sobrescrituras no distinguiría «no autorizó nada» de «no escribió nada».
    """
    con_nota = make_students(3, cu=CU, seed=21)
    sin_nota = make_students(1, cu=CU, seed=22)

    for st in con_nota:
        fake_np.scenario.add_student(
            CU, GRUPO_NP, st.cedula, st.nombre_completo, notas={INSTRUMENTO: NOTA_PUESTA}
        )
    for st in sin_nota:
        fake_np.scenario.add_student(CU, GRUPO_NP, st.cedula, st.nombre_completo)

    todos = [*con_nota, *sin_nota]
    csv_grupo = fx.export_csv(
        todos, {s.cedula: {COLUMNA: NOTA_MOODLE} for s in todos}, columnas=(COLUMNA,)
    )
    return con_nota, sin_nota, csv_grupo


def preparar_corrida(tmp_path: Path, csv_grupo: str, *, allow_update: bool = False):
    cfg = load_config(escribir_courses_yml(tmp_path, allow_update=allow_update))
    mock_moodle(responses.mock, csv_grupo)
    return preparar(cfg.courses[0], load_credentials(), tmp_path / "salida")


# ---------------------------------------------------------------------------
# La mitad que escribe
# ---------------------------------------------------------------------------
@responses.activate
def test_la_sobrescritura_autorizada_se_escribe(fake_np, tmp_path, poblacion):
    """Sin esto, autorizar no serviría de nada."""
    con_nota, _, csv_grupo = poblacion
    prep = preparar_corrida(tmp_path, csv_grupo)
    elegido = con_nota[0]

    aplicar(prep, commit=True, autorizadas={(elegido.cedula, INSTRUMENTO)})

    escrituras = fake_np.scenario.writes_for(elegido.cedula, INSTRUMENTO)
    assert len(escrituras) == 1
    assert escrituras[0].nota == "8.9"


# ---------------------------------------------------------------------------
# La mitad que NO escribe: lo que de verdad protege
# ---------------------------------------------------------------------------
@responses.activate
def test_las_no_autorizadas_no_se_tocan(fake_np, tmp_path, poblacion):
    """
    Autorizar una no autoriza a sus compañeras.

    Es el corazón de A-11: si autorizar una fila arrastrara al resto, la
    autorización fila por fila sería una casilla global con más pasos.
    """
    con_nota, _, csv_grupo = poblacion
    prep = preparar_corrida(tmp_path, csv_grupo)
    elegido, *resto = con_nota

    aplicar(prep, commit=True, autorizadas={(elegido.cedula, INSTRUMENTO)})

    for st in resto:
        assert fake_np.scenario.writes_for(st.cedula) == []
        assert fake_np.scenario.valor_actual(CU, GRUPO_NP, st.cedula, INSTRUMENTO) == NOTA_PUESTA


@responses.activate
def test_una_lista_vacia_dice_que_no(fake_np, tmp_path, poblacion):
    """
    No autorizar ninguna es una respuesta, y distinta de no haber preguntado.

    Un conjunto vacío tiene que significar «ninguna». Si se confundiera con
    «sin preferencia», la política del curso volvería a mandar y el profesor
    que revisó y dijo que no vería sus notas cambiadas igual.
    """
    con_nota, sin_nota, csv_grupo = poblacion
    # La política del curso dice que sí. La decisión de la pantalla dice que no.
    prep = preparar_corrida(tmp_path, csv_grupo, allow_update=True)

    aplicar(prep, commit=True, autorizadas=set())

    for st in con_nota:
        assert fake_np.scenario.writes_for(st.cedula) == []
    # …y lo que no era una sobrescritura sí se escribió: no se detuvo todo.
    assert len(fake_np.scenario.writes_for(sin_nota[0].cedula, INSTRUMENTO)) == 1


@responses.activate
def test_la_politica_del_curso_no_amplia_lo_autorizado(fake_np, tmp_path, poblacion):
    """
    Con lista explícita, ``allow_update`` de courses.yml no suma nada.

    Son dos fuentes de permiso para la misma decisión, y la que se tomó
    mirando las filas concretas tiene que ganarle a la que se escribió una vez
    en un archivo hace un cuatrimestre.
    """
    con_nota, _, csv_grupo = poblacion
    prep = preparar_corrida(tmp_path, csv_grupo, allow_update=True)
    elegido = con_nota[0]

    aplicar(prep, commit=True, autorizadas={(elegido.cedula, INSTRUMENTO)})

    sobrescritas = [
        w for w in fake_np.scenario.journal if w.cedula in {s.cedula for s in con_nota}
    ]
    assert [w.cedula for w in sobrescritas] == [elegido.cedula]


# ---------------------------------------------------------------------------
# Lo que queda por escrito
# ---------------------------------------------------------------------------
@responses.activate
def test_el_reporte_no_dice_haber_escrito_lo_que_dejo_pasar(fake_np, tmp_path, poblacion):
    """
    Un registro que exagera lo que hizo no sirve como respaldo.

    Ante una consulta de registro, el reporte es la prueba de qué se tocó y
    qué no. Si contara como escritas las sobrescrituras declinadas, el
    profesor defendería un número que el sistema no tiene.
    """
    con_nota, sin_nota, csv_grupo = poblacion
    prep = preparar_corrida(tmp_path, csv_grupo)

    report = aplicar(
        prep, commit=True, autorizadas={(con_nota[0].cedula, INSTRUMENTO)}
    )

    # Una sobrescritura autorizada + la nota nueva del que no tenía nada.
    assert report.total_escritas == 2
    assert sum(o.sobrescrituras_declinadas for o in report.outcomes) == 2
    assert len(fake_np.scenario.journal) == 2
    assert sin_nota  # el cuarto estudiante existe y entró en la cuenta

    texto = report.to_markdown()
    assert "no se autorizó cambiarlas" in texto


@responses.activate
def test_el_plan_ejecutado_queda_en_disco(fake_np, tmp_path, poblacion):
    """
    Lo que se le entregó al script se puede abrir en Excel después.

    La pantalla se cierra; el archivo queda. Es lo que permite comprobar, un
    mes más tarde, que lo autorizado fue exactamente lo que se ejecutó.
    """
    con_nota, _, csv_grupo = poblacion
    prep = preparar_corrida(tmp_path, csv_grupo)
    elegido = con_nota[0]

    aplicar(prep, commit=True, autorizadas={(elegido.cedula, INSTRUMENTO)})

    autorizados = list((tmp_path / "salida").glob("*_autorizado.csv"))
    assert autorizados, "no quedó el plan que de verdad se ejecutó"

    with autorizados[0].open(encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f))
    sobrescrituras = [r for r in filas if r["accion"] == "would_overwrite"]
    assert [r["cedula"] for r in sobrescrituras] == [elegido.cedula]


# ---------------------------------------------------------------------------
# El recorte, sin servidor de por medio
# ---------------------------------------------------------------------------
CAMPOS = [
    "cu", "grupo", "cedula", "nombre",
    "instrumento", "instrumento_nombre",
    "nota_local", "nota_remota",
    "accion", "motivo", "fuente",
]


def escribir_plan(path: Path, filas: list[tuple[str, str, str]]) -> Path:
    """``filas``: (cédula, instrumento, acción)."""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        for cedula, instrumento, accion in filas:
            w.writerow(
                {
                    **dict.fromkeys(CAMPOS, ""),
                    "cu": CU, "grupo": str(GRUPO_NP), "cedula": cedula,
                    "instrumento": instrumento, "accion": accion,
                }
            )
    return path


def test_el_recorte_conserva_todo_lo_que_no_es_una_sobrescritura(tmp_path):
    """
    El plan recortado sigue siendo el relato completo de la corrida.

    Solo se le quitan las sobrescrituras sin permiso: las subidas normales, los
    retirados y los que ya estaban igual siguen ahí, porque el archivo también
    se lee para entender qué pasó, no solo para ejecutarlo.
    """
    plan = escribir_plan(
        tmp_path / "plan.csv",
        [
            ("001", "Tar1", "upload"),
            ("002", "Tar1", "would_overwrite"),
            ("003", "Tar1", "would_overwrite"),
            ("004", "Tar1", "skip_already_set"),
            ("005", "Tar1", "review"),
        ],
    )

    salida = escribir_plan_autorizado(plan, {("002", "Tar1")}, tmp_path / "recortado.csv")

    with salida.open(encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f))

    assert [r["cedula"] for r in filas] == ["001", "002", "004", "005"]
    assert [r["cedula"] for r in filas if r["accion"] == "would_overwrite"] == ["002"]


def test_el_recorte_distingue_dos_notas_del_mismo_estudiante(tmp_path):
    """
    Autorizar la tarea 1 de alguien no autoriza su proyecto.

    La unidad de decisión es la nota, no la persona: por eso la clave lleva
    cédula **e** instrumento.
    """
    plan = escribir_plan(
        tmp_path / "plan.csv",
        [
            ("001", "Tar1", "would_overwrite"),
            ("001", "Proy1", "would_overwrite"),
        ],
    )

    salida = escribir_plan_autorizado(plan, {("001", "Tar1")}, tmp_path / "recortado.csv")

    with salida.open(encoding="utf-8-sig", newline="") as f:
        assert [r["instrumento"] for r in csv.DictReader(f)] == ["Tar1"]


# ---------------------------------------------------------------------------
# El ensayo no puede quedar registrado como una escritura
# ---------------------------------------------------------------------------
@responses.activate
def test_el_reporte_de_un_ensayo_dice_que_fue_un_ensayo(fake_np, tmp_path, poblacion):
    """
    Un ensayo recorre el camino de escritura entero, con ``commit=True``.

    Sin una marca propia, su reporte sería idéntico al de una corrida real, y
    este archivo es justamente el respaldo de qué se tocó y qué no. Decir que
    escribió lo que no escribió lo vuelve inservible cuando más hace falta.
    """
    _, _, csv_grupo = poblacion
    prep = preparar_corrida(tmp_path, csv_grupo)
    diario = tmp_path / "ensayo.jsonl"

    with prep.uploader.reja_de_escritura(diario):
        report = aplicar(prep, commit=True, autorizadas=set())

    assert report.ensayo
    texto = report.to_markdown()
    assert "ENSAYO" in texto
    assert "se habrían escrito" in report.to_console() or report.total_escritas == 0
    assert "SE ESCRIBIERON las notas" not in texto


@responses.activate
def test_la_reja_del_ensayo_de_verdad_detiene_la_escritura(fake_np, tmp_path, poblacion):
    """
    Lo que importa del ensayo: el servidor no recibe nada.

    Se afirma sobre el diario del servidor falso, no sobre lo que el programa
    dice haber hecho. Una reja se comprueba desde el otro lado.
    """
    _, sin_nota, csv_grupo = poblacion
    prep = preparar_corrida(tmp_path, csv_grupo)

    with prep.uploader.reja_de_escritura(tmp_path / "ensayo.jsonl"):
        aplicar(prep, commit=True, autorizadas=set())

    assert fake_np.scenario.journal == []
    # …y sin la reja, ese mismo estudiante sí habría recibido su nota.
    assert sin_nota


@responses.activate
def test_una_corrida_normal_no_se_marca_como_ensayo(fake_np, tmp_path, poblacion):
    """La marca tiene que salir de la reja, no quedarse pegada."""
    _, _, csv_grupo = poblacion
    prep = preparar_corrida(tmp_path, csv_grupo)

    report = aplicar(prep, commit=True, autorizadas=set())

    assert not report.ensayo
    assert "SE ESCRIBIERON las notas" in report.to_markdown()
