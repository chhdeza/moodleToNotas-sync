"""
Lectura de formularios HTML de Moodle.

La estrategia es **repetir el formulario, no reconstruirlo**: se cosecha cada
campo que el servidor mandó —ocultos, casillas, menús— y se reenvía tal cual,
cambiando solo lo que hace falta.

Reconstruir el formulario a mano exigiría acertarle a nombres de campo que
cambian entre versiones de Moodle, y equivocarse ahí no da un error claro:
da una descarga silenciosamente distinta a la pedida.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class Option:
    """Una opción de un menú desplegable."""

    value: str
    label: str
    selected: bool = False


@dataclass
class Select:
    """Un menú desplegable con sus opciones."""

    name: str
    options: list[Option] = field(default_factory=list)

    @property
    def selected_value(self) -> str | None:
        for o in self.options:
            if o.selected:
                return o.value
        return None


@dataclass
class Form:
    """Un formulario ya cosechado, listo para reenviar."""

    action: str = ""
    method: str = "post"
    #: Campos que se envían tal cual (ocultos, texto, casillas marcadas...).
    fields: dict[str, str] = field(default_factory=dict)
    #: Nombres de todas las casillas vistas, marcadas o no.
    checkboxes: dict[str, bool] = field(default_factory=dict)
    selects: dict[str, Select] = field(default_factory=dict)

    def select(self, name: str) -> Select | None:
        return self.selects.get(name)

    def payload(self, **overrides: str) -> dict[str, str]:
        """Los campos a enviar, con los cambios pedidos aplicados encima."""
        data = dict(self.fields)
        data.update({k: str(v) for k, v in overrides.items()})
        return data


class _FormParser(HTMLParser):
    """Recorre el HTML y arma un ``Form`` por cada ``<form>`` encontrado."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[Form] = []
        self._current: Form | None = None
        self._select: Select | None = None
        self._option: Option | None = None
        self._option_text: list[str] = []
        # Los <select> de Moodle a veces viven fuera de un <form> (los
        # selectores de grupo, por ejemplo). Se guardan aparte.
        self.loose_selects: dict[str, Select] = {}

    # --- etiquetas de apertura ------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}

        if tag == "form":
            self._current = Form(
                action=a.get("action", ""),
                method=(a.get("method") or "post").lower(),
            )
            return

        if tag == "input":
            self._handle_input(a)
            return

        if tag == "select":
            name = a.get("name", "")
            if name:
                self._select = Select(name=name)
            return

        if tag == "option" and self._select is not None:
            self._option = Option(
                value=a.get("value", ""),
                label="",
                selected="selected" in a,
            )
            self._option_text = []

    def _handle_input(self, a: dict[str, str]) -> None:
        name = a.get("name", "")
        if not name:
            return
        itype = (a.get("type") or "text").lower()
        value = a.get("value", "")

        if itype in ("submit", "button", "image", "reset"):
            # Los botones solo se envían si el navegador los activó; el que
            # nos interesa se agrega explícitamente al descargar.
            return

        if self._current is None:
            return

        if itype == "checkbox":
            marcada = "checked" in a
            self._current.checkboxes[name] = marcada
            if marcada:
                self._current.fields[name] = value or "1"
            return

        if itype == "radio":
            if "checked" in a:
                self._current.fields[name] = value
            return

        self._current.fields[name] = value

    # --- contenido y cierre ---------------------------------------------
    def handle_data(self, data: str) -> None:
        if self._option is not None:
            self._option_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self._option is not None and self._select is not None:
            self._option.label = "".join(self._option_text).strip()
            self._select.options.append(self._option)
            self._option = None
            self._option_text = []
            return

        if tag == "select" and self._select is not None:
            if self._current is not None:
                self._current.selects[self._select.name] = self._select
                elegido = self._select.selected_value
                if elegido is not None:
                    self._current.fields[self._select.name] = elegido
            else:
                self.loose_selects[self._select.name] = self._select
            self._select = None
            return

        if tag == "form" and self._current is not None:
            self.forms.append(self._current)
            self._current = None


def parse_forms(html: str) -> tuple[list[Form], dict[str, Select]]:
    """Devuelve (formularios, menús sueltos fuera de cualquier formulario)."""
    p = _FormParser()
    p.feed(html)
    # Un <form> sin cerrar todavía tiene campos útiles.
    if p._current is not None:
        p.forms.append(p._current)
    return p.forms, p.loose_selects


def find_form(html: str, *, contains_field: str) -> Form | None:
    """El primer formulario que tenga un campo con ese nombre (o prefijo)."""
    forms, _ = parse_forms(html)
    for f in forms:
        if contains_field in f.fields or any(
            k.startswith(contains_field) for k in {**f.fields, **f.checkboxes}
        ):
            return f
    return None


def find_select(html: str, name: str) -> Select | None:
    """Busca un menú desplegable por nombre, esté dentro o fuera de un form."""
    forms, loose = parse_forms(html)
    if name in loose:
        return loose[name]
    for f in forms:
        if name in f.selects:
            return f.selects[name]
    return None
