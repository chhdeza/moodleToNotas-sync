"""
El andamiaje de las pruebas de punta a punta.

Un ``courses.yml``, un Moodle simulado y la línea de comandos, iguales para
todos los archivos de prueba que recorren el camino completo. Tenerlos en un
solo lugar asegura que dos pruebas que dicen probar lo mismo arranquen del
mismo escenario.
"""

from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import responses

from mnsync.cli import main as cli_main
from tests.fixtures import moodle_html as fx

BASE_MOODLE = "http://127.0.0.1:9/moodle"
CURSO_MOODLE = 8067


def escribir_courses_yml(
    tmp_path: Path,
    *,
    grupos_moodle: list[int],
    destinos: list[tuple[str, int]] | None = None,
    max_changes: int = 40,
    allow_update: bool = False,
) -> Path:
    """
    Escribe un ``courses.yml`` con el esquema de specs/002.

    ``destinos`` es opcional: si se omite, el programa los descubre sondeando
    (R-05). Nunca contiene cédulas (R-15).
    """
    lineas = [
        "courses:",
        "  - id: curso-prueba",
        "    moodle:",
        f"      course_id: {CURSO_MOODLE}",
        "      groups:",
    ]
    for gid in grupos_moodle:
        lineas += [f"        - id: {gid}", f'          name: "Grupo {gid}"']
    lineas += [
        "    notas_parciales:",
        '      ano: "2026"',
        '      pac: "3"',
        '      tipo: "O"',
        '      asignatura: "00883"',
        '      escuela: "03"',
        "      catedra: 253",
        "      encargado: ARODRIGUEZP",
        "      modelo: 4",
    ]
    if destinos is not None:
        lineas.append("    destinos:")
        for cu, grupo in destinos:
            lineas += [f'      - cu: "{cu}"', f"        grupo: {grupo}"]
    lineas += [
        "    policy:",
        f"      allow_update: {'true' if allow_update else 'false'}",
        "      justificacion_codigo: 2005",
        f"      max_changes: {max_changes}",
    ]
    p = tmp_path / "courses.yml"
    p.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return p


def mock_moodle(
    rsps,
    csv_por_grupo: dict[int, str],
    *,
    grupos_moodle: list[tuple[int, str]] | None = None,
) -> None:
    """Simula Moodle: ingreso, página de exportación y descarga, por grupo."""
    grupos_moodle = grupos_moodle or [(gid, f"Grupo {gid}") for gid in csv_por_grupo]

    rsps.add(responses.GET, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_PAGE, status=200)
    rsps.add(responses.POST, f"{BASE_MOODLE}/login/index.php", body=fx.LOGIN_OK, status=200)

    def index_callback(request):
        qs = parse_qs(urlparse(request.url).query)
        gid = int(qs.get("group", ["0"])[0])
        return (
            200,
            {},
            fx.export_page(
                course_id=CURSO_MOODLE,
                groups=grupos_moodle,
                selected_group=gid if gid else None,
            ),
        )

    rsps.add_callback(
        responses.GET, f"{BASE_MOODLE}/grade/export/txt/index.php", callback=index_callback
    )

    def export_callback(request):
        body = parse_qs(request.body or "")
        gid = int((body.get("group") or ["0"])[0])
        return (200, {}, csv_por_grupo.get(gid, ""))

    rsps.add_callback(
        responses.POST, f"{BASE_MOODLE}/grade/export/txt/export.php", callback=export_callback
    )


def correr(tmp_path: Path, cfg: Path, *args: str) -> int:
    """La línea de comandos, con la configuración y la salida de la prueba."""
    return cli_main(["--config", str(cfg), "--out", str(tmp_path / "salida"), *args])


def leer_planes(tmp_path: Path) -> list[dict[str, str]]:
    """Todas las filas de todos los planes de la corrida, fusionadas."""
    planes = [
        p
        for p in sorted((tmp_path / "salida").glob("notas_plan_*.csv"))
        if "resultados" not in p.name
    ]
    assert planes, "no se generó ningún plan"
    filas: list[dict[str, str]] = []
    for p in planes:
        with p.open(encoding="utf-8-sig", newline="") as f:
            filas += [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]
    return filas
