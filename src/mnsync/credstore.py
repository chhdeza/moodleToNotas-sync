"""
Guardado de credenciales en el Administrador de credenciales de Windows.

El `.env` sirve mientras el programa lo usa quien lo escribió. Para veinte
profesores en máquinas personales no sirve: un archivo de texto con la
contraseña institucional termina, tarde o temprano, sincronizado a OneDrive.

Acá se guardan con la API del sistema, cifradas contra la cuenta de Windows del
profesor (DPAPI).

**Qué protege y qué no.** Protege de otro usuario de la misma máquina, de que
alguien copie el archivo, y de que una carpeta sincronizada se lleve las
contraseñas a la nube. **No** protege de un programa malicioso corriendo como
ese mismo usuario: la aplicación tiene que poder leerlas, así que cualquier cosa
con su mismo permiso también puede. Conviene decirlo en vez de sugerir una
seguridad que no existe.

Dos entradas separadas, una por sistema (specs/003, A-15). El módulo funciona
igual si ``keyring`` no está instalado o si el sistema no ofrece un almacén: en
ese caso simplemente no hay nada guardado, y las credenciales vienen del entorno
o del `.env` como siempre.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Una entrada por sistema. El prefijo evita chocar con otras aplicaciones.
SERVICIO_MOODLE = "mnsync:moodle"
SERVICIO_NP = "mnsync:notasparciales"


@dataclass(frozen=True)
class StoredCredential:
    """Un usuario y su contraseña, tal como salieron del almacén."""

    username: str
    password: str

    def __repr__(self) -> str:  # pragma: no cover - trivial
        # Igual que en `Credentials`: que un repr accidental en un traceback
        # nunca revele una contraseña.
        return f"StoredCredential(username={self.username!r}, password='***')"


def disponible() -> bool:
    """¿Hay un almacén de credenciales utilizable en esta máquina?"""
    return _backend() is not None


def _backend():
    """
    El módulo ``keyring``, si está instalado y tiene un almacén real detrás.

    Se comprueba el almacén, no solo el import: ``keyring`` se instala igual en
    sistemas donde no hay dónde guardar nada, y ahí devuelve un backend que
    falla al primer uso.
    """
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except ImportError:
        return None

    try:
        if isinstance(keyring.get_keyring(), FailKeyring):
            return None
    except Exception:  # pragma: no cover - depende del sistema
        return None
    return keyring


def leer(servicio: str) -> StoredCredential | None:
    """
    Devuelve la credencial guardada para un sistema, o ``None``.

    Se consulta primero el registro índice, que dice **cuál** es el usuario.
    Preguntar por el servicio a secas no sirve: bajo un mismo servicio hay dos
    registros —el del usuario y el índice— y cuál de los dos contesta depende
    del sistema.

    Nunca levanta: un almacén que falla se trata como un almacén vacío, y las
    credenciales se buscan en los otros orígenes.
    """
    keyring = _backend()
    if keyring is None:
        return None

    try:
        usuario = keyring.get_password(servicio, _CLAVE_USUARIO)
        if not usuario:
            return None
        password = keyring.get_password(servicio, usuario)
    except Exception:  # pragma: no cover - depende del sistema
        return None

    if not password:
        return None
    return StoredCredential(username=usuario, password=password)


def guardar(servicio: str, username: str, password: str) -> None:
    """Guarda (o reemplaza) la credencial de un sistema."""
    keyring = _backend()
    if keyring is None:
        raise CredStoreNoDisponible()

    anterior = leer(servicio)
    if anterior is not None and anterior.username != username:
        # keyring indexa por (servicio, usuario): si cambió el usuario, la
        # entrada vieja quedaría huérfana, y con la contraseña adentro.
        borrar(servicio)

    keyring.set_password(servicio, username, password)
    # El nombre de usuario no es secreto, pero hay que poder recuperarlo sin
    # conocerlo de antemano. Este segundo registro hace de índice.
    keyring.set_password(servicio, _CLAVE_USUARIO, username)


def borrar(servicio: str) -> None:
    """Quita la credencial guardada. No falla si no había ninguna."""
    keyring = _backend()
    if keyring is None:
        return

    usuarios = [_CLAVE_USUARIO]
    actual = leer(servicio)
    if actual is not None:
        # Primero el del usuario: si se borrara el índice antes, ya no habría
        # forma de saber cuál era y la contraseña quedaría guardada para siempre.
        usuarios.insert(0, actual.username)

    for usuario in usuarios:
        try:
            keyring.delete_password(servicio, usuario)
        except Exception:  # pragma: no cover - no había nada que borrar
            pass


#: Registro auxiliar que guarda el nombre de usuario, para poder recuperarlo
#: en sistemas donde `get_credential` no lo devuelve solo.
_CLAVE_USUARIO = "__usuario__"


class CredStoreNoDisponible(RuntimeError):
    """No hay un almacén de credenciales en esta máquina."""

    def __init__(self) -> None:
        super().__init__(
            "Este sistema no ofrece un almacén de credenciales. "
            "Usá el archivo .env en su lugar."
        )
