"""
El asistente de configuración (specs/003, §4).

Cinco pasos, una vez por curso. Al final, ``courses.yml`` queda escrito sin que
nadie haya tenido que abrirlo: **ese es el objetivo del programa entero**.

El orden no es casual. Cada paso solo puede hacerse si el anterior salió bien, y
cada uno se comprueba contra el servidor antes de dejar avanzar. La comprobación
importa más de lo que parece: el sistema de la UNED **no da error** ante códigos
que no corresponden —devuelve listas vacías— así que sin verificar, un profesor
podría terminar el asistente convencido de que quedó bien y enterarse semanas
después de que nunca subió nada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from .. import credstore
from ..config import Course, Credentials, MoodleGroupRef, NotasParcialesCtx, Policy
from ..discovery import ContextCheck
from ..moodle_export import MoodleCourse, MoodleGroup
from .tareas import Fallo, Tarea


@dataclass
class Borrador:
    """
    Lo que el asistente va sabiendo, paso a paso.

    Se llena de arriba abajo. Al final se convierte en un ``Course`` y se
    escribe. Las contraseñas **no** viven acá: van directo al almacén del
    sistema en cuanto se validan.
    """

    creds: Credentials | None = None
    curso_moodle: MoodleCourse | None = None
    grupos_moodle: list[MoodleGroup] = field(default_factory=list)
    seleccionados: list[int] = field(default_factory=list)
    np: NotasParcialesCtx | None = None
    tutor: str = ""
    check: ContextCheck | None = None
    course_id_local: str = ""

    def a_course(self) -> Course:
        """El ``Course`` que se va a escribir en courses.yml."""
        if self.np is None or self.curso_moodle is None:
            raise ValueError("el borrador todavía no está completo")
        elegidos = {g.id: g for g in self.grupos_moodle}
        return Course(
            id=self.course_id_local,
            moodle_course_id=self.curso_moodle.id,
            np=self.np,
            groups=tuple(
                MoodleGroupRef(moodle_group_id=gid, name=elegidos[gid].name)
                for gid in self.seleccionados
                if gid in elegidos
            ),
            destinations=tuple(self.check.destinos) if self.check else (),
            policy=Policy(),
        )


class PasoBase(QWizardPage):
    """
    Un paso que puede lanzar trabajo lento sin congelar la ventana.

    Mientras la tarea corre, el paso queda «incompleto» a propósito: así el
    botón de continuar se apaga solo y nadie avanza sobre un resultado que
    todavía no llegó.
    """

    def __init__(self, borrador: Borrador) -> None:
        super().__init__()
        self.borrador = borrador
        self._tarea: Tarea | None = None
        self._ocupado = False
        self.estado = QLabel("")
        self.estado.setWordWrap(True)
        self.estado.setTextFormat(Qt.TextFormat.PlainText)

    # --- trabajo en segundo plano ----------------------------------------
    def correr(self, funcion, *args, al_terminar=None, mientras: str = "Un momento…"):
        self._ocupado = True
        self.estado.setText(mientras)
        self.completeChanged.emit()

        tarea = Tarea(funcion, *args)
        tarea.terminada.connect(lambda r: self._listo(r, al_terminar))
        tarea.fallada.connect(self._error)
        tarea.finished.connect(tarea.deleteLater)
        self._tarea = tarea
        tarea.start()

    def _listo(self, resultado, al_terminar) -> None:
        self._ocupado = False
        if al_terminar is not None:
            al_terminar(resultado)
        self.completeChanged.emit()

    def _error(self, fallo: Fallo) -> None:
        self._ocupado = False
        self.estado.setText("✗ " + fallo.texto)
        self.completeChanged.emit()

    @property
    def ocupado(self) -> bool:
        return self._ocupado


# ---------------------------------------------------------------------------
# Paso 1 — credenciales
# ---------------------------------------------------------------------------
class PasoCredenciales(PasoBase):
    """
    Usuario y contraseña de los dos sistemas, comprobados antes de seguir.

    Se comprueban acá y no al final por una razón: si la contraseña está mal,
    todo lo que viene después falla de maneras que no se parecen en nada a
    «tu contraseña está mal».
    """

    def __init__(self, borrador: Borrador) -> None:
        super().__init__(borrador)
        self.setTitle("Tus dos cuentas")
        self.setSubTitle(
            "La de Moodle, con la que calificás, y la del SSO de la UNED, "
            "con la que entrás a Notas Parciales."
        )

        self.moodle_usuario = QLineEdit()
        self.moodle_clave = QLineEdit(echoMode=QLineEdit.EchoMode.Password)
        self.np_usuario = QLineEdit()
        self.np_usuario.setPlaceholderText("sin @uned.ac.cr")
        self.np_clave = QLineEdit(echoMode=QLineEdit.EchoMode.Password)
        self.tutor = QLineEdit()
        self.tutor.setPlaceholderText("tu cédula, sin guiones")

        self.recordar = QCheckBox(
            "Recordar en este equipo (Administrador de credenciales de Windows)"
        )
        self.recordar.setChecked(credstore.disponible())
        self.recordar.setEnabled(credstore.disponible())

        form = QFormLayout()
        form.addRow(QLabel("<b>Moodle</b>"))
        form.addRow("Usuario:", self.moodle_usuario)
        form.addRow("Contraseña:", self.moodle_clave)
        form.addRow(QLabel(""))
        form.addRow(QLabel("<b>Notas Parciales</b>"))
        form.addRow("Usuario:", self.np_usuario)
        form.addRow("Contraseña:", self.np_clave)
        form.addRow("Tu cédula:", self.tutor)

        caja = QVBoxLayout(self)
        caja.addLayout(form)
        caja.addWidget(self.recordar)
        caja.addWidget(self.estado)

        for campo in (
            self.moodle_usuario, self.moodle_clave,
            self.np_usuario, self.np_clave, self.tutor,
        ):
            campo.textChanged.connect(self._olvidar_comprobacion)

        self._verificado = False
        self._precargar()

    def _precargar(self) -> None:
        """Si ya hay credenciales guardadas, no hacérselas escribir de nuevo."""
        moodle = credstore.leer(credstore.SERVICIO_MOODLE)
        if moodle:
            self.moodle_usuario.setText(moodle.username)
            self.moodle_clave.setText(moodle.password)
        np = credstore.leer(credstore.SERVICIO_NP)
        if np:
            self.np_usuario.setText(np.username)
            self.np_clave.setText(np.password)

    def _olvidar_comprobacion(self) -> None:
        # Cambió una credencial: lo comprobado antes ya no vale para lo de ahora.
        if self._verificado:
            self._verificado = False
            self.estado.setText("")
            self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._verificado and not self.ocupado

    def validatePage(self) -> bool:
        return self._verificado


# ---------------------------------------------------------------------------
# Paso 2 — curso y grupos
# ---------------------------------------------------------------------------
class PasoGrupos(PasoBase):
    """
    Cuáles de los grupos del curso son suyos.

    Se preseleccionan los que llevan su nombre en el título (specs/001, D-01),
    pero **no se ocultan los demás**: los nombres varían en tildes, prefijos y
    apellidos, y un filtro acertado el 95% de las veces esconde justo el que
    hacía falta, sin forma de recuperarlo (A-04).
    """

    def __init__(self, borrador: Borrador) -> None:
        super().__init__(borrador)
        self.setTitle("Tus grupos")
        self.setSubTitle(
            "Un curso se reparte entre varios profesores. Marcá los que das vos: "
            "los demás son estudiantes de otra persona."
        )

        self.lista = QListWidget()
        self.lista.itemChanged.connect(lambda _: self.completeChanged.emit())

        caja = QVBoxLayout(self)
        caja.addWidget(self.lista)
        caja.addWidget(self.estado)

    def poblar(self, grupos: list[MoodleGroup], nombre_profesor: str) -> None:
        self.lista.clear()
        for g in grupos:
            item = QListWidgetItem(f"{g.name}   ({g.id})")
            item.setData(Qt.ItemDataRole.UserRole, g.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            propio = _parece_suyo(g.name, nombre_profesor)
            item.setCheckState(
                Qt.CheckState.Checked if propio else Qt.CheckState.Unchecked
            )
            self.lista.addItem(item)

    def marcados(self) -> list[int]:
        return [
            self.lista.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.lista.count())
            if self.lista.item(i).checkState() == Qt.CheckState.Checked
        ]

    def isComplete(self) -> bool:
        return bool(self.marcados()) and not self.ocupado


def _parece_suyo(nombre_grupo: str, nombre_profesor: str) -> bool:
    """
    ¿El título del grupo menciona al profesor?

    Compara sin tildes ni mayúsculas, y le alcanza con **una** palabra del
    nombre para marcarlo. Es una ayuda de presentación, no una regla: marcar de
    más solo cuesta un clic, y marcar de menos esconde un grupo entero.
    """
    import unicodedata

    def limpiar(s: str) -> set[str]:
        sin_tildes = "".join(
            c for c in unicodedata.normalize("NFD", s.lower())
            if unicodedata.category(c) != "Mn"
        )
        return {p for p in sin_tildes.replace(".", " ").split() if len(p) > 2}

    return bool(limpiar(nombre_grupo) & limpiar(nombre_profesor))


# ---------------------------------------------------------------------------
# Paso 3 — los códigos de la asignatura
# ---------------------------------------------------------------------------
class PasoCodigos(PasoBase):
    """
    Los ocho códigos de la pantalla de Captura de Notas, verificados al instante.

    Se escriben en vez de elegirse de un menú porque reproducir esos menús
    exigiría interpretar el HTML de una página que puede cambiar cuando quiera
    (A-05). Lo que hace aceptable escribirlos es la verificación: si con esos
    códigos aparecen estudiantes, son los correctos, y el número que sale en
    pantalla se puede comparar con lo que uno sabe de su propio curso (A-05.1).
    """

    CAMPOS = [
        ("ano", "Año:", "2026"),
        ("pac", "Período (PAC):", "3"),
        ("asignatura", "Asignatura:", "00883"),
        ("escuela", "Escuela:", "03"),
        ("catedra", "Cátedra:", "253"),
        ("encargado", "Encargado de cátedra:", "ARODRIGUEZP"),
        ("modelo", "Modelo de evaluación:", "4"),
        ("tipo", "Tipo:", "O"),
    ]

    def __init__(self, borrador: Borrador) -> None:
        super().__init__(borrador)
        self.setTitle("Los datos de tu curso en Notas Parciales")
        self.setSubTitle(
            "Copialos de los menús de la página de Captura de Notas. "
            "Se comprueban contra el sistema antes de seguir."
        )

        form = QFormLayout()
        self.campos: dict[str, QLineEdit] = {}
        for clave, etiqueta, ejemplo in self.CAMPOS:
            campo = QLineEdit()
            campo.setPlaceholderText(f"por ejemplo: {ejemplo}")
            campo.textChanged.connect(self._olvidar_comprobacion)
            self.campos[clave] = campo
            form.addRow(etiqueta, campo)

        caja = QVBoxLayout(self)
        caja.addLayout(form)
        caja.addWidget(self.estado)
        self._verificado = False

    def valores(self) -> dict[str, str]:
        return {k: c.text().strip() for k, c in self.campos.items()}

    def contexto(self) -> NotasParcialesCtx:
        v = self.valores()
        return NotasParcialesCtx(
            ano=v["ano"], pac=v["pac"], asignatura=v["asignatura"],
            escuela=v["escuela"], catedra=int(v["catedra"] or 0),
            encargado=v["encargado"], modelo=int(v["modelo"] or 0),
            tipo=v["tipo"] or "O",
        )

    def _olvidar_comprobacion(self) -> None:
        if self._verificado:
            self._verificado = False
            self.estado.setText("")
            self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._verificado and not self.ocupado


# ---------------------------------------------------------------------------
# Paso 4 — el reparto
# ---------------------------------------------------------------------------
class PasoReparto(PasoBase):
    """
    A qué grupos oficiales van a parar los estudiantes, para que lo confirme.

    Es la pantalla que justifica el programa entero: enseña de una vez el
    reparto que antes había que deducir a mano, incluido el caso de un mismo
    centro universitario repartido en varios grupos (A-06). Y enseña, aparte,
    a quien no apareció en ninguno.
    """

    def __init__(self, borrador: Borrador) -> None:
        super().__init__(borrador)
        self.setTitle("Adónde van tus notas")
        self.setSubTitle("Revisá que los números cuadren con lo que sabés de tu curso.")

        self.tabla = QTableWidget(0, 2)
        self.tabla.setHorizontalHeaderLabels(["Grupo de Notas Parciales", "Estudiantes"])
        self.tabla.horizontalHeader().setStretchLastSection(True)
        self.tabla.verticalHeader().setVisible(False)

        self.huerfanos = QLabel("")
        self.huerfanos.setWordWrap(True)
        self.huerfanos.setTextFormat(Qt.TextFormat.PlainText)

        caja = QVBoxLayout(self)
        caja.addWidget(self.tabla)
        caja.addWidget(self.huerfanos)
        caja.addWidget(self.estado)

    def mostrar(self, check: ContextCheck) -> None:
        self.tabla.setRowCount(0)
        for cu in sorted(check.por_cu):
            for d in check.por_cu[cu]:
                fila = self.tabla.rowCount()
                self.tabla.insertRow(fila)
                self.tabla.setItem(fila, 0, QTableWidgetItem(d.label))
                self.tabla.setItem(
                    fila, 1, QTableWidgetItem(str(check.estudiantes_en.get(d.key, 0)))
                )

        self.huerfanos.setText(_texto_huerfanos(check))

    def isComplete(self) -> bool:
        return not self.ocupado


def _texto_huerfanos(check: ContextCheck) -> str:
    """
    Qué decir de quienes no aparecieron en ningún grupo oficial.

    Es poco frecuente (specs/001, D-07), y por eso mismo cada uno va con nombre.
    Lo que **no** puede decir es que algo salió mal: sus compañeros suben igual.
    """
    if not check.sin_destino:
        return "✓ Todos tus estudiantes tienen grupo en Notas Parciales."

    nombres = "\n".join(
        f"    · {nombre or '(sin nombre)'}  —  {cedula}  —  CU {cu}"
        for cedula, nombre, cu in check.sin_destino
    )
    return (
        f"⚠ {len(check.sin_destino)} estudiante(s) están en Moodle pero no en "
        "Notas Parciales. Sus notas NO se van a subir:\n"
        f"{nombres}\n\n"
        "Suele significar que no quedaron matriculados en esta asignatura. "
        "Consultalo con registro. El resto sube con normalidad."
    )


# ---------------------------------------------------------------------------
# Paso 5 — las columnas
# ---------------------------------------------------------------------------
class PasoColumnas(PasoBase):
    """
    Qué columna de Moodle va a qué instrumento (A-07).

    El programa lo propone solo, pero la última palabra es del profesor: acá se
    ve el emparejamiento entero, y las columnas que no reconoció aparecen
    marcadas en vez de desaparecer sin más.
    """

    def __init__(self, borrador: Borrador) -> None:
        super().__init__(borrador)
        self.setTitle("Tus columnas de Moodle")
        self.setSubTitle("Así quedó el emparejamiento con los instrumentos del sistema.")

        self.tabla = QTableWidget(0, 2)
        self.tabla.setHorizontalHeaderLabels(["Columna en Moodle", "Instrumento"])
        self.tabla.horizontalHeader().setStretchLastSection(True)
        self.tabla.verticalHeader().setVisible(False)

        self.aviso = QLabel("")
        self.aviso.setWordWrap(True)

        caja = QVBoxLayout(self)
        caja.addWidget(self.tabla)
        caja.addWidget(self.aviso)
        caja.addWidget(self.estado)

    def mostrar(self, check: ContextCheck) -> None:
        self.tabla.setRowCount(0)
        for i in check.instrumentos:
            fila = self.tabla.rowCount()
            self.tabla.insertRow(fila)
            self.tabla.setItem(fila, 0, QTableWidgetItem(i.columna))
            self.tabla.setItem(fila, 1, QTableWidgetItem(i.descripcion))

        sin = check.columnas_sin_emparejar
        self.aviso.setText(
            ""
            if not sin
            else (
                f"⚠ {len(sin)} columna(s) no se reconocieron y no se van a subir. "
                "Si alguna debía subirse, avisale a quien mantiene el programa "
                "para agregarla a mano."
            )
        )

    def isComplete(self) -> bool:
        return not self.ocupado


class Asistente(QWizard):
    """Los cinco pasos, en orden."""

    def __init__(self, work_dir: Path) -> None:
        super().__init__()
        self.setWindowTitle("mnsync — configurar un curso")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.borrador = Borrador()
        self.work_dir = work_dir

    def credenciales_del_paso(self, paso: PasoCredenciales) -> Credentials:
        """Arma unas credenciales con lo que hay escrito en el primer paso."""
        from ..config import MOODLE_URL_DEFAULT, NP_BASE_URL_DEFAULT

        return Credentials(
            moodle_url=MOODLE_URL_DEFAULT,
            moodle_username=paso.moodle_usuario.text().strip(),
            moodle_password=paso.moodle_clave.text(),
            np_user=paso.np_usuario.text().strip(),
            np_password=paso.np_clave.text(),
            np_tutor=paso.tutor.text().strip(),
            np_base_url=NP_BASE_URL_DEFAULT,
        )


# ---------------------------------------------------------------------------
# El trabajo que hace cada paso
# ---------------------------------------------------------------------------
def probar_ingresos(creds: Credentials) -> list[MoodleCourse]:
    """
    Comprueba que Moodle acepta las credenciales, y de paso trae los cursos.

    Se hace de una sola vez porque son la misma sesión: pedir la lista de
    cursos **es** la prueba de que el ingreso funcionó.
    """
    from ..moodle_export import MoodleSession

    sesion = MoodleSession(creds.moodle_url)
    sesion.login(creds.moodle_username, creds.moodle_password)
    return sesion.list_courses()


def traer_grupos(creds: Credentials, course_id: int) -> list[MoodleGroup]:
    """Los grupos del curso, para que el profesor marque los suyos."""
    from ..moodle_export import MoodleSession

    sesion = MoodleSession(creds.moodle_url)
    sesion.login(creds.moodle_username, creds.moodle_password)
    return sesion.list_groups(course_id)


def verificar(course: Course, creds: Credentials, work_dir: Path) -> ContextCheck:
    """
    El paso caro: baja de Moodle, sondea Notas Parciales y resuelve el reparto.

    Es una sola llamada porque produce de una vez todo lo que necesitan los dos
    pasos siguientes —el reparto y el emparejamiento de columnas—, y repetirla
    costaría otra tanda de consultas al servidor para saber lo mismo.

    No escribe nada: son todas operaciones de lectura.
    """
    from ..discovery import verificar_contexto

    return verificar_contexto(course, creds, work_dir)
