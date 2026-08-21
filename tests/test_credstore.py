"""
Pruebas del almacén de credenciales.

Nunca tocan el Administrador de credenciales de la máquina: se monta un
almacén falso en memoria con la misma interfaz que ``keyring``. Una prueba que
guardara una contraseña de verdad en el sistema dejaría basura difícil de
encontrar y más difícil de borrar.
"""

from __future__ import annotations

import pytest

from mnsync import credstore
from mnsync.config import load_credentials


class FakeKeyring:
    """Un ``keyring`` en memoria, con lo justo que usa ``credstore``."""

    def __init__(self, *, falla: bool = False):
        self.datos: dict[tuple[str, str], str] = {}
        self.falla = falla

    def get_password(self, servicio: str, usuario: str) -> str | None:
        if self.falla:
            raise RuntimeError("el almacén del sistema no responde")
        return self.datos.get((servicio, usuario))

    def set_password(self, servicio: str, usuario: str, password: str) -> None:
        if self.falla:
            raise RuntimeError("el almacén del sistema no responde")
        self.datos[(servicio, usuario)] = password

    def delete_password(self, servicio: str, usuario: str) -> None:
        if self.falla:
            raise RuntimeError("el almacén del sistema no responde")
        del self.datos[(servicio, usuario)]


@pytest.fixture
def almacen(monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    fake = FakeKeyring()
    monkeypatch.setattr(credstore, "_backend", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Lo básico
# ---------------------------------------------------------------------------
def test_guarda_y_recupera(almacen):
    credstore.guardar(credstore.SERVICIO_MOODLE, "profe.perez", "secreta")

    cred = credstore.leer(credstore.SERVICIO_MOODLE)
    assert cred is not None
    assert cred.username == "profe.perez"
    assert cred.password == "secreta"


def test_los_dos_sistemas_no_se_pisan(almacen):
    credstore.guardar(credstore.SERVICIO_MOODLE, "usuario.moodle", "clave-moodle")
    credstore.guardar(credstore.SERVICIO_NP, "usuario.np", "clave-np")

    assert credstore.leer(credstore.SERVICIO_MOODLE).password == "clave-moodle"
    assert credstore.leer(credstore.SERVICIO_NP).password == "clave-np"


def test_sin_nada_guardado_devuelve_none(almacen):
    assert credstore.leer(credstore.SERVICIO_NP) is None


def test_borrar_deja_el_almacen_limpio(almacen):
    """
    Nada de la contraseña puede quedar atrás.

    Si «borrar» dejara el registro del usuario en pie, la contraseña seguiría
    en el sistema sin que nada la muestre: lo peor de los dos mundos.
    """
    credstore.guardar(credstore.SERVICIO_MOODLE, "profe.perez", "secreta")
    credstore.borrar(credstore.SERVICIO_MOODLE)

    assert credstore.leer(credstore.SERVICIO_MOODLE) is None
    assert almacen.datos == {}


def test_cambiar_de_usuario_no_deja_la_clave_vieja(almacen):
    """
    Al cambiar el usuario, la entrada anterior quedaría huérfana.

    Huérfana pero con la contraseña adentro, y sin nada que apunte a ella: no
    habría forma de borrarla después.
    """
    credstore.guardar(credstore.SERVICIO_NP, "usuario.viejo", "clave-vieja")
    credstore.guardar(credstore.SERVICIO_NP, "usuario.nuevo", "clave-nueva")

    assert credstore.leer(credstore.SERVICIO_NP).username == "usuario.nuevo"
    assert "clave-vieja" not in almacen.datos.values()


def test_borrar_lo_que_no_existe_no_falla(almacen):
    credstore.borrar(credstore.SERVICIO_MOODLE)  # no debe levantar


# ---------------------------------------------------------------------------
# Cuando no hay almacén
# ---------------------------------------------------------------------------
def test_sin_almacen_leer_devuelve_none(monkeypatch):
    """En Linux sin backend, o sin keyring instalado, no pasa nada."""
    monkeypatch.setattr(credstore, "_backend", lambda: None)

    assert credstore.disponible() is False
    assert credstore.leer(credstore.SERVICIO_MOODLE) is None


def test_sin_almacen_guardar_lo_dice_claro(monkeypatch):
    monkeypatch.setattr(credstore, "_backend", lambda: None)

    with pytest.raises(credstore.CredStoreNoDisponible) as ex:
        credstore.guardar(credstore.SERVICIO_MOODLE, "u", "p")
    assert ".env" in str(ex.value)


def test_un_almacen_que_falla_se_trata_como_vacio(monkeypatch):
    """
    Un almacén roto no puede tumbar una sincronización.

    Las credenciales pueden venir del entorno o del `.env`; que el sistema no
    conteste es motivo para seguir buscando, no para detenerse.
    """
    monkeypatch.setattr(credstore, "_backend", lambda: FakeKeyring(falla=True))

    assert credstore.leer(credstore.SERVICIO_MOODLE) is None


# ---------------------------------------------------------------------------
# Precedencia entre los tres orígenes
# ---------------------------------------------------------------------------
def test_el_entorno_le_gana_al_almacen(almacen, monkeypatch, tmp_path):
    """
    En GitHub Actions los secretos llegan por el entorno y mandan ellos.

    Si el almacén de la máquina pudiera ganarles, una corrida automática usaría
    credenciales que no son las que se le pasaron.
    """
    credstore.guardar(credstore.SERVICIO_MOODLE, "del.almacen", "clave-almacen")
    monkeypatch.setenv("MOODLE_USERNAME", "del.entorno")
    monkeypatch.setenv("MOODLE_PASSWORD", "clave-entorno")

    creds = load_credentials(tmp_path / "no-existe.env")

    assert creds.moodle_username == "del.entorno"
    assert creds.moodle_password == "clave-entorno"


def test_el_almacen_completa_lo_que_falta(almacen, monkeypatch, tmp_path):
    """Es el caso de la aplicación de escritorio: no hay entorno ni .env."""
    for name in ("MOODLE_USERNAME", "MOODLE_PASSWORD", "NP_NTLM_USER", "NP_NTLM_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    credstore.guardar(credstore.SERVICIO_MOODLE, "profe.perez", "clave-moodle")
    credstore.guardar(credstore.SERVICIO_NP, "pperez", "clave-np")

    creds = load_credentials(tmp_path / "no-existe.env")

    assert creds.moodle_username == "profe.perez"
    assert creds.moodle_password == "clave-moodle"
    assert creds.np_user == "pperez"
    assert creds.np_password == "clave-np"


def test_la_contrasena_no_aparece_en_el_repr(almacen):
    """
    Un traceback no puede filtrar una contraseña.

    Es la misma defensa que ya tiene `Credentials`, y por la misma razón: los
    tracebacks terminan pegados en correos y en mensajes de soporte.
    """
    credstore.guardar(credstore.SERVICIO_NP, "pperez", "clave-secreta")
    texto = repr(credstore.leer(credstore.SERVICIO_NP))

    assert "clave-secreta" not in texto
    assert "pperez" in texto
