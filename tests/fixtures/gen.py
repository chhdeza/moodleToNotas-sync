"""
Generador de datos sintéticos para las pruebas.

**Nunca** entra al repositorio un dato real de un estudiante. Todo lo que
usan las pruebas se genera acá: cédulas con la forma correcta pero
inventadas, nombres inventados, y centros universitarios reales solo en su
código (que es información pública, no personal).

El generador es determinista: la misma semilla da los mismos datos, así una
prueba que falla se puede reproducir.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

NOMBRES = [
    "ANA", "JUAN", "LUIS", "MARIA", "CARLOS", "SOFIA", "DIEGO", "LAURA",
    "PABLO", "ELENA", "MARCO", "JIMENA", "ANDRES", "PAULA", "TOMAS", "IRENE",
]
APELLIDOS = [
    "SOLANO", "MORA", "CASTRO", "ROJAS", "VARGAS", "JIMENEZ", "CHAVES",
    "ARIAS", "QUESADA", "MADRIGAL", "UMANA", "BRENES", "ZUNIGA", "ALFARO",
]

#: Centros universitarios: código → nombre tal como Moodle escribe la
#: columna "Institución", p. ej. "DESAMPARADOS (42)".
CENTROS = {
    "42": "DESAMPARADOS",
    "01": "SAN JOSE",
    "09": "CARTAGO",
    "13": "HEREDIA",
}


@dataclass(frozen=True)
class FakeStudent:
    """Un estudiante inventado, coherente entre Moodle y Notas Parciales."""

    cedula: str
    nombre: str
    apellidos: str
    cu: str

    @property
    def nombre_completo(self) -> str:
        return f"{self.nombre} {self.apellidos}"

    @property
    def institucion(self) -> str:
        """La columna «Institución» tal como la exporta Moodle."""
        return f"{CENTROS.get(self.cu, 'CENTRO')} ({self.cu})"


def make_cedula(rng: random.Random) -> str:
    """
    Cédula costarricense con la forma correcta (10 dígitos con cero inicial)
    pero sin corresponder a ninguna persona.
    """
    provincia = rng.randint(1, 7)
    resto = rng.randint(0, 99_999_999)
    return f"0{provincia}{resto:08d}"


def make_students(
    n: int,
    cu: str = "42",
    *,
    seed: int = 20260820,
) -> list[FakeStudent]:
    """Genera ``n`` estudiantes distintos para un centro universitario."""
    rng = random.Random(f"{seed}-{cu}-{n}")
    vistos: set[str] = set()
    out: list[FakeStudent] = []
    while len(out) < n:
        cedula = make_cedula(rng)
        if cedula in vistos:
            continue
        vistos.add(cedula)
        out.append(
            FakeStudent(
                cedula=cedula,
                nombre=rng.choice(NOMBRES),
                apellidos=f"{rng.choice(APELLIDOS)} {rng.choice(APELLIDOS)}",
                cu=cu,
            )
        )
    return out
