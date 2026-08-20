"""
HTML sintético con la forma que sirve Moodle.

Reproduce lo que importa de la página «Calificaciones → Exportar → Archivo de
texto»: el selector de grupo, los campos ocultos y una casilla por cada
instrumento. Deliberadamente incluye ruido (campos que no usamos, casillas
desmarcadas) para que el cosechador de formularios se ejercite de verdad.
"""

from __future__ import annotations

LOGIN_PAGE = """
<html><body>
<form action="/login/index.php" method="post" id="login">
  <input type="hidden" name="logintoken" value="tok3nDePrueba">
  <input type="text" name="username">
  <input type="password" name="password">
  <input type="submit" name="submitbutton" value="Acceder">
</form>
</body></html>
"""

LOGIN_OK = "<html><body><div class='usermenu'>Sesion iniciada</div></body></html>"

LOGIN_FAIL = """
<html><body>
<div class="loginerrors"><a id="loginerrormessage">Datos incorrectos</a></div>
<form action="/login/index.php" method="post">
  <input type="hidden" name="logintoken" value="tok3nDePrueba">
</form>
</body></html>
"""


def export_page(
    *,
    course_id: int = 8067,
    groups: list[tuple[int, str]] | None = None,
    selected_group: int | None = None,
    items: list[tuple[int, str]] | None = None,
    with_group_select: bool = True,
) -> str:
    """Arma la página de exportación con el grupo indicado ya seleccionado."""
    groups = groups if groups is not None else [(38525, "Grupo 1"), (38526, "Grupo 2")]
    items = items if items is not None else [(101, "Tarea 1"), (102, "Tarea 2"), (103, "Proyecto")]

    opciones = ['<option value="0">Todos los participantes</option>']
    for gid, nombre in groups:
        sel = " selected" if selected_group == gid else ""
        opciones.append(f'<option value="{gid}"{sel}>{nombre}</option>')

    bloque_grupo = ""
    if with_group_select:
        bloque_grupo = (
            '<div class="groupselector"><select name="group" id="single_select">'
            + "".join(opciones)
            + "</select></div>"
        )

    casillas = []
    for i, (item_id, nombre) in enumerate(items):
        # La primera va desmarcada a proposito: el exportador debe marcarlas todas.
        checked = "" if i == 0 else " checked"
        casillas.append(
            f'<input type="checkbox" name="itemids[{item_id}]" value="1"{checked}> {nombre}'
        )

    return f"""
<html><body>
{bloque_grupo}
<form action="/grade/export/txt/export.php" method="post" id="mform1">
  <input type="hidden" name="sesskey" value="s3sskeyPrueba">
  <input type="hidden" name="_qf__grade_export_form" value="1">
  <input type="hidden" name="id" value="{course_id}">
  <input type="hidden" name="mform_isexpanded_id_gradeitems" value="1">
  {"".join(casillas)}
  <input type="checkbox" name="export_feedback" value="1"> Incluir comentarios
  <input type="checkbox" name="export_onlyactive" value="1" checked> Solo activos
  <input type="checkbox" name="display[real]" value="1" checked> Real
  <input type="checkbox" name="display[percentage]" value="1" checked> Porcentaje
  <select name="decimals"><option value="2" selected>2</option></select>
  <select name="separator"><option value="comma" selected>coma</option></select>
  <input type="submit" name="submitbutton" value="Descargar">
</form>
</body></html>
"""


def export_csv(students, notas_por_cedula=None, *, columnas=("Tarea 1 (Real)", "Proyecto (Real)")):
    """
    CSV con la forma exacta que exporta Moodle.

    ``notas_por_cedula``: {cedula: {columna: valor}}. Lo que no se indique
    queda como guion, que Moodle usa para "sin calificar".
    """
    notas_por_cedula = notas_por_cedula or {}
    encabezados = ["Nombre", "Apellido(s)", "Número de ID", "Institución", *columnas]
    lineas = [",".join(encabezados)]
    for st in students:
        propias = notas_por_cedula.get(st.cedula, {})
        fila = [st.nombre, st.apellidos, st.cedula, st.institucion]
        fila += [str(propias.get(c, "-")) for c in columnas]
        lineas.append(",".join(fila))
    return "\n".join(lineas) + "\n"
