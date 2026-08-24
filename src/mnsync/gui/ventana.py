"""
La ventana de todas las semanas (specs/003, §5).

El asistente se corre una vez por curso. **Esto** es lo que un profesor abre el
viernes por la tarde, y por eso está construido alrededor de una sola idea: al
abrirse ya sabe qué va a pasar, y lo único que queda por hacer es decidir.

Tres cosas la ordenan.

**Primero se mira, después se escribe.** La ventana nunca escribe sin haber
enseñado antes, fila por fila, qué cambiaría. No es una confirmación genérica
—«¿está seguro?»— sino la lista concreta: este estudiante, este instrumento,
esto hay ahora y esto quedaría.

**Las notas ya puestas se autorizan de a una** (A-11). No hay ninguna casilla
que las autorice todas juntas, y no la hay a propósito: «este estudiante tiene
8,5 y Moodle dice 9,0» es una decisión individual, y cada una queda registrada
con su justificación en el sistema de la UNED.

**Lo que se rompe, se dice al arrancar.** Una contraseña vencida se avisa como
contraseña vencida y en la primera pantalla, no como un fallo incomprensible a
la mitad de una sincronización (A-14).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import Config, Course, Credentials
from ..errors import MnsyncError
from ..report import ACCIONES, RunReport, write_report
from ..sync import FilaPlan, Preparacion, aplicar, preparar
from ..uploader import ACCION_SOBRESCRIBIRIA, Uploader, reja_verificada
from .tareas import Fallo, Tarea

#: El orden en que se cuentan las acciones del plan.
#:
#: Están **todas** las que el script sabe producir, incluidas las que dieron
#: cero: la vista de diferencias no reduce el plan a «se sube» y «no se sube»
#: (specs/003, A-10). Ver un cero al lado de «cambiaría una nota existente» es
#: información, y de la que más tranquiliza.
ORDEN_ACCIONES = (
    "upload",
    "mark_not_presented",
    ACCION_SOBRESCRIBIRIA,
    "review",
    "skip_already_set",
    "skip_retirado",
    "skip_not_in_roster",
)

#: Las columnas de la vista de diferencias.
COLUMNAS = (
    "Estudiante",
    "Cédula",
    "Instrumento",
    "Grupo oficial",
    "Ahora",
    "Quedaría",
    "Qué pasaría",
    "Autorizo",
)

#: Cómo se llaman los filtros de la vista, y qué acciones deja pasar cada uno.
#:
#: Es un filtro de **vista**, no un recorte de lo que hay: «Todo» es el que
#: viene puesto, y los otros dos solo acercan la lupa.
FILTROS: tuple[tuple[str, tuple[str, ...] | None], ...] = (
    ("Todo", None),
    ("Solo lo que cambiaría", ("upload", "mark_not_presented", ACCION_SOBRESCRIBIRIA)),
    ("Solo lo que hay que revisar", (ACCION_SOBRESCRIBIRIA, "review")),
)


#: Los tres desenlaces posibles de comprobar una credencial.
#:
#: El tercero es el que faltaba y el que más importa. Sin él, cualquier fallo
#: —el servidor caído, un parámetro que el programa no mandó, la red— se
#: reportaba como «no aceptó tus datos», y eso manda a cambiar una contraseña
#: que estaba bien. Perder el acceso a los sistemas de la UNED por un aviso
#: equivocado es un daño real, y peor que no haber avisado nada.
OK = "ok"
RECHAZADO = "rechazado"
SIN_COMPROBAR = "sin_comprobar"


@dataclass
class Ingreso:
    """Cómo respondió un sistema a las credenciales guardadas."""

    nombre: str
    estado: str = SIN_COMPROBAR
    detalle: str = ""
    remedio: str = ""

    @property
    def ok(self) -> bool:
        return self.estado == OK

    @property
    def rechazado(self) -> bool:
        return self.estado == RECHAZADO

    @property
    def marca(self) -> str:
        if self.ok:
            return f"✓ {self.nombre}"
        if self.rechazado:
            return f"✗ {self.nombre}"
        return f"· {self.nombre}"

    @property
    def explicacion(self) -> str:
        """Qué pasó y qué hacer, para quien necesite leerlo entero."""
        if self.ok:
            return ""
        encabezado = (
            f"{self.nombre}: parece que cambió tu contraseña."
            if self.rechazado
            else f"{self.nombre}: no se pudo comprobar."
        )
        return "\n".join(x for x in (encabezado, self.detalle, self.remedio) if x)


@dataclass
class EstadoIngresos:
    """
    Cómo respondieron los dos sistemas a las credenciales guardadas.

    Se comprueban por separado porque son dos cuentas distintas y fallan por
    motivos distintos: decir «falló el ingreso» sin decir cuál de los dos manda
    al profesor a revisar la contraseña que no era.
    """

    moodle: Ingreso = field(default_factory=lambda: Ingreso("Moodle"))
    np: Ingreso = field(default_factory=lambda: Ingreso("Notas Parciales"))
    faltan: tuple[str, ...] = ()

    @property
    def hay_rechazo(self) -> bool:
        return self.moodle.rechazado or self.np.rechazado

    @property
    def puede_revisar(self) -> bool:
        """
        Si tiene sentido intentar la revisión.

        Alcanza con que Moodle responda y que nada haya sido rechazado. Que a
        Notas Parciales no se lo haya podido comprobar **no** detiene nada: la
        revisión es justamente lo que averigua los grupos oficiales, y exigir
        que ya estuvieran averiguados para poder averiguarlos no dejaría
        empezar nunca.
        """
        return not self.faltan and self.moodle.ok and not self.hay_rechazo

    @property
    def texto(self) -> str:
        if self.faltan:
            return (
                "Faltan credenciales: "
                + ", ".join(self.faltan)
                + ".\nPulsá «Configurar un curso…» para volver a guardarlas."
            )

        linea = f"{self.moodle.marca}   {self.np.marca}"
        detalles = [i.explicacion for i in (self.moodle, self.np) if i.explicacion]
        return "\n\n".join([linea, *detalles])


@dataclass
class Ensayo:
    """El resultado de una prueba con la escritura tapiada (specs/003, A-12)."""

    report: RunReport
    diario: Path
    interceptadas: int


@dataclass
class Contexto:
    """
    Lo que la ventana necesita saber para trabajar, ya resuelto.

    Se le entrega hecho en vez de dejar que lo lea ella misma: así abrir la
    ventana no puede fallar por un archivo mal escrito, y el fallo se cuenta
    donde todavía se puede explicar con calma.
    """

    creds: Credentials
    config: Config | None = None


class Ventana(QMainWindow):
    """La pantalla semanal: revisar, autorizar, sincronizar."""

    def __init__(self, ctx: Contexto, work_dir: Path) -> None:
        super().__init__()
        self.ctx = ctx
        self.work_dir = work_dir
        self.prep: Preparacion | None = None
        self._tarea: Tarea | None = None
        self._casillas: dict[tuple[str, str], QTableWidgetItem] = {}
        self._filas: list[FilaPlan] = []
        self.estado_ingresos: EstadoIngresos | None = None

        self.setWindowTitle("mnsync — de Moodle a Notas Parciales")
        self._construir()
        self._poblar_cursos()

    # --- construcción -----------------------------------------------------
    def _construir(self) -> None:
        self.ingresos = QLabel("Comprobando tus contraseñas…")
        self.ingresos.setWordWrap(True)
        self.ingresos.setTextFormat(Qt.TextFormat.PlainText)

        self.cursos = QComboBox()
        self.cursos.currentIndexChanged.connect(self._cambio_de_curso)

        self.boton_revisar = QPushButton("Revisar de nuevo")
        self.boton_revisar.clicked.connect(self.revisar)

        self.boton_configurar = QPushButton("Configurar un curso…")
        self.boton_configurar.clicked.connect(self.configurar)

        arriba = QHBoxLayout()
        arriba.addWidget(QLabel("Curso:"))
        arriba.addWidget(self.cursos, 1)
        arriba.addWidget(self.boton_revisar)
        arriba.addWidget(self.boton_configurar)

        self.aviso = QLabel("")
        self.aviso.setWordWrap(True)
        self.aviso.setTextFormat(Qt.TextFormat.PlainText)

        self.recuento = QLabel("")
        self.recuento.setWordWrap(True)
        self.recuento.setTextFormat(Qt.TextFormat.PlainText)

        self.ver = QComboBox()
        for etiqueta, _ in FILTROS:
            self.ver.addItem(etiqueta)
        self.ver.currentIndexChanged.connect(self._repintar)

        filtro = QHBoxLayout()
        filtro.addWidget(QLabel("Ver:"))
        filtro.addWidget(self.ver)
        filtro.addStretch(1)

        self.tabla = QTableWidget(0, len(COLUMNAS))
        self.tabla.setHorizontalHeaderLabels(COLUMNAS)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tabla.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.tabla.itemChanged.connect(self._cambio_de_autorizacion)

        self.boton_ensayo = QPushButton("Probar sin escribir nada")
        self.boton_ensayo.setToolTip(
            "Hace el proceso completo contra el sistema real de la UNED, pero "
            "tapia la única llamada que escribe. Sirve para ver qué pasaría sin "
            "que pase."
        )
        self.boton_ensayo.clicked.connect(self.ensayar)

        self.boton_sync = QPushButton("Sincronizar")
        self.boton_sync.setDefault(True)
        self.boton_sync.clicked.connect(self.sincronizar)

        abajo = QHBoxLayout()
        abajo.addWidget(self.boton_ensayo)
        abajo.addStretch(1)
        abajo.addWidget(self.boton_sync)

        self.estado = QLabel("")
        self.estado.setWordWrap(True)
        self.estado.setTextFormat(Qt.TextFormat.PlainText)

        caja = QVBoxLayout()
        caja.addWidget(self.ingresos)
        caja.addLayout(arriba)
        caja.addWidget(self.aviso)
        caja.addWidget(self.recuento)
        caja.addLayout(filtro)
        caja.addWidget(self.tabla, 1)
        caja.addLayout(abajo)
        caja.addWidget(self.estado)

        central = QWidget()
        central.setLayout(caja)
        self.setCentralWidget(central)
        self._ocupada(False)
        self._permitir_escritura(False)

    def _poblar_cursos(self) -> None:
        self.cursos.blockSignals(True)
        self.cursos.clear()
        for c in self.ctx.config.courses if self.ctx.config else ():
            self.cursos.addItem(c.id, c)
        self.cursos.blockSignals(False)

        hay = self.cursos.count() > 0
        self.boton_revisar.setEnabled(hay)
        if not hay:
            self.aviso.setText(
                "Todavía no hay ningún curso configurado. Pulsá «Configurar un "
                "curso…» para configurar el primero."
            )
        else:
            self.aviso.setText("")

    @property
    def curso(self) -> Course | None:
        return self.cursos.currentData()

    # --- comprobación de ingresos (A-14) ----------------------------------
    def comprobar_ingresos(self) -> None:
        """
        Lo primero que pasa al abrir: ¿siguen sirviendo las contraseñas?

        Se hace acá y no cuando hagan falta porque una contraseña vencida
        descubierta a mitad de una sincronización parece un programa roto, y no
        lo que es: una contraseña que hay que cambiar.
        """
        self._correr(
            revisar_ingresos,
            self.ctx.creds,
            self.curso,
            self.work_dir,
            al_terminar=self._con_ingresos,
            mientras="Comprobando tus contraseñas…",
        )

    def _con_ingresos(self, estado: EstadoIngresos) -> None:
        self.estado_ingresos = estado
        self.ingresos.setText(estado.texto)
        if estado.puede_revisar and self.curso is not None:
            self.revisar()

    def _ingreso_confirmado_por_la_revision(self, prep: Preparacion) -> None:
        """
        Una revisión que trajo destinos **es** un ingreso exitoso.

        Al arrancar, un curso recién configurado todavía no tiene grupos
        oficiales averiguados y la comprobación no puede hacerse. Pero la
        revisión los averigua consultando el servidor, así que cuando termina
        bien ya hay evidencia de sobra. Dejar el aviso como estaba pondría
        «no se pudo comprobar» encima de una tabla llena de respuestas de ese
        mismo servidor, y un programa que se contradice a sí mismo en pantalla
        no merece que se le crea ninguna de las dos cosas.
        """
        estado = getattr(self, "estado_ingresos", None)
        if estado is None or estado.np.ok or not prep.destinos:
            return
        estado.np.estado = OK
        estado.np.detalle = ""
        estado.np.remedio = ""
        self.ingresos.setText(estado.texto)

    # --- revisar (fases A y B, sin escribir) ------------------------------
    def revisar(self) -> None:
        if self.curso is None:
            return
        self._permitir_escritura(False)
        self.prep = None
        self.tabla.setRowCount(0)
        self._correr(
            preparar,
            self.curso,
            self.ctx.creds,
            self.work_dir,
            al_terminar=self.mostrar,
            mientras=(
                "Bajando tus notas de Moodle y preguntándole a Notas Parciales "
                "qué hay puesto. No se escribe nada."
            ),
        )

    def mostrar(self, prep: Preparacion) -> None:
        """Enseña el plan completo: los recuentos, el aviso y fila por fila."""
        self.prep = prep
        self._ingreso_confirmado_por_la_revision(prep)
        self._filas = prep.filas()
        self.recuento.setText(_texto_recuento(self._filas))
        self.aviso.setText(_texto_aviso(prep))
        self._repintar()

        if prep.detenida:
            self.estado.setText(
                "No se puede sincronizar hasta resolver eso. No se escribió nada."
            )
            self._permitir_escritura(False)
            return

        self.estado.setText(
            "Revisá la lista. Las notas que ya estaban puestas necesitan tu "
            "autorización una por una."
        )
        self._permitir_escritura(True)

    def _repintar(self) -> None:
        """Vuelve a dibujar la tabla, conservando lo que ya estaba autorizado."""
        autorizadas = self.autorizadas()
        _, acciones = FILTROS[max(0, self.ver.currentIndex())]

        self.tabla.blockSignals(True)
        self.tabla.setRowCount(0)
        self._casillas.clear()

        for fila in self._filas:
            if acciones is not None and fila.accion not in acciones:
                continue
            i = self.tabla.rowCount()
            self.tabla.insertRow(i)
            etiqueta = ACCIONES.get(fila.accion, (fila.accion, ""))[0]
            for col, valor in enumerate(
                (
                    fila.nombre or "(sin nombre)",
                    fila.cedula,
                    fila.instrumento_nombre or fila.instrumento,
                    fila.destino.label,
                    nota_legible(fila.nota_remota),
                    nota_legible(fila.nota_local),
                    etiqueta,
                )
            ):
                self.tabla.setItem(i, col, QTableWidgetItem(valor))

            self.tabla.setItem(i, 7, self._casilla(fila, fila.clave in autorizadas))

        self.tabla.blockSignals(False)
        self.tabla.resizeColumnsToContents()
        self.tabla.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        # Las señales estuvieron tapadas mientras se dibujaba, así que el
        # recuento del botón se rehace a mano: si no, al cambiar de filtro
        # diría un número que ya no corresponde.
        self._actualizar_boton()

    def _casilla(self, fila: FilaPlan, marcada: bool) -> QTableWidgetItem:
        """
        La celda de autorización. Solo las sobrescrituras la tienen marcable.

        Deliberadamente empieza **sin marcar**: el silencio no autoriza cambiar
        una nota que alguien ya puso.
        """
        item = QTableWidgetItem()
        if not fila.es_sobrescritura:
            item.setText("—")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            return item

        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        item.setCheckState(
            Qt.CheckState.Checked if marcada else Qt.CheckState.Unchecked
        )
        item.setData(Qt.ItemDataRole.UserRole, fila.clave)
        self._casillas[fila.clave] = item
        return item

    def _cambio_de_autorizacion(self, item: QTableWidgetItem) -> None:
        if item.column() == 7:
            self._actualizar_boton()

    def autorizadas(self) -> set[tuple[str, str]]:
        """Las sobrescrituras que el profesor marcó, una por una."""
        return {
            clave
            for clave, item in self._casillas.items()
            if item.checkState() == Qt.CheckState.Checked
        }

    # --- ensayo (A-12) ----------------------------------------------------
    def ensayar(self) -> None:
        if self.prep is None:
            return
        self._correr(
            ensayar_corrida,
            self.prep,
            self.autorizadas(),
            self.work_dir,
            al_terminar=self._con_ensayo,
            mientras=(
                "Haciendo el proceso completo contra el sistema real, con la "
                "escritura tapiada…"
            ),
        )

    def _con_ensayo(self, ensayo: Ensayo) -> None:
        ruta = write_report(ensayo.report, self.work_dir)
        if ensayo.interceptadas:
            texto = (
                f"Se interceptaron {ensayo.interceptadas} escritura(s): eso es "
                "exactamente lo que una corrida real escribiría.\n"
                f"Detalle: {ensayo.diario}\nReporte: {ruta}"
            )
        else:
            texto = (
                "No había nada pendiente: todas las notas ya estaban puestas. "
                "El ensayo recorrió el camino completo de escritura y no "
                "encontró nada que escribir.\n"
                f"Reporte: {ruta}"
            )
        self.estado.setText(texto)
        # El ensayo no escribió, así que los planes siguen valiendo.
        self._permitir_escritura(True)

    # --- sincronizar (A-09, A-13) -----------------------------------------
    def sincronizar(self) -> None:
        if self.prep is None:
            return
        autorizadas = self.autorizadas()
        if not self._confirmar(autorizadas):
            return
        self._correr(
            aplicar,
            self.prep,
            al_terminar=self._con_reporte,
            mientras="Escribiendo en Notas Parciales…",
            commit=True,
            autorizadas=autorizadas,
        )

    def _confirmar(self, autorizadas: set[tuple[str, str]]) -> bool:
        """La última pregunta antes de escribir. El texto se arma aparte."""
        resumen, advertencia = texto_confirmacion(self._filas, autorizadas)

        caja = QMessageBox(self)
        caja.setIcon(QMessageBox.Icon.Warning)
        caja.setWindowTitle("Confirmar la escritura")
        caja.setText(resumen)
        caja.setInformativeText(advertencia)
        si = caja.addButton("Escribir ahora", QMessageBox.ButtonRole.AcceptRole)
        no = caja.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        # El botón que viene puesto es el que no escribe: una ventana que se
        # confirma sola con la tecla Enter no es una confirmación.
        caja.setDefaultButton(no)
        caja.setEscapeButton(no)
        caja.exec()
        return caja.clickedButton() is si

    def _con_reporte(self, report: RunReport) -> None:
        ruta = write_report(report, self.work_dir)
        self.estado.setText(
            f"Se escribieron {report.total_escritas} nota(s).\n"
            f"{report.to_console()}\n"
            f"Reporte: {ruta}"
        )
        # Después de escribir, los planes describen un pasado. Volver a
        # ofrecerlos permitiría escribir dos veces sobre datos viejos.
        self.prep = None
        self._permitir_escritura(False)
        self.aviso.setText(
            "Los datos de esta pantalla son de antes de escribir. Pulsá "
            "«Revisar de nuevo» para verlos actualizados."
        )

    # --- configurar otro curso -------------------------------------------
    def configurar(self) -> None:
        """
        Abre el asistente sin cerrar la ventana.

        Un profesor da varias asignaturas y cada cuatrimestre cambian los
        códigos, así que configurar no puede ser algo que solo pase la primera
        vez que se abre el programa.
        """
        from .asistente import construir_asistente

        asistente = construir_asistente(self.work_dir)
        asistente.setModal(True)
        asistente.exec()
        if asistente.escrito is None:
            return
        self._recargar(asistente.borrador.course_id_local)

    def _recargar(self, seleccionar: str = "") -> None:
        """
        Vuelve a leer la configuración y las credenciales después del asistente.

        Las credenciales también, y no solo los cursos: el asistente pudo haber
        guardado una contraseña nueva, y seguir usando la vieja daría un fallo
        que ya sabemos explicar mejor.
        """
        from ..config import load_config, load_credentials

        try:
            self.ctx.config = load_config()
        except MnsyncError as e:
            self.aviso.setText("✗ " + str(e))
            return
        self.ctx.creds = load_credentials(require=False)

        self._poblar_cursos()
        if seleccionar:
            i = self.cursos.findText(seleccionar)
            if i >= 0:
                self.cursos.setCurrentIndex(i)
        self._cambio_de_curso()
        self.comprobar_ingresos()

    # --- plomería ---------------------------------------------------------
    def _cambio_de_curso(self) -> None:
        self.prep = None
        self._filas = []
        # Se repinta en vez de vaciar la tabla a mano: vaciarla destruye las
        # celdas del lado de Qt, y las autorizaciones guardarían referencias a
        # celdas que ya no existen. Repintar sin filas las deja limpias.
        self._repintar()
        self.recuento.setText("")
        self._permitir_escritura(False)

    def _correr(self, funcion, *args, al_terminar=None, mientras: str, **kwargs) -> None:
        self._ocupada(True)
        self.estado.setText(mientras)

        tarea = Tarea(funcion, *args, **kwargs)
        tarea.terminada.connect(lambda r: self._listo(r, al_terminar))
        tarea.fallada.connect(self._fallo)
        tarea.finished.connect(tarea.deleteLater)
        self._tarea = tarea
        tarea.start()

    def _listo(self, resultado, al_terminar) -> None:
        self._ocupada(False)
        if al_terminar is not None:
            al_terminar(resultado)

    def _fallo(self, fallo: Fallo) -> None:
        self._ocupada(False)
        self._permitir_escritura(False)
        self.estado.setText("✗ " + fallo.texto)

    def _ocupada(self, ocupada: bool) -> None:
        self.boton_revisar.setEnabled(not ocupada and self.cursos.count() > 0)
        self.cursos.setEnabled(not ocupada)
        self.boton_configurar.setEnabled(not ocupada)
        self._trabajando = ocupada
        if ocupada:
            self.boton_sync.setEnabled(False)
            self.boton_ensayo.setEnabled(False)
        else:
            self.boton_ensayo.setEnabled(getattr(self, "_escritura", False))
        self._actualizar_boton()

    def _permitir_escritura(self, permitir: bool) -> None:
        self._escritura = permitir
        self.boton_ensayo.setEnabled(permitir)
        self._actualizar_boton()

    def _actualizar_boton(self) -> None:
        permitir = getattr(self, "_escritura", False) and not getattr(
            self, "_trabajando", False
        )
        self.boton_sync.setEnabled(permitir)
        marcadas = len(self.autorizadas()) if permitir else 0
        self.boton_sync.setText(
            f"Sincronizar ({marcadas} sobrescritura(s) autorizadas)"
            if marcadas
            else "Sincronizar"
        )


# ---------------------------------------------------------------------------
# Los textos que acompañan a la tabla
# ---------------------------------------------------------------------------
#: Lo que el sistema de la UNED escribe en una celda cuando no hay una nota.
#:
#: Son números reservados, no calificaciones: 998 significa «no presentó», 999
#: «sin nota» y 994 «retiro justificado». En la tabla se muestran con su
#: significado, porque «998» al lado de un 9,4 se lee como una nota altísima.
MARCADORES = {
    "998": "no presentó",
    "999": "sin nota",
    "994": "retirado",
}

#: Lo que escribe un profesor en Moodle para decir «no entregó».
GUION_NO_PRESENTO = "-"


def nota_legible(valor: str) -> str:
    """
    Una celda de nota tal como conviene enseñarla.

    Los marcadores del servidor y el guion de Moodle son convenciones internas:
    quien lee la tabla necesita el significado, no el código. Cualquier otra
    cosa se muestra tal cual vino, sin reinterpretarla: una nota es de quien la
    puso, y este programa no está para redondearla.
    """
    crudo = (valor or "").strip()
    if not crudo:
        return "—"
    if crudo == GUION_NO_PRESENTO:
        return "no presentó"

    # El script ya rotula algunos como «998 (no presentó)»; se toma el número.
    numero = crudo.split()[0].split(".")[0]
    return MARCADORES.get(numero, crudo)


def _texto_recuento(filas: list[FilaPlan]) -> str:
    """
    Una línea por cada acción posible del plan, incluidas las que dieron cero.

    Enseñar las siete y no solo las que salieron es lo que evita que la vista
    se lea como «se sube o no se sube» (specs/003, A-10): el profesor ve el
    vocabulario completo del sistema y dónde cayó lo suyo dentro de él.
    """
    conteo: dict[str, int] = {}
    for f in filas:
        conteo[f.accion] = conteo.get(f.accion, 0) + 1

    lineas = [f"{len(filas)} fila(s) en total:"]
    for accion in ORDEN_ACCIONES:
        etiqueta = ACCIONES.get(accion, (accion, ""))[0]
        lineas.append(f"    {conteo.get(accion, 0):>4}  {etiqueta}")

    otras = sorted(set(conteo) - set(ORDEN_ACCIONES))
    lineas += [f"    {conteo[a]:>4}  {a}" for a in otras]
    return "\n".join(lineas)


def texto_confirmacion(
    filas: list[FilaPlan], autorizadas: set[tuple[str, str]]
) -> tuple[str, str]:
    """
    Lo que se pregunta justo antes de escribir: el resumen y la advertencia.

    La advertencia no es «¿está seguro?». Es qué es exactamente lo que no se
    puede deshacer desde acá (specs/003, A-13): un profesor que sabe que va a
    tener que entrar a la página de la UNED a arreglar un error decide distinto
    que uno que cree que hay un botón para volver atrás.
    """
    nuevas = sum(1 for f in filas if f.accion in ("upload", "mark_not_presented"))
    resumen = [f"Se van a escribir {nuevas} nota(s) nuevas."]

    pendientes = sum(1 for f in filas if f.es_sobrescritura)
    if pendientes:
        resumen.append(
            f"De las {pendientes} nota(s) ya puestas que cambiarían, autorizaste "
            f"{len(autorizadas)}. El resto queda como está."
        )

    advertencia = (
        "Una vez escrita, esta nota queda en el sistema oficial de la UNED. Este "
        "programa NO puede deshacerla: para corregir un error habría que entrar "
        "a Notas Parciales y hacerlo a mano.\n\n"
        "¿Escribir ahora?"
    )
    return "\n".join(resumen), advertencia


def _texto_aviso(prep: Preparacion) -> str:
    """Lo que hay que decir antes de la tabla: frenos y estudiantes sin grupo."""
    partes: list[str] = []

    v = prep.routing_verdict
    if v.blocked:
        partes.append("🛑 " + v.reason + "\n\n" + v.remedy)

    for o in prep.fallidos:
        partes.append(f"❌ {o.label}: {o.error}")

    huerfanos = sorted(prep.routing.sin_destino())
    if huerfanos:
        nombres = "\n".join(
            f"    · {prep.routing.nombres.get(c) or '(sin nombre)'}  —  {c}"
            for c in huerfanos
        )
        partes.append(
            f"⚠ {len(huerfanos)} estudiante(s) están en Moodle pero no en ningún "
            "grupo de Notas Parciales. Sus notas NO se van a subir:\n"
            f"{nombres}\n"
            "Suele significar que no quedaron matriculados. El resto sube con "
            "normalidad."
        )

    return "\n\n".join(partes)


# ---------------------------------------------------------------------------
# El trabajo, que corre fuera del hilo de la ventana
# ---------------------------------------------------------------------------
def revisar_ingresos(
    creds: Credentials, course: Course | None, work_dir: Path
) -> EstadoIngresos:
    """
    Le pregunta a los dos sistemas si las credenciales guardadas siguen valiendo.

    A Moodle se le entra de verdad. A Notas Parciales se le pide el ``probe``,
    que es su comprobación más barata, y solo si hay un curso configurado: sin
    los códigos de la asignatura no hay nada que preguntarle.

    Un fallo solo se llama «rechazo» cuando la respuesta del servidor lo dice.
    Todo lo demás queda como «no se pudo comprobar», con el detalle a la vista
    (specs/003, A-14).

    Ninguna de las dos escribe nada.
    """
    from ..config import missing_credentials
    from ..moodle_export import MoodleSession
    from ..uploader import parece_rechazo_de_credenciales

    faltan = tuple(missing_credentials(creds))
    if faltan:
        return EstadoIngresos(faltan=faltan)

    estado = EstadoIngresos()

    try:
        sesion = MoodleSession(creds.moodle_url)
        sesion.login(creds.moodle_username, creds.moodle_password)
        estado.moodle.estado = OK
    except MnsyncError as e:
        _anotar(estado.moodle, e, rechazo="rechaz" in e.mensaje.lower())

    if course is None:
        estado.np.remedio = "Todavía no hay ningún curso configurado."
        return estado

    try:
        Uploader(course, creds, work_dir).probar_ingreso()
        estado.np.estado = OK
    except MnsyncError as e:
        _anotar(
            estado.np,
            e,
            rechazo=parece_rechazo_de_credenciales(f"{e.mensaje} {e.remedio or ''}"),
        )

    return estado


def _anotar(ingreso: Ingreso, error: MnsyncError, *, rechazo: bool) -> None:
    """Deja en el ingreso lo que pasó, sin adornarlo ni suavizarlo."""
    ingreso.estado = RECHAZADO if rechazo else SIN_COMPROBAR
    ingreso.detalle = "" if rechazo else error.mensaje
    ingreso.remedio = (
        "Volvé a configurar el curso para guardar la nueva."
        if rechazo
        else (error.remedio or "")
    )


def ensayar_corrida(
    prep: Preparacion, autorizadas: set[tuple[str, str]], work_dir: Path
) -> Ensayo:
    """
    La corrida entera contra el servidor real, con la escritura tapiada (A-12).

    Recorre el mismo camino que una sincronización de verdad —el ingreso real,
    el roster real, los instrumentos reales— y deja anotado por escrito qué
    habría escrito. Es lo más parecido a escribir que se puede hacer sin
    escribir.

    Si la reja no puede demostrar que quedó puesta, el ensayo no ocurre: una
    reja que no se puede verificar no es una reja.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    # Mismo nombre que el de la terminal: lo cubre el .gitignore y lo lee
    # «mnsync leer-ensayo» sin tener que enseñar un segundo patrón.
    diario = work_dir / f"rehearsal_{prep.course.id}_{datetime.now():%Y%m%d_%H%M}.jsonl"

    if not reja_verificada(work_dir):
        raise MnsyncError(
            "No se pudo confirmar que la escritura quedara tapiada.",
            remedio=(
                "El ensayo NO se hizo, y no se escribió nada. Reportalo a quien "
                "mantiene el programa antes de sincronizar."
            ),
        )

    with prep.uploader.reja_de_escritura(diario):
        report = aplicar(prep, commit=True, autorizadas=autorizadas)

    interceptadas = (
        sum(1 for ln in diario.read_text(encoding="utf-8").splitlines() if ln.strip())
        if diario.exists()
        else 0
    )
    return Ensayo(report=report, diario=diario, interceptadas=interceptadas)
