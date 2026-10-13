"""
Enrutamiento multi-destino — criterios de aceptación de specs/002.

Un grupo de Moodle reúne estudiantes de muchos centros universitarios (D-02), y
un mismo centro universitario puede estar repartido en varios grupos oficiales
(D-04). El destino de una nota se decide **por estudiante**: el CU acota la
búsqueda, la pertenencia al roster decide.

Cada prueba cita el criterio de aceptación que verifica. Si una falla, el
identificador dice exactamente qué requisito dejó de cumplirse.
"""

from __future__ import annotations

import responses

from tests.fixtures import moodle_html as fx
from tests.fixtures.escenario import correr, escribir_courses_yml, leer_planes, mock_moodle
from tests.fixtures.gen import FakeStudent, make_students

COLUMNA = "Tarea 1 (Real)"

GRUPO_A = 38525
GRUPO_B = 38526


# ---------------------------------------------------------------------------
# Andamiaje
# ---------------------------------------------------------------------------
def csv_de(estudiantes: list[FakeStudent], nota: str = "80") -> str:
    return fx.export_csv(
        estudiantes, {s.cedula: {COLUMNA: nota} for s in estudiantes}, columnas=(COLUMNA,)
    )


# ---------------------------------------------------------------------------
# El escenario real: un tutor, dos grupos de Moodle, varios CU
# ---------------------------------------------------------------------------
class Poblacion:
    """
    Los estudiantes de un tutor, repartidos como en la vida real.

    Grupo A de Moodle reúne estudiantes de tres centros universitarios, y los
    de CU 42 están partidos en dos grupos oficiales distintos (D-04). El grupo B
    aporta más estudiantes a destinos que el grupo A ya alimentaba (D-11).
    """

    def __init__(self) -> None:
        # CU 42 → destino oficial (42, 1)
        self.a_42_g1 = make_students(2, cu="42", seed=11)
        # CU 42 → destino oficial (42, 2). El caso que hoy se pierde en silencio.
        self.a_42_g2 = make_students(1, cu="42", seed=12)
        # CU 01 → destino oficial (01, 1)
        self.a_01_g1 = make_students(2, cu="01", seed=13)

        # Grupo B de Moodle, hacia destinos que el grupo A también alimenta
        self.b_42_g1 = make_students(1, cu="42", seed=14)
        self.b_09_g3 = make_students(1, cu="09", seed=15)

        cedulas = [s.cedula for s in self.todos()]
        assert len(cedulas) == len(set(cedulas)), "el generador repitió una cédula"

    def todos(self) -> list[FakeStudent]:
        return [
            *self.a_42_g1, *self.a_42_g2, *self.a_01_g1,
            *self.b_42_g1, *self.b_09_g3,
        ]

    def grupo_a(self) -> list[FakeStudent]:
        return [*self.a_42_g1, *self.a_42_g2, *self.a_01_g1]

    def grupo_b(self) -> list[FakeStudent]:
        return [*self.b_42_g1, *self.b_09_g3]

    #: Dónde vive cada bloque en Notas Parciales.
    def destinos(self) -> list[tuple[str, int, list[FakeStudent]]]:
        return [
            ("42", 1, [*self.a_42_g1, *self.b_42_g1]),
            ("42", 2, list(self.a_42_g2)),
            ("01", 1, list(self.a_01_g1)),
            ("09", 3, list(self.b_09_g3)),
        ]

    def sembrar(self, scenario) -> None:
        for cu, grupo, estudiantes in self.destinos():
            for st in estudiantes:
                scenario.add_student(cu, grupo, st.cedula, st.nombre_completo)

    def pares_destino(self) -> list[tuple[str, int]]:
        return [(cu, grupo) for cu, grupo, _ in self.destinos()]


# ---------------------------------------------------------------------------
# CA-01 — todo estudiante con destino recibe su nota
# ---------------------------------------------------------------------------
@responses.activate
def test_ca01_cada_estudiante_llega_a_su_destino(fake_np, tmp_path):
    """
    Incluidos los dos del mismo CU que van a destinos distintos (R-01, R-02).

    Es el criterio central: con un solo `cu` por grupo, hoy se sube una fracción
    de los estudiantes y el resto desaparece sin error.
    """
    pob = Poblacion()
    pob.sembrar(fake_np.scenario)

    mock_moodle(
        responses.mock,
        {GRUPO_A: csv_de(pob.grupo_a()), GRUPO_B: csv_de(pob.grupo_b())},
    )
    cfg = escribir_courses_yml(
        tmp_path, grupos_moodle=[GRUPO_A, GRUPO_B], destinos=pob.pares_destino()
    )

    assert correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit") == 0

    # Todos subieron, y cada uno exactamente una vez.
    escrito = {(w.cu, w.grupo, w.cedula) for w in fake_np.journal}
    esperado = {
        (cu, grupo, st.cedula)
        for cu, grupo, estudiantes in pob.destinos()
        for st in estudiantes
    }
    assert escrito == esperado
    assert len(fake_np.journal) == len(esperado), "alguien se escribió dos veces"


@responses.activate
def test_ca01_un_cu_repartido_en_dos_destinos(fake_np, tmp_path):
    """El caso mínimo de D-04, aislado: mismo CU, dos grupos oficiales."""
    pob = Poblacion()
    pob.sembrar(fake_np.scenario)

    mock_moodle(responses.mock, {GRUPO_A: csv_de(pob.grupo_a())})
    cfg = escribir_courses_yml(
        tmp_path, grupos_moodle=[GRUPO_A], destinos=[("42", 1), ("42", 2), ("01", 1)]
    )

    assert correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit") == 0

    en_g1 = {w.cedula for w in fake_np.journal if w.cu == "42" and w.grupo == 1}
    en_g2 = {w.cedula for w in fake_np.journal if w.cu == "42" and w.grupo == 2}

    assert en_g1 == {s.cedula for s in pob.a_42_g1}
    assert en_g2 == {s.cedula for s in pob.a_42_g2}
    assert not (en_g1 & en_g2), "una cédula acabó en los dos destinos"


# ---------------------------------------------------------------------------
# CA-02 — el grupo de Moodle no influye en el destino
# ---------------------------------------------------------------------------
@responses.activate
def test_ca02_el_grupo_de_moodle_no_cambia_el_destino(fake_np, tmp_path):
    """
    Mover un estudiante del grupo A al grupo B de Moodle no altera dónde va (R-03).

    El grupo de Moodle responde *de quién* es el estudiante; una vez recolectado,
    su identidad de grupo se descarta (D-11).
    """
    pob = Poblacion()
    pob.sembrar(fake_np.scenario)
    viajero = pob.a_42_g2[0]

    # Reparto 1: el viajero llega por el grupo A.
    mock_moodle(
        responses.mock,
        {GRUPO_A: csv_de(pob.grupo_a()), GRUPO_B: csv_de(pob.grupo_b())},
    )
    cfg = escribir_courses_yml(
        tmp_path, grupos_moodle=[GRUPO_A, GRUPO_B], destinos=pob.pares_destino()
    )
    assert correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit") == 0

    destino_1 = [(w.cu, w.grupo) for w in fake_np.journal if w.cedula == viajero.cedula]
    assert destino_1 == [("42", 2)]

    # Reparto 2: el mismo estudiante llega por el grupo B.
    fake_np.reset_journal()
    for cu, grupo, estudiantes in pob.destinos():
        for st in estudiantes:
            fake_np.scenario.add_student(cu, grupo, st.cedula, st.nombre_completo)

    grupo_a_sin = [s for s in pob.grupo_a() if s.cedula != viajero.cedula]
    responses.mock.reset()
    mock_moodle(
        responses.mock,
        {GRUPO_A: csv_de(grupo_a_sin), GRUPO_B: csv_de([*pob.grupo_b(), viajero])},
    )
    assert correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit") == 0

    destino_2 = [(w.cu, w.grupo) for w in fake_np.journal if w.cedula == viajero.cedula]
    assert destino_2 == destino_1, "el destino cambió al cambiar de grupo de Moodle"


# ---------------------------------------------------------------------------
# CA-03 — el estudiante sin destino no detiene a los demás
# ---------------------------------------------------------------------------
@responses.activate
def test_ca03_estudiante_sin_destino_no_detiene_al_resto(fake_np, tmp_path):
    """
    Un estudiante en Moodle que no existe en Notas Parciales (D-07).

    Es poco frecuente pero legítimo: se nombra en el reporte (R-07) y el resto
    sube igual (R-06).
    """
    pob = Poblacion()
    pob.sembrar(fake_np.scenario)
    huerfano = make_students(1, cu="42", seed=99)[0]
    assert huerfano.cedula not in {s.cedula for s in pob.todos()}

    mock_moodle(responses.mock, {GRUPO_A: csv_de([*pob.grupo_a(), huerfano])})
    cfg = escribir_courses_yml(
        tmp_path, grupos_moodle=[GRUPO_A], destinos=[("42", 1), ("42", 2), ("01", 1)]
    )

    assert correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit") == 0

    subidos = {w.cedula for w in fake_np.journal}
    assert subidos == {s.cedula for s in pob.grupo_a()}, "el huérfano frenó a los demás"
    assert huerfano.cedula not in subidos

    # Y aparece nombrado en el plan, no desaparece.
    filas = [f for f in leer_planes(tmp_path) if f["cedula"] == huerfano.cedula]
    assert filas, "el estudiante sin destino no aparece en ningún plan"
    assert all(f["accion"] == "skip_not_in_roster" for f in filas)


# ---------------------------------------------------------------------------
# CA-04 — un CU entero sin emparejar detiene ese CU y solo ese
# ---------------------------------------------------------------------------
@responses.activate
def test_ca04_un_cu_sin_destino_no_arrastra_a_los_otros(fake_np, tmp_path):
    """
    Todos los estudiantes de un CU sin destino significa configuración, no matrícula.

    Se detiene ese centro universitario; los demás siguen su curso (R-09).
    """
    pob = Poblacion()
    pob.sembrar(fake_np.scenario)
    # CU 13 completo, sin ningún roster que lo respalde.
    ajenos = make_students(3, cu="13", seed=77)

    mock_moodle(responses.mock, {GRUPO_A: csv_de([*pob.grupo_a(), *ajenos])})
    cfg = escribir_courses_yml(
        tmp_path,
        grupos_moodle=[GRUPO_A],
        destinos=[("42", 1), ("42", 2), ("01", 1), ("13", 1)],
    )

    codigo = correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit")
    assert codigo == 2, "un freno debería haber marcado la corrida"

    subidos = {w.cedula for w in fake_np.journal}
    assert subidos == {s.cedula for s in pob.grupo_a()}, "los CU sanos no subieron"
    assert not (subidos & {s.cedula for s in ajenos})


# ---------------------------------------------------------------------------
# CA-05 — códigos de contexto equivocados: no se escribe nada
# ---------------------------------------------------------------------------
@responses.activate
def test_ca05_contexto_equivocado_no_escribe_nada(fake_np, tmp_path):
    """
    Si ningún CU empareja, la corrida entera apunta a la asignatura equivocada.

    El servidor no da error ante códigos que no corresponden: devuelve tablas
    vacías. Ese silencio es justamente lo que hay que convertir en un alto (R-09).
    """
    pob = Poblacion()
    pob.sembrar(fake_np.scenario)
    fake_np.scenario.force_empty_roster = True

    mock_moodle(responses.mock, {GRUPO_A: csv_de(pob.grupo_a())})
    cfg = escribir_courses_yml(
        tmp_path, grupos_moodle=[GRUPO_A], destinos=[("42", 1), ("42", 2), ("01", 1)]
    )

    codigo = correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit")
    assert codigo != 0
    assert fake_np.journal == [], "escribió con los códigos de contexto equivocados"


# ---------------------------------------------------------------------------
# CA-07 — la configuración no lleva datos personales
# ---------------------------------------------------------------------------
def test_sincronizar_sin_grupos_dice_que_correr(fake_np, tmp_path, capsys):
    """
    Con el archivo recién creado, el error tiene que llevar al paso siguiente.

    Es el primer tropiezo posible de un profesor, y un error que solo dijera
    «no hay grupos» lo dejaría igual de perdido que antes.
    """
    cfg = escribir_courses_yml(tmp_path, grupos_moodle=[])

    assert correr(tmp_path, cfg, "destinos", "--course", "curso-prueba") == 1

    salida = capsys.readouterr().out
    assert "no tiene ningún grupo de Moodle configurado" in salida
    assert "mnsync groups --course curso-prueba" in salida


def test_ca07_la_configuracion_no_contiene_cedulas(tmp_path):
    """
    La configuración la consume también el flujo automático, y vive en un
    repositorio. Lo que entra al historial de git no sale nunca (R-15).
    """
    pob = Poblacion()
    cfg = escribir_courses_yml(
        tmp_path, grupos_moodle=[GRUPO_A, GRUPO_B], destinos=pob.pares_destino()
    )
    texto = cfg.read_text(encoding="utf-8")

    for st in pob.todos():
        assert st.cedula not in texto, f"la configuración filtró la cédula {st.cedula}"


# ---------------------------------------------------------------------------
# El comando «destinos»
# ---------------------------------------------------------------------------
@responses.activate
def test_el_comando_destinos_no_escribe_y_muestra_el_reparto(fake_np, tmp_path, capsys):
    """
    Averigua el reparto por separado, para poder anotarlo y no repetirlo.

    Es de solo lectura: se conecta al sistema oficial pero no escribe nada.
    """
    pob = Poblacion()
    pob.sembrar(fake_np.scenario)

    mock_moodle(
        responses.mock,
        {GRUPO_A: csv_de(pob.grupo_a()), GRUPO_B: csv_de(pob.grupo_b())},
    )
    cfg = escribir_courses_yml(tmp_path, grupos_moodle=[GRUPO_A, GRUPO_B], destinos=None)

    assert correr(tmp_path, cfg, "destinos", "--course", "curso-prueba") == 0
    assert fake_np.journal == [], "el comando destinos escribió algo"

    salida = capsys.readouterr().out
    for cu, grupo, _ in pob.destinos():
        assert f"CU {cu} · grupo {grupo}" in salida
    # Y deja el bloque listo para copiar a courses.yml.
    assert "destinos:" in salida
    assert 'cu: "42"' in salida


# ---------------------------------------------------------------------------
# CA-08 — el descubrimiento de destinos, cuando no se declaran
# ---------------------------------------------------------------------------
@responses.activate
def test_descubre_los_destinos_sondeando(fake_np, tmp_path):
    """
    Sin ``destinos`` en la configuración, se sondean los grupos 1 a 5 (R-05).

    Es lo que hace el asistente la primera vez, y lo que permite que la
    configuración no tenga que escribirse a mano.
    """
    pob = Poblacion()
    pob.sembrar(fake_np.scenario)

    mock_moodle(
        responses.mock,
        {GRUPO_A: csv_de(pob.grupo_a()), GRUPO_B: csv_de(pob.grupo_b())},
    )
    cfg = escribir_courses_yml(tmp_path, grupos_moodle=[GRUPO_A, GRUPO_B], destinos=None)

    assert correr(tmp_path, cfg, "sync", "--course", "curso-prueba", "--commit") == 0

    escrito = {(w.cu, w.grupo, w.cedula) for w in fake_np.journal}
    esperado = {
        (cu, grupo, st.cedula)
        for cu, grupo, estudiantes in pob.destinos()
        for st in estudiantes
    }
    assert escrito == esperado


# ---------------------------------------------------------------------------
# R-05 — el sondeo no repite consultas
# ---------------------------------------------------------------------------
@responses.activate
def test_r05_el_sondeo_no_vuelve_a_planificar_lo_que_ya_encontro(fake_np, tmp_path, monkeypatch):
    """
    La sonda de un destino **es** su plan: mismo destino, mismos argumentos.

    Cada plan es un subproceso, un ingreso NTLM y una tanda de consultas al
    servidor de la UNED. Pedirlo dos veces para saber lo mismo duplica el costo
    de la primera corrida sin ganar nada.
    """
    from mnsync.uploader import Uploader

    pob = Poblacion()
    pob.sembrar(fake_np.scenario)
    mock_moodle(
        responses.mock,
        {GRUPO_A: csv_de(pob.grupo_a()), GRUPO_B: csv_de(pob.grupo_b())},
    )
    # Sin destinos declarados: se descubren sondeando.
    cfg = escribir_courses_yml(tmp_path, grupos_moodle=[GRUPO_A, GRUPO_B])

    pedidos: list[tuple[str, int]] = []
    original = Uploader.plan

    def contar(self, destino, xlsx_paths):
        pedidos.append(destino.key)
        return original(self, destino, xlsx_paths)

    monkeypatch.setattr(Uploader, "plan", contar)

    assert correr(tmp_path, cfg, "plan", "--course", "curso-prueba") == 0
    assert set(pob.pares_destino()) <= set(pedidos)
    assert len(pedidos) == len(set(pedidos)), f"se planificó dos veces: {pedidos}"
