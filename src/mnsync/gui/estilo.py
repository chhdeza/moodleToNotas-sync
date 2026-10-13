"""
El aspecto de la aplicación: colores, letras y el tono de cada mensaje.

Vive en un solo lugar por la misma razón que ``acciones.py``: el color con que
se pinta «cambiaría una nota existente» en la tabla tiene que ser el mismo que
el de su recuento, y dos copias de una paleta terminan diciendo cosas
distintas.

Los colores no decoran: cada uno nombra una acción del plan o la gravedad de un
mensaje. Si algo está en ámbar es porque pisaría una nota ya puesta, y en ningún
otro lado de la ventana se usa ámbar.

Las letras son las que ya trae Windows —Bahnschrift, la DIN de los formularios
y la señalización oficial, para títulos y cifras; Segoe UI para leer— así que
no hay archivos de fuentes que distribuir. En otro sistema Qt elige una
parecida y la ventana sigue funcionando igual.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from ..acciones import NO_PRESENTO, RETIRADO, REVISAR, SIN_ROSTER, SOBRESCRIBIRIA, SUBIR, YA_ESTABA

# --- la paleta --------------------------------------------------------------
TINTA = "#1B2A4A"      # el texto, y el único botón que escribe
MESA = "#E8ECF1"       # el fondo de la ventana
PAPEL = "#FFFFFF"      # lo que se lee: tabla, campos, paneles
REGLA = "#C9D1DC"      # bordes y divisiones
APAGADO = "#5B6679"    # texto secundario
TENUE = "#A9B2BF"      # lo desactivado, y los ceros del recuento
FOCO = "#2F6FE0"       # el anillo de foco del teclado


@dataclass(frozen=True)
class Tinte:
    """Un color con su fondo claro: el texto va en ``tinta``, la celda en ``fondo``."""

    tinta: str
    fondo: str


VERDE = Tinte("#1F7A4D", "#E4F3EA")
VIOLETA = Tinte("#5E45A0", "#EEE9F8")
AMBAR = Tinte("#9A5600", "#FFF0D6")
ROJO = Tinte("#B42318", "#FDE8E6")
GRIS = Tinte(APAGADO, "#F1F3F6")
AZUL = Tinte("#2456A6", "#E7EEFB")

#: El color de cada acción del plan. Una acción que no está acá se pinta gris.
TINTE_ACCION: dict[str, Tinte] = {
    SUBIR: VERDE,
    NO_PRESENTO: VIOLETA,
    SOBRESCRIBIRIA: AMBAR,
    REVISAR: ROJO,
    YA_ESTABA: GRIS,
    RETIRADO: GRIS,
    SIN_ROSTER: GRIS,
}


def tinte(accion: str) -> Tinte:
    return TINTE_ACCION.get(accion, GRIS)


#: El tono de un mensaje según cómo empieza. Los textos de la ventana ya
#: abren con su marca —✓, ⚠, ✗—, así que el color sale de ahí y no hay que
#: acordarse de pedirlo en cada lugar donde se escribe un mensaje.
MARCAS = (
    ("✓", "ok"),
    ("✅", "ok"),
    ("⚠", "cuidado"),
    ("✗", "error"),
    ("❌", "error"),
    ("🛑", "error"),
)


def tono_de(texto: str) -> str:
    inicio = texto.lstrip()
    for marca, tono in MARCAS:
        if inicio.startswith(marca):
            return tono
    return "info"


class Mensaje(QLabel):
    """
    Una etiqueta que se pinta según lo que dice, y desaparece si no dice nada.

    Se comporta como cualquier ``QLabel`` —``setText`` y ``text``—, así que
    reemplazarla no cambia nada para quien la usa. El tono se deduce de la
    marca con que empieza el texto; quien sepa más puede darlo explícito.

    El texto se puede seleccionar: los mensajes traen rutas de reportes, y un
    profesor que las necesita no tendría que copiarlas a mano.
    """

    def __init__(self, texto: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setText(texto)

    def setText(self, texto: str, tono: str | None = None) -> None:  # noqa: N802 - API de Qt
        super().setText(texto)
        self.setVisible(bool(texto))
        repintar(self, "tono", tono or tono_de(texto))


#: El margen a cada lado del texto de una celda, el mismo que el del encabezado.
AIRE = 10


class ConAire(QStyledItemDelegate):
    """
    Dibuja las celdas con margen a los costados, sin perder su color.

    La forma obvia —``padding`` en ``QTableWidget::item`` de la hoja de
    estilos— hace que Qt deje de pintar el fondo propio de cada celda, y con
    él los colores de las acciones. Así que el margen se pone acá: el fondo y
    la selección se pintan a lo ancho de la celda, y solo el contenido entra
    con margen.

    El clic se mide con el mismo margen que el dibujo; si no, la casilla de
    «Autorizo» se vería en un lugar y respondería en otro.
    """

    def _con_margen(self, option: QStyleOptionViewItem) -> QStyleOptionViewItem:
        o = QStyleOptionViewItem(option)
        o.rect = option.rect.adjusted(AIRE, 0, -AIRE, 0)
        return o

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - API de Qt
        s = super().sizeHint(option, index)
        return QSize(s.width() + 2 * AIRE, s.height())

    def paint(self, painter, option, index) -> None:
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        else:
            fondo = index.data(Qt.ItemDataRole.BackgroundRole)
            if fondo is not None:
                painter.fillRect(option.rect, QBrush(fondo))
        super().paint(painter, self._con_margen(option), index)

    def initStyleOption(self, option, index) -> None:  # noqa: N802 - API de Qt
        super().initStyleOption(option, index)
        # El fondo y la selección ya se pintaron a lo ancho; pintarlos otra
        # vez dentro del margen dejaría un recuadro. El de foco sobra igual.
        option.backgroundBrush = QBrush()
        option.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802 - API de Qt
        return super().editorEvent(event, model, self._con_margen(option), index)


def repintar(widget: QWidget, propiedad: str, valor: str) -> None:
    """Cambia una propiedad de la hoja de estilos y obliga a Qt a releerla."""
    if widget.property(propiedad) == valor:
        return
    widget.setProperty(propiedad, valor)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# --- las letras -------------------------------------------------------------
def _primera(*familias: str) -> str:
    disponibles = set(QFontDatabase.families())
    return next((f for f in familias if f in disponibles), familias[-1])


@dataclass(frozen=True)
class Letras:
    texto: str
    titulo: str
    cifras: str


def letras() -> Letras:
    """Las familias que hay en este equipo, de la preferida a la que siempre está."""
    return Letras(
        texto=_primera("Segoe UI Variable Text", "Segoe UI", "Noto Sans", "DejaVu Sans"),
        titulo=_primera("Bahnschrift SemiBold", "Bahnschrift", "Segoe UI Semibold", "DejaVu Sans"),
        cifras=_primera("Bahnschrift", "Segoe UI", "DejaVu Sans"),
    )


# --- la hoja de estilos -----------------------------------------------------
def hoja(f: Letras) -> str:
    return f"""
    QWidget {{
        font-family: "{f.texto}";
        font-size: 10pt;
        color: {TINTA};
    }}
    QMainWindow, QWizard, QDialog {{ background: {MESA}; }}
    QWizardPage {{ background: {PAPEL}; }}
    QToolTip {{
        background: {TINTA}; color: {PAPEL}; border: none; padding: 6px 8px;
    }}

    /* --- la cabecera de la ventana semanal --- */
    #cabecera {{ background: {PAPEL}; border-bottom: 1px solid {REGLA}; }}
    #titulo {{
        font-family: "{f.titulo}"; font-size: 17pt; color: {TINTA};
    }}
    #rotulo {{ color: {APAGADO}; }}
    #seccion {{ font-family: "{f.titulo}"; font-size: 11pt; padding-top: 4px; }}
    #pie {{ background: {PAPEL}; border-top: 1px solid {REGLA}; }}

    /* --- los mensajes, por tono --- */
    Mensaje {{
        padding: 8px 12px;
        border-left: 4px solid {TENUE};
        background: {GRIS.fondo};
    }}
    Mensaje[tono="ok"]      {{ border-left-color: {VERDE.tinta}; background: {VERDE.fondo}; }}
    Mensaje[tono="cuidado"] {{ border-left-color: {AMBAR.tinta}; background: {AMBAR.fondo}; }}
    Mensaje[tono="error"]   {{ border-left-color: {ROJO.tinta};  background: {ROJO.fondo}; }}
    Mensaje[tono="info"]    {{ border-left-color: {AZUL.tinta};  background: {AZUL.fondo}; }}
    Mensaje[tono="trabajando"] {{ border-left-color: {TENUE}; background: {PAPEL}; color: {APAGADO}; }}

    /* --- el recuento --- */
    #recuento {{ background: {PAPEL}; border: 1px solid {REGLA}; }}
    #recuento QLabel[cifra="si"] {{
        font-family: "{f.cifras}"; font-size: 20pt; font-weight: 600;
    }}
    #recuento QLabel[cero="si"] {{ color: {TENUE}; }}
    #recuento QLabel[nombre="si"] {{ color: {APAGADO}; font-size: 9pt; }}

    /* --- botones --- */
    QPushButton {{
        font-family: "{f.cifras}";
        font-size: 10pt;
        background: {PAPEL};
        border: 1px solid {REGLA};
        border-radius: 4px;
        padding: 7px 16px;
        min-height: 18px;
    }}
    QPushButton:hover {{ border-color: {APAGADO}; }}
    QPushButton:pressed {{ background: {MESA}; }}
    QPushButton:disabled {{ color: {TENUE}; border-color: {MESA}; background: {MESA}; }}
    QPushButton:focus {{ border: 2px solid {FOCO}; padding: 6px 15px; }}
    QPushButton[primario="si"] {{
        background: {TINTA}; color: {PAPEL}; border-color: {TINTA};
        font-family: "{f.titulo}";
        padding: 8px 22px;
    }}
    QPushButton[primario="si"]:hover {{ background: #2A3D66; }}
    QPushButton[primario="si"]:disabled {{
        background: {MESA}; color: {TENUE}; border-color: {REGLA};
    }}
    QPushButton[primario="si"]:focus {{ border: 2px solid {FOCO}; padding: 7px 21px; }}

    /* --- campos --- */
    QLineEdit, QComboBox {{
        background: {PAPEL};
        border: 1px solid {REGLA};
        border-radius: 4px;
        padding: 5px 8px;
        min-height: 20px;
        selection-background-color: {AZUL.fondo};
        selection-color: {TINTA};
    }}
    QLineEdit:focus, QComboBox:focus {{ border: 2px solid {FOCO}; padding: 4px 7px; }}
    QLineEdit:disabled, QComboBox:disabled {{ color: {TENUE}; background: {MESA}; }}
    /* Sin regla para ::drop-down a propósito: restilarlo hace que Qt deje de
       dibujar la flecha nativa, y el desplegable pasa por un campo de texto
       —nadie adivina que tiene más opciones—. */
    QComboBox QAbstractItemView {{
        background: {PAPEL}; border: 1px solid {REGLA};
        selection-background-color: {AZUL.fondo}; selection-color: {TINTA};
    }}

    /* --- tablas y listas --- */
    QTableWidget, QListWidget {{
        background: {PAPEL};
        alternate-background-color: #F7F9FB;
        border: 1px solid {REGLA};
        gridline-color: transparent;
        selection-background-color: {AZUL.fondo};
        selection-color: {TINTA};
    }}
    /* Sin regla para «QTableWidget::item»: en cuanto la hoja la define, Qt
       deja de pintar el fondo propio de cada celda, y con él los colores de
       las acciones. */
    QListWidget::item {{ padding: 6px 8px; }}
    QHeaderView::section {{
        background: {PAPEL};
        color: {APAGADO};
        font-weight: 600;
        border: none;
        border-bottom: 2px solid {REGLA};
        padding: 8px {AIRE}px;
    }}

    QProgressBar {{
        background: {MESA}; border: none; max-height: 4px; min-height: 4px;
    }}
    QProgressBar::chunk {{ background: {TINTA}; }}

    QCheckBox {{ spacing: 8px; }}
    """


def aplicar(app: QApplication) -> None:
    """
    Le pone a la aplicación su aspecto.

    Se usa el estilo «Fusion» porque es el único que dibuja igual en todos los
    Windows y respeta la hoja de estilos entera; el nativo ignora parte de ella
    según la versión, y una ventana que cambia de aspecto entre dos equipos de
    la misma oficina parece dos programas distintos.

    Va en claro aunque Windows esté en modo oscuro: los colores de cada acción
    se eligieron para leerse sobre papel blanco, y una tabla de notas es un
    documento que se lee, no un tablero.
    """
    app.setStyle("Fusion")

    paleta = QPalette()
    for rol, color in (
        (QPalette.ColorRole.Window, MESA),
        (QPalette.ColorRole.WindowText, TINTA),
        (QPalette.ColorRole.Base, PAPEL),
        (QPalette.ColorRole.AlternateBase, "#F7F9FB"),
        (QPalette.ColorRole.Text, TINTA),
        (QPalette.ColorRole.Button, PAPEL),
        (QPalette.ColorRole.ButtonText, TINTA),
        (QPalette.ColorRole.Highlight, AZUL.fondo),
        (QPalette.ColorRole.HighlightedText, TINTA),
        (QPalette.ColorRole.PlaceholderText, TENUE),
        (QPalette.ColorRole.ToolTipBase, TINTA),
        (QPalette.ColorRole.ToolTipText, PAPEL),
    ):
        paleta.setColor(rol, QColor(color))
    paleta.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(TENUE))
    paleta.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(TENUE))
    paleta.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(TENUE))
    app.setPalette(paleta)

    f = letras()
    app.setFont(QFont(f.texto, 10))
    app.setStyleSheet(hoja(f))
