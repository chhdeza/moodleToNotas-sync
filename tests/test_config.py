"""
Pruebas de la configuración.

Casi todas verifican que algo se **rechace**. Es a propósito: en este proyecto
una configuración mal puesta no produce un error del sistema de la UNED,
produce una corrida que parece exitosa y deja notas sin subir. Por eso la
validación prefiere negarse a arrancar.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mnsync.config import load_config, load_credentials, missing_credentials
from mnsync.errors import ConfigError

BASE_YAML = """
courses:
  - id: curso-a
    moodle:
      course_id: 8067
    notas_parciales:
      ano: "2026"
      pac: "3"
      asignatura: "00883"
      escuela: "03"
      catedra: 253
      encargado: ARODRIGUEZP
      tutor: "0401780367"
      modelo: 4
    groups:
{grupos}
    policy:
      allow_update: false
      max_changes: 40
"""

G1 = '      - moodle_group_id: 38525\n        cu: "42"\n        grupo: 1\n'
G2 = '      - moodle_group_id: 38526\n        cu: "01"\n        grupo: 2\n'


def escribir(tmp_path: Path, grupos: str, base: str = BASE_YAML) -> Path:
    p = tmp_path / "courses.yml"
    p.write_text(base.format(grupos=grupos), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Lo que debe aceptar
# ---------------------------------------------------------------------------
def test_carga_curso_con_dos_grupos(tmp_path):
    cfg = load_config(escribir(tmp_path, G1 + G2))
    curso = cfg.course("curso-a")
    assert [g.moodle_group_id for g in curso.groups] == [38525, 38526]
    assert curso.groups[0].cu == "42"


def test_preserva_los_ceros_a_la_izquierda(tmp_path):
    """
    «01» no puede volverse «1», ni «00883» volverse «883».

    Si YAML los convirtiera a número, el servidor devolvería tablas vacías sin
    dar ningún error, que es el fallo silencioso más difícil de diagnosticar.
    """
    cfg = load_config(escribir(tmp_path, G1 + G2))
    curso = cfg.course("curso-a")
    assert curso.np.asignatura == "00883"
    assert curso.np.escuela == "03"
    assert curso.groups[1].cu == "01"


def test_slug_del_grupo_sirve_para_nombrar_archivos(tmp_path):
    cfg = load_config(escribir(tmp_path, G1))
    assert cfg.course("curso-a").groups[0].slug == "g38525_cu42_gr1"


# ---------------------------------------------------------------------------
# Lo que debe rechazar
# ---------------------------------------------------------------------------
def test_rechaza_grupo_sin_cu(tmp_path):
    malo = '      - moodle_group_id: 38525\n        grupo: 1\n'
    with pytest.raises(ConfigError) as ex:
        load_config(escribir(tmp_path, malo))
    assert "cu" in str(ex.value)


def test_rechaza_grupo_sin_numero_de_grupo(tmp_path):
    malo = '      - moodle_group_id: 38525\n        cu: "42"\n'
    with pytest.raises(ConfigError) as ex:
        load_config(escribir(tmp_path, malo))
    assert "grupo" in str(ex.value)


def test_rechaza_dos_grupos_apuntando_al_mismo_destino(tmp_path):
    """
    Dos grupos de Moodle no pueden ir al mismo (cu, grupo) oficial.

    Si se permitiera, el segundo pisaría las notas del primero sin avisar.
    """
    choque = (
        '      - moodle_group_id: 38525\n        cu: "42"\n        grupo: 1\n'
        '      - moodle_group_id: 38526\n        cu: "42"\n        grupo: 1\n'
    )
    with pytest.raises(ConfigError) as ex:
        load_config(escribir(tmp_path, choque))
    msg = str(ex.value)
    assert "38525" in msg and "38526" in msg


def test_rechaza_el_mismo_grupo_de_moodle_repetido(tmp_path):
    repetido = (
        '      - moodle_group_id: 38525\n        cu: "42"\n        grupo: 1\n'
        '      - moodle_group_id: 38525\n        cu: "01"\n        grupo: 2\n'
    )
    with pytest.raises(ConfigError) as ex:
        load_config(escribir(tmp_path, repetido))
    assert "dos veces" in str(ex.value)


def test_rechaza_curso_sin_grupos(tmp_path):
    with pytest.raises(ConfigError) as ex:
        load_config(escribir(tmp_path, "      []\n"))
    assert "mnsync groups" in str(ex.value)


def test_rechaza_max_changes_negativo(tmp_path):
    yaml = BASE_YAML.replace("max_changes: 40", "max_changes: -1")
    with pytest.raises(ConfigError):
        load_config(escribir(tmp_path, G1, base=yaml))


def test_falta_el_archivo_dice_como_crearlo(tmp_path):
    with pytest.raises(ConfigError) as ex:
        load_config(tmp_path / "no-existe.yml")
    assert "courses.example.yml" in str(ex.value)


def test_yaml_roto_no_muestra_una_traza_de_python(tmp_path):
    p = tmp_path / "courses.yml"
    p.write_text("courses:\n  - id: x\n   mal: [sangría\n", encoding="utf-8")
    with pytest.raises(ConfigError) as ex:
        load_config(p)
    assert "sangría" in str(ex.value) or "formato" in str(ex.value)


def test_curso_inexistente_lista_los_disponibles(tmp_path):
    cfg = load_config(escribir(tmp_path, G1))
    with pytest.raises(ConfigError) as ex:
        cfg.course("no-existe")
    assert "curso-a" in str(ex.value)


# ---------------------------------------------------------------------------
# Credenciales
# ---------------------------------------------------------------------------
def test_credenciales_no_aparecen_en_el_repr():
    """Un repr accidental en un log o una traza no debe revelar la contraseña."""
    creds = load_credentials(require=False)
    texto = repr(creds)
    assert creds.np_password not in texto
    assert creds.moodle_password not in texto
    assert "***" in texto


def test_reporta_cuales_credenciales_faltan(monkeypatch):
    monkeypatch.setenv("NP_NTLM_PASSWORD", "")
    monkeypatch.setenv("MOODLE_PASSWORD", "")
    faltan = missing_credentials(load_credentials(require=False))
    assert set(faltan) == {"NP_NTLM_PASSWORD", "MOODLE_PASSWORD"}
