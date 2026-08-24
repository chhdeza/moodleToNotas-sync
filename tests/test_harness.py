"""
Pruebas de la reja misma.

Antes de confiar en cualquier prueba que use el servidor falso, hay que
demostrar que el servidor falso engaña de verdad al cliente real: que
`notasparciales_upload.py` corre entero contra el, sin saber que no es la UNED.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_uploader(args, cwd, env_extra=None):
    """Invoca el script real a traves del lanzador de produccion."""
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "mnsync._uploader_shim", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


CTX = [
    "--ano", "2026", "--pac", "3", "--tipo", "O",
    "--escuela", "03", "--catedra", "253",
    "--encargado", "ARODRIGUEZP", "--tutor", "9999999999",
    "--asignatura", "00883", "--modelo", "4",
]


def test_fake_server_drives_the_real_uploader(fake_np, tmp_path):
    """El cliente real completa un `probe` entero contra el servidor falso."""
    from tests.fixtures.gen import make_students

    for st in make_students(3, cu="42"):
        fake_np.scenario.add_student("42", 1, st.cedula, st.nombre_completo)

    res = run_uploader(
        ["probe", *CTX, "--cu", "42", "--grupo", "1"],
        cwd=tmp_path,
        env_extra={"NP_BASE_URL": fake_np.base_url},
    )

    assert res.returncode == 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert "AUTENTICACION EXITOSA" in res.stdout.replace("Ó", "O").replace("ó", "o")
    # Descubrio los instrumentos del modelo...
    assert "Tar1" in res.stdout and "Proy1" in res.stdout
    # ...y vio el roster completo.
    assert "Estudiantes en el grupo: 3" in res.stdout
    # Un probe no escribe nada, nunca.
    assert fake_np.journal == []


def test_probe_reports_empty_roster_without_crashing(fake_np, tmp_path):
    """El caso '0 estudiantes': el servidor no da error, devuelve tabla vacia."""
    fake_np.scenario.force_empty_roster = True

    res = run_uploader(
        ["probe", *CTX, "--cu", "42", "--grupo", "9"],
        cwd=tmp_path,
        env_extra={"NP_BASE_URL": fake_np.base_url},
    )
    salida = res.stdout + res.stderr
    assert "0 estudiantes" in salida or "ADVERTENCIA" in salida


def test_socket_guard_blocks_the_real_world():
    """
    Cualquier salida a internet en una prueba falla en vez de escribir.

    Se afirma la excepcion concreta de pytest-socket: si algun dia el intento
    fallara por otra razon (DNS, timeout), esta prueba pasaria sin que la reja
    exista, y seria una falsa tranquilidad.
    """
    import requests
    from pytest_socket import SocketConnectBlockedError

    with pytest.raises(SocketConnectBlockedError):
        requests.get("https://produccion.uned.ac.cr/notasparciales", timeout=5)
