"""
Servidor falso de Notas Parciales.

Habla el mismo dialecto de PageMethods de ASP.NET que
``produccion.uned.ac.cr/notasparciales``, con la fidelidad suficiente para
que ``notasparciales_upload.py`` corra **entero y de verdad** contra él —
incluido ``--commit``— sin que un solo byte llegue al sistema real.

Lo que replica:

  - ``GET /notasparciales/?direccion2=<usuario>`` entrega la cookie
    ``ASP.NET_SessionId``, sin la cual el cliente se niega a seguir.
  - Nunca devuelve un desafío 401, así que ``requests_ntlm`` pasa de largo
    sin negociar NTLM.
  - Envuelve toda respuesta como ``{"d": <payload>}``, con doble
    serialización en los métodos que la usan en producción.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .jsliteral import parse_body
from .state import NOTA_NO_NOTA, Scenario, WriteRecord

BASE_PATH = "/notasparciales"
PAGE_PATH = f"{BASE_PATH}/Formularios/CapturaNotas.aspx"

# Métodos cuyo payload el servidor real devuelve doblemente serializado
# (una cadena JSON dentro de "d"). El cliente lo desenvuelve en _unwrap_d.
_DOUBLE_ENCODED = {
    "funValidarIngresoNotas",
    "funCargarNotas",
    "funCargarNotasIndividual",
    "funObtenerInstrumentosModelo",
    "funObtUltimoCambioNota",
}

_HTML_SESION_EXPIRADA = (
    "<html><head><title>Object moved</title></head>"
    "<body><h2>Object moved to <a href='/login.aspx'>here</a>.</h2></body></html>"
)


class _Handler(BaseHTTPRequestHandler):
    scenario: Scenario  # inyectado por make_server

    # Silenciar el log a stderr: ensucia la salida de pytest.
    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    # --- utilidades de respuesta ---------------------------------------
    def _send(self, status: int, body: bytes, ctype: str, cookie: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, method: str) -> None:
        if method in _DOUBLE_ENCODED:
            envelope = {"d": json.dumps(payload, ensure_ascii=False)}
        else:
            envelope = {"d": payload}
        raw = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        self._send(200, raw, "application/json; charset=utf-8")

    def _send_html(self, body: str, cookie: str | None = None) -> None:
        self._send(200, body.encode("utf-8"), "text/html; charset=utf-8", cookie=cookie)

    # --- GET -------------------------------------------------------------
    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path in (BASE_PATH, f"{BASE_PATH}/"):
            # El cliente exige que exista ASP.NET_SessionId después de este GET.
            self._send_html(
                "<html><body>Notas Parciales (simulado)</body></html>",
                cookie="ASP.NET_SessionId=fake-session-0001; path=/",
            )
            return

        if path == PAGE_PATH:
            self._send_html("<html><body>CapturaNotas (simulado)</body></html>")
            return

        self._send(404, b"no such page", "text/plain; charset=utf-8")

    # --- POST ------------------------------------------------------------
    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith(PAGE_PATH + "/"):
            self._send(404, b"no such method", "text/plain; charset=utf-8")
            return

        method = path[len(PAGE_PATH) + 1 :]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        args = parse_body(raw)

        sc = self.scenario

        # Simular expiración de sesión: HTML en vez de JSON, igual que el real.
        if sc.session_dies_after is not None and len(sc.journal) >= sc.session_dies_after:
            self._send_html(_HTML_SESION_EXPIRADA)
            return

        handler = getattr(self, f"_m_{method}", None)
        if handler is None:
            self._send(500, f"método desconocido: {method}".encode(), "text/plain; charset=utf-8")
            return

        if method != "actualizarNotas":
            sc.read_calls.append(method)

        handler(args, method)

    # --- PageMethods de solo lectura ------------------------------------
    def _m_funValidarIngresoNotas(self, args: dict, method: str) -> None:
        self._send_json({"HayError": False, "Descripcion": "", "PermiteIngreso": True}, method)

    def _m_wmConsultarNotaMinima(self, args: dict, method: str) -> None:
        self._send_json(self.scenario.nota_minima, method)

    def _m_funCargarNotas(self, args: dict, method: str) -> None:
        cu = str(args.get("_peCU", ""))
        grupo = int(args.get("_peGrupo", 0))
        self._send_json({"Tabla_Datos": self.scenario.tabla_datos(cu, grupo)}, method)

    def _m_funCargarNotasIndividual(self, args: dict, method: str) -> None:
        cu = str(args.get("_peCU", ""))
        grupo = int(args.get("_peGrupo", 0))
        cedula = str(args.get("cedula", ""))
        filas = [r for r in self.scenario.tabla_datos(cu, grupo) if r["Cedula"] == cedula]
        self._send_json({"Tabla_Datos": filas}, method)

    def _m_funObtenerInstrumentosModelo(self, args: dict, method: str) -> None:
        """
        Devuelve las dos tablas paralelas que el cliente recorre con zip():
        ``Tabla_Modelo`` (códigos) y ``Tabla_Encabezados`` (nombres legibles).

        Se incluyen columnas de metadatos a propósito: el cliente tiene que
        descartarlas por su cuenta, y esa lógica merece ejercitarse.
        """
        modelo: list[dict[str, Any]] = [
            {"name": "Cedula", "editable": False},
            {"name": "Nombre", "editable": False},
        ]
        encabezados: list[dict[str, Any]] = [
            {"Dato": "Cédula"},
            {"Dato": "Nombre"},
        ]
        for inst in self.scenario.instruments:
            modelo.append({"name": inst.codigo, "editable": False})
            encabezados.append({"Dato": inst.nombre})
        modelo.append({"name": "Promedio", "editable": False})
        encabezados.append({"Dato": "Promedio"})

        self._send_json({"Tabla_Modelo": modelo, "Tabla_Encabezados": encabezados}, method)

    def _m_funObtUltimoCambioNota(self, args: dict, method: str) -> None:
        """
        Informa si esa casilla ya tiene nota y si el servidor deja tocarla.

        ``TieneCambios`` es verdadero cuando ya hay algo distinto de 999: es
        justo lo que hace al cliente exigir ``--allow-update``.
        """
        cedula = str(args.get("_peCedula", ""))
        instrumento = str(args.get("_peEvalua", ""))
        sc = self.scenario

        actual: float | int | None = None
        for gs in sc.groups.values():
            if cedula in gs.students:
                actual = gs.students[cedula].notas.get(instrumento)
                break

        bloqueado = (cedula, instrumento) in sc.locked
        tiene_cambios = actual is not None and actual != NOTA_NO_NOTA

        self._send_json(
            {
                "PermiteCambio": not bloqueado,
                "TieneCambios": tiene_cambios,
                "Mensaje": (
                    "El período de captura está cerrado para este instrumento."
                    if bloqueado
                    else ""
                ),
            },
            method,
        )

    # --- el ÚNICO PageMethod de escritura --------------------------------
    def _m_actualizarNotas(self, args: dict, method: str) -> None:
        """
        Registra la escritura en el diario en vez de tocar ninguna base real.

        Desarma exactamente la forma de payload que arma el cliente
        (``notasparciales_upload.py:actualizar_notas``).
        """
        sc = self.scenario

        if sc.fail_writes_with:
            self._send_json({"HayError": True, "Descripcion": sc.fail_writes_with}, method)
            return

        grupo_obj = args.get("grupo") or {}
        cu = str((grupo_obj.get("CentroUniversitario") or {}).get("Codigo", ""))
        grupo = int(grupo_obj.get("Numero", 0))
        promedios = grupo_obj.get("ListaPromedios") or [{}]
        primero = promedios[0] if promedios else {}
        cedula = str((primero.get("Estudiante") or {}).get("Cedula", ""))
        notas = primero.get("ListaNotas") or [{}]
        nota_obj = notas[0] if notas else {}
        instrumento = (nota_obj.get("InstrumentoEvaluacion") or {}).get("Codigo", "")
        instrumento_nombre = (nota_obj.get("InstrumentoEvaluacion") or {}).get("Nombre", "")
        observacion = args.get("observacion") or {}

        record = WriteRecord(
            cu=cu,
            grupo=grupo,
            cedula=cedula,
            instrumento=str(instrumento),
            instrumento_nombre=str(instrumento_nombre),
            nota=str(nota_obj.get("Nota", "")),
            tipo_nota=int(nota_obj.get("TipoNota", 0)),
            observacion_codigo=int(observacion.get("Codigo", 0) or 0),
            justificacion=str(args.get("justificacion", "") or ""),
        )
        sc.record_write(record)

        self._send_json({"HayError": False, "Descripcion": "Nota actualizada"}, method)


class FakeNotasParciales:
    """
    Servidor falso corriendo en un hilo, sobre un puerto libre.

    Uso típico::

        with FakeNotasParciales(scenario) as fake:
            os.environ["NP_BASE_URL"] = fake.base_url
            ...
            assert fake.journal_summary() == ["0117540192/Tar1 → 8.9"]
    """

    def __init__(self, scenario: Scenario, host: str = "127.0.0.1", port: int = 0):
        self.scenario = scenario
        handler = type("BoundHandler", (_Handler,), {"scenario": scenario})
        self._srv = ThreadingHTTPServer((host, port), handler)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self._srv.server_address[1]

    @property
    def base_url(self) -> str:
        """Lo que va en ``NP_BASE_URL``."""
        return f"http://127.0.0.1:{self.port}{BASE_PATH}"

    # --- atajos hacia el escenario --------------------------------------
    @property
    def journal(self) -> list[WriteRecord]:
        return self.scenario.journal

    def journal_summary(self) -> list[str]:
        return self.scenario.journal_summary()

    def reset_journal(self) -> None:
        self.scenario.reset_journal()

    # --- ciclo de vida ---------------------------------------------------
    def start(self) -> FakeNotasParciales:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()

    def __enter__(self) -> FakeNotasParciales:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
