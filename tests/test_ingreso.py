"""
La comprobación de credenciales al arrancar (specs/003, A-14).

Un aviso equivocado acá hace daño de verdad. Si el programa dice «se venció tu
contraseña» cuando en realidad falló otra cosa, el profesor la cambia, pierde
el acceso a los demás sistemas de la UNED, y el problema original sigue igual.

Por eso hay tres desenlaces y no dos: **sirve**, **la rechazaron**, y **no se
pudo comprobar**. El tercero es el honesto cuando no hay evidencia, y es el que
faltaba: sin él, un fallo del propio programa se reportaba como una credencial
rechazada.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mnsync.config import Course, Credentials, Destination, NotasParcialesCtx
from mnsync.errors import UploaderError
from mnsync.uploader import RunResult, Uploader, parece_rechazo_de_credenciales

CREDS = Credentials(
    moodle_url="http://127.0.0.1:9/moodle",
    moodle_username="profe",
    moodle_password="x",
    np_user="profe",
    np_password="x",
    np_tutor="0000000000",
    np_base_url="http://127.0.0.1:9",
)

NP = NotasParcialesCtx(
    ano="2026", pac="4", tipo="O", asignatura="00883",
    escuela="03", catedra=253, encargado="X", modelo=4,
)


def _curso(destinos: tuple[Destination, ...] = ()) -> Course:
    return Course(
        id="curso-prueba", moodle_course_id=8067, np=NP, groups=(), destinations=destinos
    )


class _Espia(Uploader):
    """Un Uploader que anota los argumentos en vez de ejecutar el script."""

    def __init__(self, *a, resultado: RunResult | None = None, **kw):
        super().__init__(*a, **kw)
        self.llamadas: list[list[str]] = []
        self._resultado = resultado or RunResult(0, "", "")

    def _run(self, args: list[str]) -> RunResult:
        self.llamadas.append(args)
        return self._resultado


# ---------------------------------------------------------------------------
# El fallo que hacía falta arreglar
# ---------------------------------------------------------------------------
def test_la_comprobacion_le_pasa_al_script_los_argumentos_que_exige(tmp_path: Path):
    """
    ``probe`` exige --cu y --grupo aunque el ingreso no dependa de ellos.

    Sin pasarlos, el script muere en su propio analizador de argumentos **antes
    de tocar la red**, y la ventana informaba de un rechazo de credenciales que
    nunca ocurrió. Es el peor error posible de esta pantalla: acusa a una
    contraseña que estaba bien.
    """
    u = _Espia(_curso((Destination(cu="42", grupo=2),)), CREDS, tmp_path)

    u.probar_ingreso()

    args = u.llamadas[0]
    assert args[0] == "probe"
    assert "--cu" in args and args[args.index("--cu") + 1] == "42"
    assert "--grupo" in args and args[args.index("--grupo") + 1] == "2"


def test_se_usa_un_destino_real_del_curso(tmp_path: Path):
    """
    El destino sale de courses.yml, no de un número inventado.

    Uno inventado haría que el servidor conteste sobre un grupo que no es suyo,
    y un fallo de esa consulta se confundiría con un fallo de credenciales.
    """
    u = _Espia(_curso((Destination(cu="09", grupo=3), Destination(cu="42", grupo=1))), CREDS, tmp_path)

    u.probar_ingreso()

    args = u.llamadas[0]
    assert args[args.index("--cu") + 1] == "09"


def test_sin_destinos_todavia_lo_dice_en_vez_de_inventar_uno(tmp_path: Path):
    """
    Un curso recién configurado aún no tiene grupos oficiales averiguados.

    Eso no es una credencial vencida ni un error: es el estado normal de la
    primera vez, y la revisión es justamente lo que los averigua.
    """
    u = _Espia(_curso(), CREDS, tmp_path)

    with pytest.raises(UploaderError) as e:
        u.probar_ingreso()

    assert u.llamadas == [], "no debería haberse ejecutado nada"
    assert not parece_rechazo_de_credenciales(f"{e.value.mensaje} {e.value.remedio}")


def test_un_fallo_del_script_no_se_llama_rechazo(tmp_path: Path):
    """
    «No se pudo comprobar» es distinto de «no aceptó tus datos».

    El mensaje que se ve en pantalla no puede afirmar más de lo que se sabe.
    """
    u = _Espia(
        _curso((Destination(cu="42", grupo=1),)),
        CREDS,
        tmp_path,
        resultado=RunResult(2, "", "error: the following arguments are required: --cu"),
    )

    with pytest.raises(UploaderError) as e:
        u.probar_ingreso()

    assert "no se pudo comprobar" in e.value.mensaje.lower()
    assert "rechaz" not in e.value.mensaje.lower()


# ---------------------------------------------------------------------------
# Reconocer un rechazo de verdad
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "salida,esperado",
    [
        ("Faltan credenciales NTLM en .env", True),
        ("HTTP 401 al pedir la tabla", True),
        ("401 Client Error: Unauthorized for url", True),
        ("Reautenticación NTLM requerida. La sesión ASP.NET podría haber expirado.", True),
        ("error: the following arguments are required: --cu, --grupo", False),
        ("El servidor no devolvió estudiantes para ese grupo", False),
        ("", False),
    ],
)
def test_solo_la_respuesta_del_servidor_decide(salida: str, esperado: bool):
    """
    Ante la duda, no es un rechazo.

    Es preferible decir «no se pudo comprobar» que acusar a una credencial que
    estaba bien: el costo de equivocarse no es simétrico.
    """
    assert parece_rechazo_de_credenciales(salida) is esperado
