"""
Pruebas de la reja de escritura (el modo «ensayo»).

La reja deja pasar todo hacia el servidor real menos ``actualizarNotas``, que
es la única llamada que escribe. De que esto funcione depende que un ensayo
contra producción sea seguro, así que se prueba el mecanismo de verdad y no
solo que la función exista.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import responses

from mnsync._uploader_shim import FENCE_SENTINEL_ENV, WRITE_METHOD, install_write_fence

BASE = "http://127.0.0.1:9/notasparciales/Formularios/CapturaNotas.aspx"


@pytest.fixture
def fence(tmp_path, monkeypatch):
    """Instala la reja y la desmonta al terminar, para no contaminar otras pruebas."""
    import requests

    original = requests.Session.send
    diario = tmp_path / "ensayo.jsonl"
    install_write_fence(diario)
    yield diario
    requests.Session.send = original
    monkeypatch.delenv(FENCE_SENTINEL_ENV, raising=False)


def _leer(diario: Path) -> list[dict]:
    if not diario.exists():
        return []
    return [json.loads(ln) for ln in diario.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_la_escritura_no_sale_a_la_red(fence):
    """
    ``actualizarNotas`` se responde sin transmitirse.

    ``responses`` no registra ninguna llamada porque la reja interviene antes:
    la petición nunca llega a viajar.
    """
    import requests

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        r = requests.Session().post(f"{BASE}/{WRITE_METHOD}", data=json.dumps({"grupo": {}}))
        assert len(rsps.calls) == 0, "la escritura llegó a salir"

    assert r.status_code == 200
    assert r.json()["d"]["HayError"] is False


def test_las_lecturas_pasan_de_largo(fence):
    """El ensayo tiene que ejercitar el servidor real en todo lo que no escribe."""
    import requests

    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, f"{BASE}/funCargarNotas", json={"d": "{}"}, status=200)
        requests.Session().post(f"{BASE}/funCargarNotas", data="{}")
        assert len(rsps.calls) == 1, "una lectura quedó bloqueada de más"


def test_cada_escritura_bloqueada_queda_anotada(fence):
    import requests

    payload = {
        "grupo": {
            "Numero": "1",
            "CentroUniversitario": {"Codigo": "42"},
            "ListaPromedios": [{"Estudiante": {"Cedula": "0117540192"}}],
        }
    }
    requests.Session().post(f"{BASE}/{WRITE_METHOD}", data=json.dumps(payload))

    anotadas = _leer(fence)
    assert len(anotadas) == 1
    entrada = anotadas[0]
    assert entrada["bloqueado"] is True
    assert entrada["metodo"] == WRITE_METHOD
    # El contenido exacto queda registrado: es lo que una corrida real escribiría.
    assert entrada["payload"]["grupo"]["ListaPromedios"][0]["Estudiante"]["Cedula"] == "0117540192"


def test_la_reja_deja_su_marca(fence):
    """
    Sin esta marca, ``mnsync rehearse`` se niega a continuar.

    Una reja que no puede demostrar que está puesta no protege nada.
    """
    import os

    assert os.environ.get(FENCE_SENTINEL_ENV) == "1"


def test_la_verificacion_de_la_reja_corre_de_verdad(tmp_path):
    """
    ``_fence_funciona`` no confía en que "debería andar": lo ejecuta.

    Levanta un subproceso, instala la reja ahí e intenta una escritura real.
    """
    from mnsync.cli import _fence_funciona

    assert _fence_funciona(tmp_path) is True


# ---------------------------------------------------------------------------
# Leer el diario del ensayo
# ---------------------------------------------------------------------------
def test_leer_ensayo_muestra_lo_que_se_habria_escrito(fence, capsys):
    """
    El diario tiene que poder leerse sin saber de JSON.

    Es la pieza que el profesor revisa antes de decidir escribir de verdad;
    si para leerla hiciera falta un comando críptico, no la revisaría.
    """
    import requests

    from mnsync.cli import main as cli_main

    for cedula, instrumento, nota, tipo in [
        ("0117540192", "Tar1", "8.9", 3),
        ("0304560789", "Proy1", "0.02", 1),  # "no presento"
    ]:
        payload = {
            "grupo": {
                "Numero": "1",
                "CentroUniversitario": {"Codigo": "42"},
                "ListaPromedios": [
                    {
                        "Estudiante": {"Cedula": cedula},
                        "ListaNotas": [
                            {
                                "TipoNota": tipo,
                                "Nota": nota,
                                "InstrumentoEvaluacion": {"Codigo": instrumento, "Nombre": "x"},
                            }
                        ],
                    }
                ],
            }
        }
        requests.Session().post(f"{BASE}/{WRITE_METHOD}", data=json.dumps(payload))

    assert cli_main(["leer-ensayo", str(fence)]) == 0

    salida = capsys.readouterr().out
    assert "0117540192" in salida and "8.9" in salida
    # El relleno 0.02 no debe mostrarse como si fuera una nota.
    assert "NO PRESENTÓ" in salida
    assert "0.02" not in salida
    # Y deja claro que nada se envió.
    assert "NINGUNA se envió" in salida


def test_leer_ensayo_sin_escrituras_lo_dice_claro(fence, capsys):
    """Un diario vacío significa que no había nada que subir, no que algo falló."""
    from mnsync.cli import main as cli_main

    fence.write_text("", encoding="utf-8")
    assert cli_main(["leer-ensayo", str(fence)]) == 0
    assert "no interceptó ninguna escritura" in capsys.readouterr().out


def test_leer_ensayo_archivo_inexistente_dice_donde_buscar(capsys):
    from mnsync.cli import main as cli_main

    assert cli_main(["leer-ensayo", "no-existe.jsonl"]) == 1
    assert "rehearsal_*.jsonl" in capsys.readouterr().out
