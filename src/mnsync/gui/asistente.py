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
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
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
    cursos_moodle: list[MoodleCourse] = field(default_factory=list)
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
        self.tutor.setPlaceholderText("sin guiones")
        self.tutor.setToolTip(
            "Notas Parciales identifica al tutor por su cédula. Va en cada "
            "consulta al sistema, junto con los códigos de la asignatura."
        )
        self.moodle_usuario.textChanged.connect(self._sugerir_cedula)

        self.recordar = QCheckBox(
            "Recordar en este equipo (Administrador de credenciales de Windows)"
        )
        self.recordar.setChecked(credstore.disponible())
        self.recordar.setEnabled(credstore.disponible())

        # La cédula NO es una tercera contraseña, y ponerla debajo de una lo
        # parecía. Es uno de los nueve códigos con los que el sistema arma cada
        # consulta (specs/003, A-05): así identifica al tutor. Se pide acá y no
        # con los otros ocho porque es un dato personal, y por eso se guarda en
        # el almacén de Windows y nunca en courses.yml (specs/002, R-15).
        explicacion = QLabel(
            "Es el número con el que Notas Parciales sabe cuáles grupos son "
            "tuyos. No es una contraseña: viaja en cada consulta, junto con "
            "los códigos de tu asignatura."
        )
        explicacion.setWordWrap(True)

        form = QFormLayout()
        form.addRow(QLabel("<b>Moodle</b>"))
        form.addRow("Usuario:", self.moodle_usuario)
        form.addRow("Contraseña:", self.moodle_clave)
        form.addRow(QLabel(""))
        form.addRow(QLabel("<b>Notas Parciales</b>"))
        form.addRow("Usuario:", self.np_usuario)
        form.addRow("Contraseña:", self.np_clave)
        form.addRow(QLabel(""))
        form.addRow(QLabel("<b>Tu cédula de tutora o tutor</b>"))
        form.addRow(explicacion)
        form.addRow("Cédula:", self.tutor)

        self.boton = QPushButton("Comprobar que funcionan")
        self.boton.clicked.connect(self._comprobar)

        caja = QVBoxLayout(self)
        caja.addLayout(form)
        caja.addWidget(self.recordar)
        caja.addWidget(self.boton)
        caja.addWidget(self.estado)

        for campo in (
            self.moodle_usuario, self.moodle_clave,
            self.np_usuario, self.np_clave, self.tutor,
        ):
            campo.textChanged.connect(self._olvidar_comprobacion)

        self._verificado = False
        self._precargar()

    def _comprobar(self) -> None:
        """
        Entra a Moodle de verdad y, de paso, trae la lista de cursos.

        Pedir los cursos **es** la prueba de que el ingreso funcionó: no hace
        falta una llamada aparte solo para comprobar la contraseña.
        """
        faltan = [
            etiqueta
            for etiqueta, campo in (
                ("usuario de Moodle", self.moodle_usuario),
                ("contraseña de Moodle", self.moodle_clave),
                ("usuario de Notas Parciales", self.np_usuario),
                ("contraseña de Notas Parciales", self.np_clave),
                ("tu cédula de tutora o tutor", self.tutor),
            )
            if not campo.text().strip()
        ]
        if faltan:
            self.estado.setText("Falta completar: " + ", ".join(faltan) + ".")
            return

        creds = self.wizard().credenciales_del_paso(self)
        self.borrador.creds = creds
        self.borrador.tutor = creds.np_tutor
        self.boton.setEnabled(False)
        self.correr(
            probar_ingresos,
            creds,
            al_terminar=self._con_cursos,
            mientras="Entrando a Moodle…",
        )

    def _con_cursos(self, cursos: list[MoodleCourse]) -> None:
        self.boton.setEnabled(True)
        self._verificado = True
        self.borrador.cursos_moodle = list(cursos)

        if self.recordar.isChecked() and credstore.disponible():
            credstore.guardar(
                credstore.SERVICIO_MOODLE,
                self.moodle_usuario.text().strip(),
                self.moodle_clave.text(),
            )
            credstore.guardar(
                credstore.SERVICIO_NP,
                self.np_usuario.text().strip(),
                self.np_clave.text(),
            )
            guardadas = "  Quedaron guardadas en este equipo."
        else:
            guardadas = ""

        cuantos = len(cursos)
        detalle = (
            f"Moodle aceptó tus datos y encontró {cuantos} curso(s)."
            if cuantos
            else "Moodle aceptó tus datos. La lista de cursos no se pudo leer; "
            "en el paso siguiente vas a poder escribir el número de tu curso."
        )
        self.estado.setText("✓ " + detalle + guardadas)

    def _error(self, fallo: Fallo) -> None:
        self.boton.setEnabled(True)
        super()._error(fallo)

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

        guardada = _cedula_guardada()
        if guardada:
            self.tutor.setText(guardada)
        elif moodle:
            self._sugerir_cedula(moodle.username)

    def _sugerir_cedula(self, texto: str) -> None:
        """
        Si el usuario de Moodle es un número de cédula, proponerlo.

        En la UNED se entra a Moodle con la cédula, así que casi siempre es el
        mismo dato escrito dos veces. Se **propone**, no se impone: el campo
        queda editable y una propuesta equivocada la atrapa la comprobación del
        paso 3, que es contra el servidor.

        Solo se rellena mientras esté vacío. Pisar algo que la persona escribió
        a mano sería peor que no ayudar.
        """
        limpio = texto.strip()
        if self.tutor.text().strip():
            return
        if limpio.isdigit() and 9 <= len(limpio) <= 12:
            self.tutor.setText(limpio)

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

        # Editable a propósito. Leer la lista de cursos depende de cómo arme
        # Moodle su página de inicio, que cambia con la versión y con el tema
        # visual; si un día no se puede leer, el asistente no puede quedarse sin
        # salida. El número está a la vista en la barra del navegador
        # (…/course/view.php?id=8067), así que escribirlo siempre es posible.
        self.cursos = QComboBox()
        self.cursos.setEditable(True)
        self.cursos.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.cursos.lineEdit().setPlaceholderText(
            "elegí uno, o escribí el número del curso"
        )
        self.cursos.currentIndexChanged.connect(self._cambio_de_curso)
        self.cursos.lineEdit().editingFinished.connect(self._cambio_de_curso)

        self.lista = QListWidget()
        self.lista.itemChanged.connect(lambda _: self.completeChanged.emit())

        ayuda = QLabel(
            "Si tu curso no está en la lista, abrilo en Moodle y copiá el número "
            "que sale en la dirección, después de «id=»."
        )
        ayuda.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Curso:", self.cursos)

        caja = QVBoxLayout(self)
        caja.addLayout(form)
        caja.addWidget(ayuda)
        caja.addWidget(QLabel("Grupos del curso:"))
        caja.addWidget(self.lista)
        caja.addWidget(self.estado)

    def initializePage(self) -> None:
        """Al llegar, poblar el desplegable con los cursos que trajo el paso 1."""
        self.cursos.blockSignals(True)
        self.cursos.clear()
        for c in self.borrador.cursos_moodle:
            self.cursos.addItem(f"{c.name}   ({c.id})", c)
        self.cursos.blockSignals(False)

        if self.cursos.count():
            self._cambio_de_curso()
        else:
            self.estado.setText(
                "No se pudo leer tu lista de cursos, pero eso no detiene nada: "
                "escribí acá arriba el número de tu curso y seguimos."
            )
            self.cursos.setFocus()

    def curso_elegido(self) -> MoodleCourse | None:
        """
        El curso que está seleccionado, sea de la lista o escrito a mano.

        Un número escrito a mano vale tanto como uno elegido: lo que hace falta
        es el identificador, y el nombre solo sirve para reconocerlo en pantalla
        y para armar el apodo del curso.
        """
        curso = self.cursos.currentData()
        if isinstance(curso, MoodleCourse):
            return curso

        texto = self.cursos.currentText().strip()
        if texto.isdigit():
            return MoodleCourse(id=int(texto), name=f"curso {texto}")
        return None

    def _cambio_de_curso(self) -> None:
        curso = self.curso_elegido()
        if curso is None or self.borrador.creds is None:
            return
        if self.borrador.curso_moodle == curso and self.borrador.grupos_moodle:
            return  # ya se bajaron sus grupos; no repetir la consulta
        self.borrador.curso_moodle = curso
        self.lista.clear()
        self.correr(
            traer_grupos,
            self.borrador.creds,
            curso.id,
            al_terminar=self._con_grupos,
            mientras="Buscando los grupos del curso…",
        )

    def _con_grupos(self, grupos: list[MoodleGroup]) -> None:
        self.borrador.grupos_moodle = list(grupos)
        if not grupos:
            self.estado.setText(
                "Este curso no tiene grupos en Moodle. Sin grupos no se puede "
                "separar a tus estudiantes de los de otros profesores."
            )
            return
        self.poblar(grupos, self.borrador.creds.moodle_username if self.borrador.creds else "")
        marcados = len(self.marcados())
        self.estado.setText(
            f"Se marcaron {marcados} de {len(grupos)} por tu nombre. Revisá que "
            "estén los tuyos, y solo los tuyos."
        )

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

    def validatePage(self) -> bool:
        self.borrador.seleccionados = self.marcados()
        return bool(self.borrador.seleccionados)

    def isComplete(self) -> bool:
        return bool(self.marcados()) and not self.ocupado


def _apodo(borrador: Borrador) -> str:
    """
    Un nombre corto para el curso, como «cyber-2026-4».

    Es lo que el profesor va a escribir después en la línea de comandos y lo
    que nombra los archivos de salida, así que se arma de lo que él reconoce
    —el nombre del curso en Moodle— y no del código interno de la asignatura.
    """
    import re
    import unicodedata

    crudo = borrador.curso_moodle.name if borrador.curso_moodle else "curso"
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", crudo.lower())
        if unicodedata.category(c) != "Mn"
    )
    # Se saltan «a», «de», «la», que no distinguen nada y alargan el apodo, y
    # los códigos: muchos cursos de Moodle se llaman «03622 Introducción a la
    # Ciberseguridad», y un apodo que empieza en «03622» no le dice nada a nadie
    # —que es exactamente lo que este nombre existe para evitar—.
    palabras = [
        p
        for p in re.split(r"[^a-z0-9]+", sin_tildes)
        if len(p) > 2 and not p.isdigit()
    ][:2]
    base = "-".join(palabras) or "curso"

    np = borrador.np
    return f"{base}-{np.ano}-{np.pac}" if np else base


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

        self.boton = QPushButton("Comprobar contra Notas Parciales")
        self.boton.clicked.connect(self._comprobar)

        caja = QVBoxLayout(self)
        caja.addLayout(form)
        caja.addWidget(self.boton)
        caja.addWidget(self.estado)
        self._verificado = False

    def _comprobar(self) -> None:
        """
        El paso caro, y el que da sentido a escribir los códigos a mano.

        Baja de Moodle, sondea Notas Parciales y cuenta. Si aparecen
        estudiantes, los códigos son los correctos: el sistema responde igual
        de bien a una asignatura que no existe, así que no hay otra prueba.
        """
        vacios = [e for k, e, _ in self.CAMPOS if not self.campos[k].text().strip()]
        if vacios:
            self.estado.setText("Falta completar: " + ", ".join(vacios))
            return
        if self.borrador.creds is None or self.borrador.curso_moodle is None:
            self.estado.setText("Volvé a los pasos anteriores: falta información.")
            return

        try:
            self.borrador.np = self.contexto()
        except ValueError:
            self.estado.setText(
                "«Cátedra» y «Modelo» tienen que ser números. Copialos tal cual "
                "aparecen en los menús de Captura de Notas."
            )
            return

        self.borrador.course_id_local = _apodo(self.borrador)

        self.boton.setEnabled(False)
        self.correr(
            verificar,
            self.borrador.a_course(),
            self.borrador.creds,
            self.wizard().work_dir,
            al_terminar=self._con_resultado,
            mientras=(
                "Bajando tus notas de Moodle y preguntándole a Notas Parciales "
                "dónde está cada estudiante. Esto tarda un poco la primera vez; "
                "no se escribe nada."
            ),
        )

    def _con_resultado(self, check: ContextCheck) -> None:
        self.boton.setEnabled(True)
        self.borrador.check = check

        if not check.ok:
            self.estado.setText("✗ " + check.mensaje + "\n\n" + check.remedio)
            return

        self._verificado = True
        self.estado.setText("✓ " + check.resumen)

    def _error(self, fallo: Fallo) -> None:
        self.boton.setEnabled(True)
        super()._error(fallo)

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

    def initializePage(self) -> None:
        if self.borrador.check is not None:
            self.mostrar(self.borrador.check)

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

    def initializePage(self) -> None:
        if self.borrador.check is not None:
            self.mostrar(self.borrador.check)

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
        self.config_path = Path("courses.yml").resolve()
        self.escrito: Path | None = None

    def accept(self) -> None:
        """
        Al terminar, escribe ``courses.yml``.

        Es el objetivo del programa entero: que ese archivo exista sin que nadie
        haya tenido que abrirlo. Lo que se guarda son códigos y destinos —nunca
        contraseñas, ni la cédula del tutor, ni una sola de estudiante— porque
        este archivo se comparte y se sube al repositorio (specs/002, R-15).

        Si un curso con el mismo apodo ya estaba configurado, se reemplaza: es
        lo que uno espera al volver a correr el asistente sobre el mismo curso.
        """
        from ..config import load_config, write_config
        from ..errors import MnsyncError

        try:
            nuevo = self.borrador.a_course()
        except ValueError:
            super().accept()
            return

        try:
            previos = [c for c in load_config(self.config_path).courses if c.id != nuevo.id]
        except MnsyncError:
            # No había configuración, o la que había no se puede leer. Empezamos
            # de cero en vez de negarnos a guardar lo que el profesor acaba de
            # verificar contra el servidor.
            previos = []

        self.escrito = write_config([*previos, nuevo], self.config_path)
        super().accept()

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


def _cedula_guardada() -> str:
    """
    La cédula del tutor que ya esté configurada, si la hay.

    Sale de donde la dejó una configuración anterior —el entorno, el ``.env`` o
    el almacén de Windows— para no hacérsela escribir de nuevo a quien vuelve a
    correr el asistente. Si no hay nada, se devuelve vacío y el paso la propone
    a partir del usuario de Moodle.
    """
    from ..config import load_credentials

    try:
        return load_credentials(require=False).np_tutor
    except Exception:  # noqa: BLE001 - una precarga nunca puede romper la ventana
        return ""


def construir_asistente(work_dir: Path) -> Asistente:
    """
    El asistente con sus cinco pasos puestos, listo para mostrarse.

    Vive acá y no en el arranque del programa porque también se abre desde la
    ventana principal, y dos listas de pasos que hay que acordarse de mantener
    iguales terminan siendo dos asistentes distintos.
    """
    asistente = Asistente(work_dir)
    for paso in (PasoCredenciales, PasoGrupos, PasoCodigos, PasoReparto, PasoColumnas):
        asistente.addPage(paso(asistente.borrador))
    asistente.resize(760, 560)
    return asistente


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
