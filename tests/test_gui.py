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
