"""
Pruebas de las averiguaciones previas (specs/003, etapa 2).

Es el cerebro del asistente, y se prueba sin interfaz: cada función devuelve
datos. Si estas pruebas pasan, la ventana solo tiene que mostrarlos.

La que más pesa es la verificación de los códigos de la asignatura. El sistema
de la UNED responde igual de bien a una asignatura que no existe —devuelve
listas vacías— así que la única prueba de que los códigos son correctos es que
aparezcan estudiantes. Convertir ese silencio en un «no» explícito es todo el
trabajo de `verificar_contexto`.
"""

from __future__ import annotations

import responses

from mnsync.config import load_config, load_credentials
from mnsync.discovery import InstrumentMatch, parse_instrument_matches, verificar_contexto
from mnsync.moodle_export import MoodleSession
from tests.fixtures import moodle_html as fx
from tests.test_enrutamiento import (
    GRUPO_A,
    GRUPO_B,
    Poblacion,
    csv_de,
    escribir_courses_yml,
    mock_moodle,
)

BASE_MOODLE = "http://127.0.0.1:9/moodle"


def cargar(cfg_path):
    cfg = load_config(cfg_path)
    return cfg.course("curso-prueba"), load_credentials()


# ---------------------------------------------------------------------------
# Emparejamiento de instrumentos
# ---------------------------------------------------------------------------
SALIDA_DEL_SCRIPT = """
Leídos 40 registros desde 1 archivo(s).
Columnas de nota detectadas: ['Tarea 1 (Real)', 'Proyecto (Real)', 'Foro']

Mapeo de instrumentos (xlsx → notasparciales):
  'Tarea 1 (Real)'
    -> Tar1 (Tarea 1 (2))
  'Proyecto (Real)'
    -> Proy1 (Proyecto Final (4))
  'Foro'
    -> SIN MAPEO (no se subirá esta columna)

ADVERTENCIA: las columnas sin mapeo se reportarán como skip en el plan.
"""


def test_lee_el_emparejamiento_que_anuncia_el_script():
    """
    Se le pregunta al script en vez de reimplementar su lógica.

    Tiene tres pases de emparejamiento, incluido el «item_map» explícito.
    Reimplementarlos arriesgaría que la ventana muestre un emparejamiento
    distinto del que se va a usar de verdad, que es la peor forma de fallar.
    """
    matches = parse_instrument_matches(SALIDA_DEL_SCRIPT)

    assert matches == [
        InstrumentMatch(columna="Tarea 1 (Real)", codigo="Tar1", nombre="Tarea 1 (2)"),
        InstrumentMatch(columna="Proyecto (Real)", codigo="Proy1", nombre="Proyecto Final (4)"),
        InstrumentMatch(columna="Foro"),
    ]


def test_la_columna_sin_emparejar_se_distingue():
    matches = parse_instrument_matches(SALIDA_DEL_SCRIPT)
    sin = [m for m in matches if not m.emparejada]

    assert [m.columna for m in sin] == ["Foro"]
    assert "no se subirá" in sin[0].descripcion


def test_una_salida_sin_mapeo_no_revienta():
    """Si el script cambia su salida, se muestra vacío, no se cae la ventana."""
    assert parse_instrument_matches("") == []
    assert parse_instrument_matches("otra cosa cualquiera") == []


# ---------------------------------------------------------------------------
# Verificación de los códigos de la asignatura
# ---------------------------------------------------------------------------
@responses.activate
def test_verificar_con_codigos_correctos(fake_np, tmp_path):
    """Con los códigos buenos, aparece gente: eso es la prueba."""
    pob = Poblacion()
    pob.sembrar(fake_np.scenario)

    mock_moodle(
        responses.mock,
        {GRUPO_A: csv_de(pob.grupo_a()), GRUPO_B: csv_de(pob.grupo_b())},
    )
    cfg = escribir_courses_yml(tmp_path, grupos_moodle=[GRUPO_A, GRUPO_B], destinos=None)
    course, creds = cargar(cfg)

    check = verificar_contexto(course, creds, tmp_path / "salida")

    assert check.ok
    assert check.estudiantes == len(pob.todos())
    assert check.ubicados == len(pob.todos())
    assert len(check.destinos) == len(pob.destinos())
    assert set(check.cus) == {"42", "01", "09"}
    assert check.sin_destino == ()
    # Y nada de esto escribió una sola nota.
    assert fake_np.journal == []


@responses.activate
def test_verificar_agrupa_los_destinos_por_centro(fake_np, tmp_path):
    """
    El asistente los muestra agrupados, y un CU puede tener varios.

    Es lo que hay que enseñarle al profesor para que confirme el reparto
    (specs/003, A-06).
    """
    pob = Poblacion()
    pob.sembrar(fake_np.scenario)

    mock_moodle(responses.mock, {GRUPO_A: csv_de(pob.grupo_a())})
    cfg = escribir_courses_yml(tmp_path, grupos_moodle=[GRUPO_A], destinos=None)
    course, creds = cargar(cfg)

    check = verificar_contexto(course, creds, tmp_path / "salida")

    assert check.ok
    assert sorted(d.grupo for d in check.por_cu["42"]) == [1, 2]


@responses.activate
def test_verificar_con_codigos_equivocados_lo_dice(fake_np, tmp_path):
    """
    El servidor devuelve listas vacías, no un error. Hay que deducirlo.

    Sin este paso, el profesor se enteraría de que se equivocó semanas después.
    """
    pob = Poblacion()
    pob.sembrar(fake_np.scenario)
    fake_np.scenario.force_empty_roster = True

    mock_moodle(responses.mock, {GRUPO_A: csv_de(pob.grupo_a())})
    cfg = escribir_courses_yml(tmp_path, grupos_moodle=[GRUPO_A], destinos=None)
    course, creds = cargar(cfg)

    check = verificar_contexto(course, creds, tmp_path / "salida")

    assert not check.ok
    assert "asignatura" in check.remedio and "modelo" in check.remedio
    assert "devuelve listas vacías" in check.remedio
    # Cuenta a los de Moodle igual: el problema no es que no haya estudiantes.
    assert check.estudiantes == len(pob.grupo_a())


@responses.activate
def test_verificar_nombra_a_quien_no_aparece(fake_np, tmp_path):
    """Poco frecuente, y por eso mismo con nombre y apellido (specs/002, R-07)."""
    from tests.fixtures.gen import make_students

    pob = Poblacion()
    pob.sembrar(fake_np.scenario)
    huerfano = make_students(1, cu="42", seed=99)[0]

    mock_moodle(responses.mock, {GRUPO_A: csv_de([*pob.grupo_a(), huerfano])})
    cfg = escribir_courses_yml(tmp_path, grupos_moodle=[GRUPO_A], destinos=None)
    course, creds = cargar(cfg)

    check = verificar_contexto(course, creds, tmp_path / "salida")

    assert check.ok, "un solo estudiante sin destino no invalida los códigos"
    assert [c for c, _, _ in check.sin_destino] == [huerfano.cedula]
    assert check.sin_destino[0][1] == huerfano.nombre_completo
    assert check.sin_destino[0][2] == "42"


@responses.activate
def test_verificar_trae_el_emparejamiento_de_columnas(fake_np, tmp_path):
    """Se obtiene de la misma consulta: el asistente lo necesita en el paso siguiente."""
    pob = Poblacion()
    pob.sembrar(fake_np.scenario)

    mock_moodle(responses.mock, {GRUPO_A: csv_de(pob.grupo_a())})
    cfg = escribir_courses_yml(tmp_path, grupos_moodle=[GRUPO_A], destinos=None)
    course, creds = cargar(cfg)

    check = verificar_contexto(course, creds, tmp_path / "salida")

    columnas = [i.columna for i in check.instrumentos]
    assert "Tarea 1 (Real)" in columnas
    emparejada = next(i for i in check.instrumentos if i.columna == "Tarea 1 (Real)")
    assert emparejada.emparejada
    assert emparejada.codigo == "Tar1"


@responses.activate
def test_el_resumen_dice_cuantos_y_donde(fake_np, tmp_path):
    """
    «Funcionó» no alcanza: un número que cuadra es lo que da confianza.

    specs/003, A-05.1.
    """
    pob = Poblacion()
    pob.sembrar(fake_np.scenario)

    mock_moodle(
        responses.mock,
        {GRUPO_A: csv_de(pob.grupo_a()), GRUPO_B: csv_de(pob.grupo_b())},
    )
    cfg = escribir_courses_yml(tmp_path, grupos_moodle=[GRUPO_A, GRUPO_B], destinos=None)
    course, creds = cargar(cfg)

    resumen = verificar_contexto(course, creds, tmp_path / "salida").resumen

    assert f"{len(pob.todos())} estudiante(s)" in resumen
    assert f"{len(pob.destinos())} grupo(s)" in resumen
    assert "3 centro(s)" in resumen


# ---------------------------------------------------------------------------
# Cursos de Moodle
# ---------------------------------------------------------------------------
PAGINA_MIS_CURSOS = """
<html><body>
  <div class="courses">
    <a href="/course/view.php?id=8067" class="coursename">
      <span class="sr-only">Curso</span> Redes de Computadoras
    </a>
    <a href="https://aprende.uned.ac.cr/course/view.php?id=9001">
      Bases de Datos &amp; Sistemas
    </a>
    <a href="/course/view.php?id=8067#section-2">Redes de Computadoras</a>
    <a href="/user/profile.php?id=5">No es un curso</a>
  </div>
</body></html>
"""


@responses.activate
def test_lista_los_cursos_del_profesor():
    """
    La lista evita tener que copiar el número de la barra del navegador.

    Se leen los enlaces a «course/view.php», que es lo único que Moodle escribe
    igual con cualquier tema visual.
    """
    responses.add(responses.GET, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_PAGE)
    responses.add(responses.POST, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_OK)
    responses.add(
        responses.GET, f"{BASE_MOODLE}/my/courses.php", body=PAGINA_MIS_CURSOS, status=200
    )

    s = MoodleSession(BASE_MOODLE)
    s.login("profe", "clave")
    cursos = s.list_courses()

    assert [c.id for c in cursos] == [8067, 9001]
    assert cursos[0].name == "Curso Redes de Computadoras"
    # Las entidades HTML se resuelven: «&amp;» no puede quedar a la vista.
    assert cursos[1].name == "Bases de Datos & Sistemas"


@responses.activate
def test_el_comando_cursos_marca_los_ya_configurados(tmp_path, capsys):
    """Ver de un vistazo cuál de los cursos ya está en courses.yml."""
    from mnsync.cli import main as cli_main

    responses.add(responses.GET, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_PAGE)
    responses.add(responses.POST, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_OK)
    responses.add(responses.GET, f"{BASE_MOODLE}/my/courses.php", body=PAGINA_MIS_CURSOS)

    cfg = escribir_courses_yml(tmp_path, grupos_moodle=[GRUPO_A])
    assert cli_main(["--config", str(cfg), "cursos"]) == 0

    salida = capsys.readouterr().out
    assert "Redes de Computadoras" in salida
    assert "9001" in salida
    # El 8067 es el del courses.yml de prueba.
    assert "✓ configurado" in salida


@responses.activate
def test_el_comando_cursos_explica_si_no_pudo_leer_la_lista(tmp_path, capsys):
    """
    Un tema visual distinto puede impedir leer la lista. No es un fallo.

    El número se saca de la barra de direcciones, y el comando lo explica en
    vez de dejar al profesor con una pantalla vacía.
    """
    from mnsync.cli import main as cli_main

    responses.add(responses.GET, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_PAGE)
    responses.add(responses.POST, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_OK)
    for ruta in ("/my/courses.php", "/my/", "/"):
        responses.add(responses.GET, f"{BASE_MOODLE}{ruta}", body="<html></html>")

    cfg = escribir_courses_yml(tmp_path, grupos_moodle=[GRUPO_A])
    assert cli_main(["--config", str(cfg), "cursos"]) == 0

    salida = capsys.readouterr().out
    assert "course/view.php?id=" in salida
    assert "course_id" in salida


@responses.activate
def test_sin_cursos_devuelve_lista_vacia_y_no_falla():
    """
    Un tema visual distinto puede no dejar leer la lista.

    No es motivo para detener nada: el número de curso se puede escribir a mano.
    """
    responses.add(responses.GET, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_PAGE)
    responses.add(responses.POST, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_OK)
    for ruta in ("/my/courses.php", "/my/", "/"):
        responses.add(responses.GET, f"{BASE_MOODLE}{ruta}", body="<html></html>", status=200)

    s = MoodleSession(BASE_MOODLE)
    s.login("profe", "clave")

    assert s.list_courses() == []


# ---------------------------------------------------------------------------
# La lista de cursos cuando Moodle la dibuja con JavaScript
# ---------------------------------------------------------------------------
PAGINA_CON_SESSKEY = """<!DOCTYPE html>
<html><head>
<script>M.cfg = {"wwwroot":"https://aprende.uned.ac.cr","sesskey":"AbC123xy"};</script>
</head><body>
  <div data-region="courses-view"><!-- lo llena JavaScript --></div>
</body></html>
"""


def _respuesta_del_servicio(cursos):
    return [{"error": False, "data": {"courses": cursos, "nextoffset": 0}}]


@responses.activate
def test_los_cursos_se_piden_al_servicio_que_usa_el_propio_moodle():
    """
    Desde Moodle 4, «Mis cursos» se dibuja con JavaScript.

    El HTML que llega no trae ningún curso, así que raspar enlaces devuelve una
    lista vacía **aunque el ingreso haya sido correcto**: exactamente el fallo
    que se vio en pantalla. Preguntándole al mismo servicio que consulta el
    navegador se obtiene lo que el profesor ve.
    """
    responses.add(responses.GET, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_PAGE)
    responses.add(responses.POST, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_OK)
    responses.add(responses.GET, f"{BASE_MOODLE}/my/", body=PAGINA_CON_SESSKEY, status=200)
    responses.add(
        responses.POST,
        f"{BASE_MOODLE}/lib/ajax/service.php",
        json=_respuesta_del_servicio(
            [
                {"id": 9639, "fullname": "Introducci&oacute;n a la Ciberseguridad"},
                {"id": 8067, "fullname": "Redes de Computadoras"},
            ]
        ),
        status=200,
    )

    s = MoodleSession(BASE_MOODLE)
    s.login("profe", "clave")
    cursos = s.list_courses()

    assert [c.id for c in cursos] == [8067, 9639]
    assert cursos[1].name == "Introducción a la Ciberseguridad"


@responses.activate
def test_la_clave_de_sesion_tambien_se_lee_de_un_formulario():
    """Qué forma toma el sesskey depende del tema visual; se aceptan las dos."""
    responses.add(responses.GET, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_PAGE)
    responses.add(responses.POST, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_OK)
    responses.add(
        responses.GET,
        f"{BASE_MOODLE}/my/",
        body='<form><input type="hidden" name="sesskey" value="ZzZ999" /></form>',
        status=200,
    )
    responses.add(
        responses.POST,
        f"{BASE_MOODLE}/lib/ajax/service.php",
        json=_respuesta_del_servicio([{"id": 7, "fullname": "Algoritmos"}]),
        status=200,
    )

    s = MoodleSession(BASE_MOODLE)
    s.login("profe", "clave")

    assert [c.id for c in s.list_courses()] == [7]


@responses.activate
def test_si_el_servicio_falla_se_vuelve_a_leer_la_pagina():
    """
    Son dos fuentes, no una con reemplazo.

    Un Moodle más viejo, o con el servicio cerrado por configuración, sigue
    teniendo los enlaces en el HTML. Que falle la primera no puede dejar sin
    lista a quien sí la tendría por la segunda.
    """
    responses.add(responses.GET, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_PAGE)
    responses.add(responses.POST, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_OK)
    responses.add(responses.GET, f"{BASE_MOODLE}/my/", body=PAGINA_CON_SESSKEY, status=200)
    responses.add(responses.POST, f"{BASE_MOODLE}/lib/ajax/service.php", status=404)
    responses.add(
        responses.GET, f"{BASE_MOODLE}/my/courses.php", body=PAGINA_MIS_CURSOS, status=200
    )

    s = MoodleSession(BASE_MOODLE)
    s.login("profe", "clave")

    assert [c.id for c in s.list_courses()] == [8067, 9001]


@responses.activate
def test_un_error_del_servicio_no_se_toma_por_una_lista():
    """Moodle contesta 200 con «error: true»; eso no es una lista de cursos."""
    responses.add(responses.GET, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_PAGE)
    responses.add(responses.POST, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_OK)
    responses.add(responses.GET, f"{BASE_MOODLE}/my/", body=PAGINA_CON_SESSKEY, status=200)
    responses.add(
        responses.POST,
        f"{BASE_MOODLE}/lib/ajax/service.php",
        json=[{"error": True, "exception": {"message": "Invalid session key"}}],
        status=200,
    )
    for ruta in ("/my/courses.php", "/"):
        responses.add(responses.GET, f"{BASE_MOODLE}{ruta}", body="<html></html>", status=200)

    s = MoodleSession(BASE_MOODLE)
    s.login("profe", "clave")

    assert s.list_courses() == []
