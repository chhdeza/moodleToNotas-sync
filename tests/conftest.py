"""
Rejas de seguridad de la suite de pruebas.

Las pruebas de este proyecto ejercitan el camino de escritura completo,
incluido ``--commit``. Eso solo es aceptable si es **estructuralmente
imposible** que una corrida de pruebas toque el sistema real de la UNED.

Cuatro capas, todas activas por defecto:

1. ``pytest-socket`` (en ``pyproject.toml``) corta cualquier salida a la red
   que no sea 127.0.0.1. Una prueba que intente llegar a produccion.uned.ac.cr
   **falla**, no escribe.
2. ``_no_real_server`` verifica en cada prueba que ``NP_BASE_URL`` apunte a
   localhost.
3. ``_fake_credentials`` pone credenciales de juguete y aborta la sesión si
   detecta credenciales que parecen reales.
4. Las pruebas de ensayo contra el servidor real llevan la marca
   ``rehearsal`` y quedan excluidas salvo que se pidan a mano.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tests.fake_np.server import FakeNotasParciales  # noqa: E402
from tests.fake_np.state import Instrument, Scenario  # noqa: E402

#: Credenciales de juguete. Que sean obviamente falsas es parte del diseño.
FAKE_ENV = {
    "MOODLE_URL": "http://127.0.0.1:9/moodle",
    "MOODLE_USERNAME": "profesor.prueba",
    "MOODLE_PASSWORD": "contrasena-de-prueba",
    "NP_NTLM_USER": "profesor.prueba",
    "NP_NTLM_PASSWORD": "contrasena-de-prueba",
}

REAL_HOST = "produccion.uned.ac.cr"


def pytest_configure(config: pytest.Config) -> None:
    """Excluye las pruebas de ensayo salvo que se pidan explícitamente."""
    if not config.option.markexpr:
        config.option.markexpr = "not rehearsal"


@pytest.fixture(autouse=True)
def _fake_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Sustituye el entorno por credenciales de juguete.

    Si el ``.env`` del desarrollador ya estaba cargado en el proceso, esto lo
    tapa: ninguna prueba ve nunca una credencial real.
    """
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("NP_BASE_URL", raising=False)
    monkeypatch.delenv("MNSYNC_FENCE_WRITES", raising=False)


@pytest.fixture(autouse=True)
def _no_real_server() -> None:
    """
    Última red de seguridad: aborta si algo apuntó las pruebas al sistema real.

    Corre *después* de que la prueba armó su entorno, así que atrapa también
    a una prueba que hubiera puesto NP_BASE_URL mal.
    """
    yield
    destino = os.environ.get("NP_BASE_URL", "")
    if REAL_HOST in destino:
        pytest.fail(
            f"Una prueba dejó NP_BASE_URL apuntando al sistema real ({destino}). "
            "Las pruebas nunca deben tocar produccion.uned.ac.cr.",
            pytrace=False,
        )


# ---------------------------------------------------------------------------
# Escenario base
# ---------------------------------------------------------------------------
@pytest.fixture
def instruments() -> list[Instrument]:
    """El modelo de evaluación que usan casi todas las pruebas."""
    return [
        Instrument(codigo="Tar1", nombre="Tarea 1 (2)"),
        Instrument(codigo="Tar2", nombre="Tarea 2 (2)"),
        Instrument(codigo="Proy1", nombre="Proyecto 1 (4)"),
    ]


@pytest.fixture
def scenario(instruments: list[Instrument]) -> Scenario:
    """Escenario vacío, listo para que cada prueba lo pueble."""
    return Scenario(instruments=list(instruments))


@pytest.fixture
def fake_np(scenario: Scenario, monkeypatch: pytest.MonkeyPatch):
    """
    Levanta el servidor falso y apunta el cliente hacia él.

    Al salir verifica que la dirección quedó en localhost: la reja se comprueba
    a sí misma en cada prueba que la usa.
    """
    with FakeNotasParciales(scenario) as fake:
        assert fake.base_url.startswith("http://127.0.0.1:"), fake.base_url
        monkeypatch.setenv("NP_BASE_URL", fake.base_url)
        yield fake
