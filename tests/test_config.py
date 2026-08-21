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
      groups:
{grupos}
    notas_parciales:
      ano: "2026"
      pac: "3"
      asignatura: "00883"
      escuela: "03"
      catedra: 253
      encargado: ARODRIGUEZP
      tutor: "0401780367"
      modelo: 4
{destinos}
    policy:
      allow_update: false
      max_changes: 40
"""

G1 = '        - id: 38525\n          name: "Grupo 1"\n'
G2 = '        - id: 38526\n          name: "Grupo 2"\n'

DESTINOS = (
    '    destinos:\n'
    '      - cu: "42"\n        grupo: 1\n'
    '      - cu: "42"\n        grupo: 2\n'
    '      - cu: "01"\n        grupo: 1\n'
)


def escribir(tmp_path: Path, grupos: str, destinos: str = "", base: str = BASE_YAML) -> Path:
    p = tmp_path / "courses.yml"
    p.write_text(base.format(grupos=grupos, destinos=destinos), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Lo que debe aceptar
# ---------------------------------------------------------------------------
def test_carga_curso_con_dos_grupos(tmp_path):
    cfg = load_config(escribir(tmp_path, G1 + G2, DESTINOS))
    curso = cfg.course("curso-a")
    assert [g.moodle_group_id for g in curso.groups] == [38525, 38526]
    assert curso.groups[0].name == "Grupo 1"


def test_un_cu_puede_tener_varios_destinos(tmp_path):
    """
    Es el caso normal, no una anomalía (specs/001, D-04).

    El esquema anterior no podía ni expresarlo: cada grupo llevaba un solo
    «cu» y un solo «grupo», y los estudiantes del segundo se perdían.
    """
    curso = load_config(escribir(tmp_path, G1, DESTINOS)).course("curso-a")
    cu42 = [d for d in curso.destinations if d.cu == "42"]
    assert [d.grupo for d in cu42] == [1, 2]


def test_los_destinos_son_opcionales(tmp_path):
    """Sin declararlos, se descubren sondeando (specs/002, R-05)."""
    curso = load_config(escribir(tmp_path, G1)).course("curso-a")
    assert curso.destinations == ()


def test_acepta_el_grupo_como_numero_suelto(tmp_path):
    """A mano, escribir solo el número es más cómodo que un bloque."""
    curso = load_config(escribir(tmp_path, "        - 38525\n")).course("curso-a")
    assert curso.groups[0].moodle_group_id == 38525


def test_preserva_los_ceros_a_la_izquierda(tmp_path):
    """
    «01» no puede volverse «1», ni «00883» volverse «883».

    Si YAML los convirtiera a número, el servidor devolvería tablas vacías sin
    dar ningún error, que es el fallo silencioso más difícil de diagnosticar.
    """
    curso = load_config(escribir(tmp_path, G1, DESTINOS)).course("curso-a")
    assert curso.np.asignatura == "00883"
    assert curso.np.escuela == "03"
    assert curso.destinations[2].cu == "01"


def test_slugs_sirven_para_nombrar_archivos(tmp_path):
    curso = load_config(escribir(tmp_path, G1, DESTINOS)).course("curso-a")
    assert curso.groups[0].slug == "g38525"
    assert curso.destinations[0].slug == "cu42_gr1"


# ---------------------------------------------------------------------------
# Lo que debe rechazar
# ---------------------------------------------------------------------------
def test_rechaza_destino_sin_cu(tmp_path):
    malo = '    destinos:\n      - grupo: 1\n'
    with pytest.raises(ConfigError) as ex:
        load_config(escribir(tmp_path, G1, malo))
    assert "cu" in str(ex.value)


def test_rechaza_destino_sin_numero_de_grupo(tmp_path):
    malo = '    destinos:\n      - cu: "42"\n'
    with pytest.raises(ConfigError) as ex:
        load_config(escribir(tmp_path, G1, malo))
    assert "grupo" in str(ex.value)


def test_rechaza_el_mismo_destino_repetido(tmp_path):
    """
    Escribir dos veces sobre el mismo grupo oficial duplicaría el trabajo.

    Que el mismo CU aparezca con grupos distintos sí es correcto; lo que no
    puede repetirse es el par completo.
    """
    choque = (
        '    destinos:\n'
        '      - cu: "42"\n        grupo: 1\n'
        '      - cu: "42"\n        grupo: 1\n'
    )
    with pytest.raises(ConfigError) as ex:
        load_config(escribir(tmp_path, G1, choque))
    assert "dos veces" in str(ex.value)


def test_rechaza_el_mismo_grupo_de_moodle_repetido(tmp_path):
    repetido = '        - id: 38525\n        - id: 38525\n'
    with pytest.raises(ConfigError) as ex:
        load_config(escribir(tmp_path, repetido))
    assert "dos veces" in str(ex.value)


def test_acepta_un_curso_sin_grupos_todavia(tmp_path):
    """
    Es el estado del archivo recién creado, antes de saber los números.

    «mnsync groups» necesita poder leer el archivo para decir cuáles son los
    grupos: exigirlos acá dejaría al profesor sin forma de averiguarlos. Los
    comandos que sí los necesitan se quejan al usarlos, y explican qué correr.
    """
    curso = load_config(escribir(tmp_path, "        []\n")).course("curso-a")
    assert curso.groups == ()


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
