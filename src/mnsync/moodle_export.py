"""
Descarga del libro de calificaciones de Moodle.

Esto es lo único que no existía en ninguno de los dos repositorios previos.
``moodle_download.py`` baja *archivos de entregas*; acá bajamos las *notas*,
que es lo que hay que llevar a Notas Parciales.

No se inventa ningún endpoint: se usa la misma exportación que el profesor
hace hoy a mano (Calificaciones → Exportar), manejada por programa. El
formulario se **repite**, no se reconstruye — ver ``htmlform.py``.

Dos rejas antes de devolver nada, las dos ruidosas a propósito:

  - **Alcance:** que el servidor haya aceptado de verdad el filtro de grupo.
    Si lo ignora, la descarga trae el curso entero, incluidos estudiantes de
    otros profesores, y subir eso escribiría notas ajenas.
  - **Columnas:** que vengan «Número de ID» (cédula) e «Institución» (CU).
    Sin ellas no hay forma de emparejar a nadie con el sistema oficial.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .errors import MoodleError, ScopeError
from .htmlform import Form, find_form, find_select

#: Columnas de identidad que exporta Moodle y que necesitamos sí o sí.
COL_NOMBRE = "Nombre"
COL_APELLIDOS = "Apellido(s)"
COL_CEDULA = "Número de ID"
COL_INSTITUCION = "Institución"

COLUMNAS_IDENTIDAD = (COL_NOMBRE, COL_APELLIDOS, COL_CEDULA, COL_INSTITUCION)

#: Otras columnas que Moodle puede exportar y que no son notas.
COLUMNAS_NO_NOTA = frozenset(
    {
        "Correo electrónico",
        "Dirección de correo",
        "Departamento",
        "Último descargado de este curso",
        "Idnumber",
        "ID",
    }
)


@dataclass
class MoodleGroup:
    """Un grupo tal como lo ve Moodle."""

    id: int
    name: str

    def __str__(self) -> str:
        return f"{self.name} (id {self.id})"


@dataclass
class GradeExport:
    """El resultado de exportar un grupo: encabezados y filas ya limpias."""

    headers: list[str]
    rows: list[dict[str, str]]
    group_id: int
    #: Encabezados que son columnas de nota (todo lo que no es identidad).
    grade_headers: list[str] = field(default_factory=list)

    @property
    def cedulas(self) -> set[str]:
        return {r.get(COL_CEDULA, "").strip() for r in self.rows if r.get(COL_CEDULA, "").strip()}

    def __len__(self) -> int:
        return len(self.rows)


class MoodleSession:
    """
    Sesión autenticada contra Moodle.

    El login replica ``check_moodle_login()`` de ``moodle_download.py``: pide
    el formulario, saca el ``logintoken`` (el anti-CSRF de Moodle) y envía las
    credenciales. Una sola sesión sirve para todos los grupos de una corrida.
    """

    def __init__(self, base_url: str, *, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "mnsync/0.1 (Moodle grade sync)"})
        self._logged_in = False

    # --- autenticación ---------------------------------------------------
    def login(self, username: str, password: str) -> None:
        login_url = f"{self.base_url}/login/index.php"

        try:
            r = self.session.get(login_url, timeout=self.timeout)
            r.raise_for_status()
        except requests.RequestException as e:
            raise MoodleError(
                f"No se pudo conectar con Moodle en «{self.base_url}».",
                remedio=(
                    "Revisá MOODLE_URL en el archivo .env y que tengás conexión a "
                    f"internet. Detalle técnico: {e}"
                ),
            ) from e

        m = re.search(r'name="logintoken"[^>]*value="([^"]+)"', r.text)
        if not m:
            raise MoodleError(
                "La página de ingreso de Moodle no tiene la forma esperada.",
                remedio=(
                    f"Verificá que MOODLE_URL sea correcto. Ahora dice «{self.base_url}» "
                    "y debería ser algo como https://aprende.uned.ac.cr"
                ),
            )

        try:
            resp = self.session.post(
                login_url,
                data={
                    "username": username,
                    "password": password,
                    "logintoken": m.group(1).strip(),
                    "anchor": "",
                },
                timeout=self.timeout,
                allow_redirects=True,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise MoodleError(f"Error al iniciar sesión en Moodle: {e}") from e

        if "loginerrors" in resp.text or "invalidlogin" in resp.text.lower():
            raise MoodleError(
                "Moodle rechazó el usuario o la contraseña.",
                remedio=(
                    "Revisá MOODLE_USERNAME y MOODLE_PASSWORD en el archivo .env. "
                    "Son los mismos con los que entrás a calificar."
                ),
            )
        if "/login/index.php" in (resp.url or "") and "logintoken" in resp.text:
            raise MoodleError(
                "Moodle no completó el ingreso.",
                remedio="Probá entrar a Moodle desde el navegador; puede haber un aviso pendiente.",
            )

        self._logged_in = True

    def _require_login(self) -> None:
        if not self._logged_in:
            raise MoodleError("Hay que iniciar sesión en Moodle antes de descargar notas.")

    # --- grupos -----------------------------------------------------------
    def _export_index_url(self, course_id: int, group_id: int | None = None) -> str:
        url = f"{self.base_url}/grade/export/txt/index.php?id={course_id}"
        if group_id is not None:
            url += f"&group={group_id}"
        return url

    def _get_export_page(self, course_id: int, group_id: int | None = None) -> str:
        self._require_login()
        url = self._export_index_url(course_id, group_id)
        try:
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
        except requests.RequestException as e:
            raise MoodleError(
                f"No se pudo abrir la página de exportación del curso {course_id}.",
                remedio=(
                    "Verificá que el «course_id» del archivo courses.yml sea correcto "
                    f"y que tengás permiso de ver las calificaciones. Detalle: {e}"
                ),
            ) from e
        return r.text

    def list_groups(self, course_id: int) -> list[MoodleGroup]:
        """
        Los grupos del curso, según el selector que muestra Moodle.

        Es la fuente de los números que van en ``courses.yml``.
        """
        html = self._get_export_page(course_id)
        sel = find_select(html, "group")
        if sel is None:
            return []
        grupos: list[MoodleGroup] = []
        for op in sel.options:
            try:
                gid = int(op.value)
            except (TypeError, ValueError):
                continue
            if gid <= 0:  # "Todos los participantes"
                continue
            grupos.append(MoodleGroup(id=gid, name=op.label or f"Grupo {gid}"))
        return grupos

    # --- exportación ------------------------------------------------------
    def export_group(self, course_id: int, group_id: int) -> GradeExport:
        """
        Exporta las notas de **un** grupo y devuelve las filas ya limpias.

        Verifica el alcance antes de devolver nada.
        """
        html = self._get_export_page(course_id, group_id)
        self._assert_group_scope(html, course_id, group_id)

        form = find_form(html, contains_field="itemids")
        if form is None:
            form = find_form(html, contains_field="sesskey")
        if form is None:
            raise MoodleError(
                "No se encontró el formulario de exportación de calificaciones.",
                remedio=(
                    "Puede que este curso no tenga calificaciones todavía, o que tu "
                    "usuario no tenga permiso para exportarlas. Probá el mismo paso a "
                    "mano en Moodle: Calificaciones → Exportar → Archivo de texto."
                ),
            )

        csv_text = self._post_export(form, course_id, group_id)
        return self._parse_csv(csv_text, group_id)

    def _assert_group_scope(self, html: str, course_id: int, group_id: int) -> None:
        """
        Confirma que Moodle aceptó el filtro de grupo.

        Si el curso no tiene grupos activados, o el id no existe, Moodle **no
        da error**: sirve la página con el curso entero. Descargar eso y
        subirlo escribiría notas de estudiantes de otros profesores, así que
        acá se corta.
        """
        sel = find_select(html, "group")
        if sel is None:
            raise ScopeError(
                f"El curso {course_id} de Moodle no muestra un selector de grupos, "
                f"así que no se puede limitar la descarga al grupo {group_id}.",
                remedio=(
                    "Descargar el curso entero traería estudiantes de otros profesores. "
                    "Revisá que el curso tenga grupos configurados («Modo de grupo» "
                    "separado o visible) y que el «moodle_group_id» sea correcto. Para "
                    "ver los grupos: mnsync groups --course <id>"
                ),
            )

        elegido = sel.selected_value
        if elegido is None or str(elegido) != str(group_id):
            disponibles = ", ".join(f"{o.label.strip()}={o.value}" for o in sel.options if o.value)
            raise ScopeError(
                f"Se pidió el grupo {group_id}, pero Moodle quedó mostrando "
                f"«{elegido if elegido is not None else 'ninguno'}».",
                remedio=(
                    "El número de grupo no corresponde a este curso. Grupos que ofrece "
                    f"Moodle: {disponibles or '(ninguno)'}. Corregí «moodle_group_id» en "
                    "courses.yml."
                ),
            )

    def _post_export(self, form: Form, course_id: int, group_id: int) -> str:
        """Reenvía el formulario cosechado, marcando todos los instrumentos."""
        payload = form.payload()

        # Marcar TODAS las casillas de instrumento, estén marcadas o no.
        for name in form.checkboxes:
            if name.startswith("itemids"):
                payload[name] = "1"

        # Opciones de formato. Se fuerzan porque el plan de subida depende de
        # ellas: nota real (no porcentaje) y sin comentarios.
        payload.update(
            {
                "id": str(course_id),
                "group": str(group_id),
                "export_feedback": "0",
                "export_onlyactive": "1",
                "display[real]": "1",
                "decimals": "2",
                "separator": "comma",
                "submitbutton": "Descargar",
            }
        )
        for opcional in ("display[percentage]", "display[letter]"):
            payload.pop(opcional, None)

        action = form.action or f"{self.base_url}/grade/export/txt/export.php"
        if action.startswith("/"):
            action = f"{self.base_url}{action}"
        elif not action.startswith("http"):
            action = f"{self.base_url}/grade/export/txt/{action}"

        try:
            r = self.session.post(action, data=payload, timeout=self.timeout)
            r.raise_for_status()
        except requests.RequestException as e:
            raise MoodleError(f"Falló la descarga de calificaciones: {e}") from e

        if "<html" in r.text[:400].lower():
            raise MoodleError(
                "Moodle devolvió una página web en vez del archivo de calificaciones.",
                remedio=(
                    "Suele pasar cuando la sesión venció o falta un permiso. Probá de "
                    "nuevo; si sigue, exportá a mano y usá la opción --from-xlsx."
                ),
            )

        # Decodificar los bytes a mano, sin hacerle caso al encabezado.
        #
        # Moodle exporta en UTF-8, pero cuando no manda "charset" en el
        # Content-Type, `requests` asume ISO-8859-1 (es lo que dice la norma
        # para text/*). Con esa suposición el propio encabezado «Número de ID»
        # llega como «NÃºmero de ID» y la verificación de columnas falla, o —peor—
        # pasa con los nombres de los estudiantes destrozados.
        return _decode_export(r.content)

    # --- lectura del CSV --------------------------------------------------
    def _parse_csv(self, text: str, group_id: int) -> GradeExport:
        text = text.lstrip("﻿")
        reader = csv.DictReader(io.StringIO(text))
        headers = [h.strip() for h in (reader.fieldnames or [])]
        if not headers:
            raise MoodleError("La exportación de Moodle vino vacía (sin encabezados).")

        _assert_identity_columns(headers)

        rows = [{(k or "").strip(): (v or "").strip() for k, v in raw.items()} for raw in reader]
        grade_headers = [h for h in headers if is_grade_column(h)]

        return GradeExport(
            headers=headers,
            rows=rows,
            group_id=group_id,
            grade_headers=grade_headers,
        )


def _decode_export(raw: bytes) -> str:
    """
    Convierte a texto la descarga de Moodle.

    UTF-8 primero (con o sin BOM), que es lo que Moodle usa. Si esos bytes no
    fueran UTF-8 válido se recurre a latin-1, que nunca falla, para poder
    mostrar un error legible en vez de reventar con una excepción de códec.
    """
    for codec in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def is_grade_column(header: str) -> bool:
    """
    ¿Es una columna de nota?

    Todo lo que no sea identidad ni metadato conocido se trata como nota. Se
    hace así —y no exigiendo el sufijo «(Real)»— porque las exportaciones
    simples de Moodle traen las notas sin sufijo, y descartarlas dejaría el
    plan vacío sin explicar por qué.
    """
    h = header.strip()
    return bool(h) and h not in COLUMNAS_IDENTIDAD and h not in COLUMNAS_NO_NOTA


def _assert_identity_columns(headers: list[str]) -> None:
    faltan = [c for c in (COL_CEDULA, COL_INSTITUCION) if c not in headers]
    if not faltan:
        return
    raise MoodleError(
        "A la exportación de Moodle le faltan columnas necesarias: " + ", ".join(faltan),
        remedio=(
            "Esas columnas salen de la configuración del sitio de Moodle "
            "(Administración → Calificaciones → Configuración general → «Campos de "
            "perfil de usuario a incluir en la exportación»). Pedile a la persona "
            "que administra Moodle que agregue «Número de ID» e «Institución». "
            f"Columnas que sí llegaron: {', '.join(headers)}"
        ),
    )


def write_moodle_xlsx(export: GradeExport, path: Path) -> Path:
    """
    Escribe el .xlsx con la forma exacta que espera ``notasparciales_upload.py``.

    Ese script busca los encabezados «Nombre», «Apellido(s)», «Número de ID» e
    «Institución» tal cual, así que se preservan sin tocar.
    """
    try:
        import openpyxl
    except ImportError:  # pragma: no cover - dependencia declarada
        raise MoodleError(
            "Falta la librería openpyxl.",
            remedio="Ejecutá:  pip install -r requirements.txt",
        ) from None

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Calificaciones"

    columnas = [c for c in COLUMNAS_IDENTIDAD if c in export.headers]
    columnas += [h for h in export.headers if h not in columnas]

    ws.append(columnas)
    for row in export.rows:
        ws.append([row.get(c, "") for c in columnas])

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    wb.close()
    return path
