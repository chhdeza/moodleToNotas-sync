"""
Pruebas de la aplicación de escritorio.

Corren sin pantalla, con el plugin «offscreen» de Qt. Se salta el módulo
entero si PySide6 no está instalado: es una dependencia opcional, y el flujo
de integración continua no la instala.

Lo que se prueba acá es la **lógica**, no el dibujo: qué se le dice al
profesor, cuándo se le deja avanzar y qué pasa cuando algo falla. El aspecto
de un botón no se puede afirmar en una prueba; que un error llegue traducido
y que nadie avance sobre un resultado que no llegó, sí.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6", reason="la interfaz gráfica es opcional")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mnsync.discovery import ContextCheck, InstrumentMatch  # noqa: E402
from mnsync.errors import ConfigError, MoodleError  # noqa: E402
from mnsync.gui.asistente import _parece_suyo, _texto_huerfanos  # noqa: E402
from mnsync.gui.tareas import traducir  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _app():
    """Una sola QApplication para todo el módulo: Qt no admite dos."""
    yield QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
# Traducción de errores
# ---------------------------------------------------------------------------
def test_un_error_del_programa_llega_con_su_remedio():
    """
    Los errores propios ya vienen explicados: se usan tal cual.

    Volver a redactarlos en la ventana los haría divergir del mensaje de la
    línea de comandos, y entonces dos personas con el mismo problema recibirían
    dos explicaciones distintas.
    """
    fallo = traducir(
        MoodleError("Moodle rechazó el usuario o la contraseña.", remedio="Revisá el .env")
    )

    assert fallo.mensaje == "Moodle rechazó el usuario o la contraseña."
    assert fallo.remedio == "Revisá el .env"
    assert "Moodle rechazó" in fallo.texto and "Revisá el .env" in fallo.texto


def test_un_error_inesperado_no_inventa_una_causa():
    """
    Ante algo que no anticipamos, lo honesto es decirlo.

    Una traza de Python en pantalla no es un error reportado: es un error
    escondido detrás de palabras que no significan nada para quien lo ve.
    """
    fallo = traducir(ZeroDivisionError("division by zero"))

    assert "no esperaba" in fallo.mensaje
    assert "No se escribió nada" in fallo.remedio
    # El detalle técnico se conserva, pero para el diagnóstico, no para la vista.
    assert "ZeroDivisionError" in fallo.detalle
    assert "ZeroDivisionError" not in fallo.texto


def test_el_detalle_tecnico_no_se_muestra_solo():
    fallo = traducir(ConfigError("Falta «id».", remedio="Mirá el ejemplo."))
    assert fallo.detalle.startswith("ConfigError")
    assert fallo.detalle not in fallo.texto


# ---------------------------------------------------------------------------
# Preselección de grupos
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "grupo,esperado",
    [
        ("Grupo 2 Hernández Araya", True),
        ("Grupo 2 Hernandez Araya", True),       # sin tildes
        ("GRUPO 1 - HERNANDEZ", True),           # mayúsculas
        ("Grupo 3 Prof. Hernández", True),       # con prefijo
        ("Grupo 4 Solano Mora", False),          # de otra persona
        ("Grupo 5", False),                      # sin nombre
    ],
)
def test_preselecciona_los_grupos_del_profesor(grupo, esperado):
    """
    Marcar de más cuesta un clic; marcar de menos esconde un grupo entero.

    Por eso alcanza con que coincida una palabra, y por eso es preselección y
    no filtro: los demás grupos siguen a la vista (specs/003, A-04).
    """
    assert _parece_suyo(grupo, "Carlos Hernández Araya") is esperado


def test_las_palabras_cortas_no_cuentan():
    """«de», «la» o «A.» harían coincidir a cualquiera con cualquiera."""
    assert not _parece_suyo("Grupo de la mañana", "Ana de la Cruz A.")


# ---------------------------------------------------------------------------
# Qué se dice de quien no aparece
# ---------------------------------------------------------------------------
def _check(**kw) -> ContextCheck:
    base = {"ok": True, "mensaje": "", "estudiantes": 10, "ubicados": 10}
    return ContextCheck(**{**base, **kw})


def test_sin_huerfanos_lo_confirma():
    assert "✓" in _texto_huerfanos(_check())


def test_con_huerfanos_los_nombra_y_aclara_que_el_resto_sube():
    """
    Poco frecuente, y por eso mismo con nombre y apellido (specs/002, R-07).

    Y sobre todo: no puede parecer que algo salió mal. Sus compañeros suben
    igual, y el profesor tiene que quedarse con esa idea, no con un susto.
    """
    texto = _texto_huerfanos(
        _check(sin_destino=(("0100000001", "ANA SOLANO", "42"),), ubicados=9)
    )

    assert "ANA SOLANO" in texto
    assert "0100000001" in texto
    assert "CU 42" in texto
    assert "NO se van a subir" in texto
    assert "El resto sube con normalidad" in texto


def test_un_huerfano_sin_nombre_no_deja_un_hueco():
    """Si Moodle no trajo el nombre, la fila igual tiene que decir algo."""
    texto = _texto_huerfanos(_check(sin_destino=(("0100000002", "", "01"),)))
    assert "(sin nombre)" in texto
    assert "0100000002" in texto


# ---------------------------------------------------------------------------
# La tabla de columnas
# ---------------------------------------------------------------------------
def test_una_columna_sin_emparejar_se_ve_como_tal():
    emparejada = InstrumentMatch(columna="Tarea 1 (Real)", codigo="Tar1", nombre="Tarea 1 (2)")
    suelta = InstrumentMatch(columna="Foro")

    assert emparejada.descripcion == "Tar1 (Tarea 1 (2))"
    assert "no se subirá" in suelta.descripcion


# ---------------------------------------------------------------------------
# El apodo del curso
# ---------------------------------------------------------------------------
def _borrador(nombre: str, ano: str = "2026", pac: str = "4"):
    from mnsync.config import NotasParcialesCtx
    from mnsync.gui.asistente import Borrador
    from mnsync.moodle_export import MoodleCourse

    return Borrador(
        curso_moodle=MoodleCourse(9639, nombre),
        np=NotasParcialesCtx(
            ano=ano, pac=pac, asignatura="00883", escuela="03",
            catedra=253, encargado="X", modelo=4,
        ),
    )


@pytest.mark.parametrize(
    "nombre,esperado",
    [
        ("Introducción a la Ciberseguridad", "introduccion-ciberseguridad-2026-4"),
        ("Redes de Computadoras", "redes-computadoras-2026-4"),
        ("Bases de Datos & Sistemas", "bases-datos-2026-4"),
    ],
)
def test_el_apodo_sale_del_nombre_que_el_profesor_reconoce(nombre, esperado):
    """
    Es lo que va a escribir en la terminal y lo que nombra sus archivos.

    Sale del nombre del curso en Moodle y no del código interno de la
    asignatura, porque «00883» no le dice nada a nadie.
    """
    from mnsync.gui.asistente import _apodo

    assert _apodo(_borrador(nombre)) == esperado


def test_un_curso_sin_nombre_igual_recibe_un_apodo():
    """Nunca puede quedar vacío: nombra archivos y se escribe en la terminal."""
    from mnsync.gui.asistente import _apodo

    assert _apodo(_borrador("...")) == "curso-2026-4"


# ---------------------------------------------------------------------------
# La ventana semanal
# ---------------------------------------------------------------------------
from mnsync.config import Config, Credentials, Destination  # noqa: E402
from mnsync.gui.ventana import (  # noqa: E402
    ORDEN_ACCIONES,
    Contexto,
    EstadoIngresos,
    Ventana,
    _texto_recuento,
    _vencida,
    texto_confirmacion,
)
from mnsync.sync import FilaPlan  # noqa: E402

DESTINO = Destination(cu="42", grupo=1)


def _fila(cedula: str, accion: str, instrumento: str = "Tar1") -> FilaPlan:
    return FilaPlan(
        destino=DESTINO,
        cedula=cedula,
        nombre="ANA SOLANO",
        instrumento=instrumento,
        instrumento_nombre="Tarea 1 (2)",
        nota_local="8.9",
        nota_remota="7.5" if accion == "would_overwrite" else "",
        accion=accion,
        motivo="",
    )


@pytest.fixture
def ventana(tmp_path):
    """Una ventana sin cursos: acá se prueba la tabla, no la configuración."""
    creds = Credentials(
        moodle_url="http://127.0.0.1:9/moodle",
        moodle_username="profesor.prueba",
        moodle_password="x",
        np_user="profesor.prueba",
        np_password="x",
    )
    v = Ventana(Contexto(creds=creds, config=Config(courses=())), tmp_path)
    yield v
    v.close()


def _con_filas(v: Ventana, filas: list[FilaPlan]) -> None:
    v._filas = filas
    v._repintar()


def test_no_existe_ninguna_casilla_que_autorice_todo(ventana):
    """
    A-11 prohíbe una casilla global, así que se afirma su **ausencia**.

    Es una prueba rara —comprueba que algo no está— y por eso vale: si alguien
    agrega «autorizar todas» para ahorrar clics, esto lo detiene antes de que
    llegue a una máquina donde se escriben notas de verdad.
    """
    from PySide6.QtWidgets import QCheckBox

    _con_filas(ventana, [_fila("001", "would_overwrite"), _fila("002", "would_overwrite")])
    assert ventana.findChildren(QCheckBox) == []


def test_solo_las_sobrescrituras_se_pueden_autorizar(ventana):
    """Una nota nueva no necesita permiso: no hay nada que pisar."""
    from PySide6.QtCore import Qt

    _con_filas(ventana, [_fila("001", "upload"), _fila("002", "would_overwrite")])

    normal = ventana.tabla.item(0, 7)
    sobrescritura = ventana.tabla.item(1, 7)
    assert not (normal.flags() & Qt.ItemFlag.ItemIsUserCheckable)
    assert sobrescritura.flags() & Qt.ItemFlag.ItemIsUserCheckable


def test_las_autorizaciones_empiezan_apagadas(ventana):
    """El silencio no autoriza cambiar una nota que alguien ya puso."""
    _con_filas(ventana, [_fila("001", "would_overwrite"), _fila("002", "would_overwrite")])
    assert ventana.autorizadas() == set()


def test_se_autoriza_una_sin_arrastrar_a_las_otras(ventana):
    from PySide6.QtCore import Qt

    _con_filas(ventana, [_fila("001", "would_overwrite"), _fila("002", "would_overwrite")])
    ventana.tabla.item(0, 7).setCheckState(Qt.CheckState.Checked)

    assert ventana.autorizadas() == {("001", "Tar1")}


def test_cambiar_el_filtro_no_pierde_lo_ya_autorizado(ventana):
    """
    El filtro es una lupa, no un borrador.

    Si al acercar la vista se perdiera una autorización dada, el profesor
    escribiría menos de lo que decidió y sin enterarse.
    """
    from PySide6.QtCore import Qt

    _con_filas(ventana, [_fila("001", "would_overwrite"), _fila("002", "upload")])
    ventana.tabla.item(0, 7).setCheckState(Qt.CheckState.Checked)

    ventana.ver.setCurrentIndex(2)   # solo lo que hay que revisar
    ventana.ver.setCurrentIndex(0)   # y de vuelta a todo

    assert ventana.autorizadas() == {("001", "Tar1")}


def test_el_boton_dice_cuantas_sobrescrituras_lleva(ventana):
    """Lo que se está por hacer se lee en el botón, no solo en la tabla."""
    from PySide6.QtCore import Qt

    _con_filas(ventana, [_fila("001", "would_overwrite")])
    ventana._permitir_escritura(True)
    assert ventana.boton_sync.text() == "Sincronizar"

    ventana.tabla.item(0, 7).setCheckState(Qt.CheckState.Checked)
    assert "1 sobrescritura" in ventana.boton_sync.text()


# ---------------------------------------------------------------------------
# Los textos de la ventana
# ---------------------------------------------------------------------------
def test_el_recuento_muestra_las_siete_acciones_aunque_den_cero():
    """
    A-10: la vista no reduce el plan a «se sube» y «no se sube».

    Un cero al lado de «cambiaría una nota existente» no es relleno: es la
    respuesta a la pregunta que más preocupa antes de sincronizar.
    """
    texto = _texto_recuento([_fila("001", "upload")])

    from mnsync.report import ACCIONES

    for accion in ORDEN_ACCIONES:
        assert ACCIONES[accion][0] in texto
    assert len(ORDEN_ACCIONES) == 7


def test_una_accion_que_no_conocemos_igual_se_cuenta():
    """Si el script agrega una acción, aparece; no se traga en silencio."""
    texto = _texto_recuento([_fila("001", "accion_nueva")])
    assert "accion_nueva" in texto


def test_la_confirmacion_advierte_que_no_hay_vuelta_atras():
    """A-13: lo que hay que decir es qué no se puede deshacer desde acá."""
    _, advertencia = texto_confirmacion([_fila("001", "upload")], set())

    assert "NO puede deshacerla" in advertencia
    assert "a mano" in advertencia


def test_la_confirmacion_cuenta_lo_que_se_deja_como_esta():
    filas = [_fila("001", "would_overwrite"), _fila("002", "would_overwrite")]
    resumen, _ = texto_confirmacion(filas, {("001", "Tar1")})

    assert "autorizaste 1" in resumen
    assert "El resto queda como está" in resumen


def test_una_contrasena_rechazada_se_nombra_como_tal():
    """A-14: «parece que cambió tu contraseña», no un fallo indescifrable."""
    from mnsync.errors import MoodleError

    texto = _vencida(MoodleError("Moodle rechazó el usuario o la contraseña."))
    assert "cambió tu contraseña" in texto


def test_un_servidor_caido_no_se_confunde_con_una_contrasena_vencida():
    """
    Mandar a cambiar una contraseña que estaba bien es peor que no decir nada.

    El profesor la cambia, pierde el acceso a los otros sistemas de la UNED, y
    el problema original —que el servidor no respondía— sigue igual.
    """
    from mnsync.errors import UploaderError

    texto = _vencida(UploaderError("El servidor de la UNED no respondió a tiempo."))
    assert "contraseña" not in texto


def test_sin_curso_configurado_no_se_le_reprocha_nada_a_notas_parciales():
    """No se le preguntó, así que no puede decirse que falló."""
    estado = EstadoIngresos(moodle_ok=True, np_consultado=False)

    assert estado.ok
    assert "sin curso configurado" in estado.texto


def test_cambiar_de_curso_no_deja_autorizaciones_colgando(ventana):
    """
    Vaciar la tabla destruye sus celdas del lado de Qt.

    Si las autorizaciones siguieran apuntando a celdas destruidas, la siguiente
    lectura reventaría con un error de C++ que no significa nada para nadie —y
    lo haría justo al pulsar «Sincronizar».
    """
    from PySide6.QtCore import Qt

    _con_filas(ventana, [_fila("001", "would_overwrite")])
    ventana.tabla.item(0, 7).setCheckState(Qt.CheckState.Checked)

    ventana._cambio_de_curso()

    assert ventana.autorizadas() == set()
    assert ventana.tabla.rowCount() == 0
