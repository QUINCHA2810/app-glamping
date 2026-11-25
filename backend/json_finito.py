"""
Rechaza con 422 cualquier cuerpo JSON con NaN, Infinity, -Infinity o un número
que se desborda como 1e999, antes de que llegue a un endpoint.

Python los acepta al leer JSON aunque el estándar no los permita, y con ellos
pasan dos cosas: si la validación los rechaza, FastAPI no puede convertir su
respuesta de error a JSON y termina en un 500; y si el campo es un float sin
restricciones, se guardarían en la base. Revisar el cuerpo aquí cubre todos los
endpoints. Los textos "NaN" e "Infinity" no son esto: los frena
schemas.EntradaApi.

El mensaje lleva el mismo formato que los errores de validación de FastAPI, que
es el que entiende el frontend (frontend/src/api/client.js).
"""

import json
import math

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

METODOS_CON_CUERPO = {"POST", "PUT", "PATCH", "DELETE"}


class _NumeroNoFinito(Exception):
    pass


def _rechazar_constante(_: str) -> float:
    raise _NumeroNoFinito


def _leer_decimal(texto: str) -> float:
    valor = float(texto)
    if not math.isfinite(valor):
        raise _NumeroNoFinito
    return valor


def tiene_numeros_no_finitos(cuerpo: bytes) -> bool:
    """El JSON mal formado devuelve False: ese error lo informa FastAPI."""
    try:
        json.loads(cuerpo, parse_constant=_rechazar_constante, parse_float=_leer_decimal)
    except _NumeroNoFinito:
        return True
    except (ValueError, RecursionError):
        return False
    return False


def _es_json(scope: Scope) -> bool:
    """FastAPI también lee como JSON un cuerpo sin Content-Type."""
    for nombre, valor in scope["headers"]:
        if nombre == b"content-type":
            tipo = valor.split(b";")[0].strip().lower()
            return tipo == b"application/json" or tipo.endswith(b"+json")
    return True


class RechazarNumerosNoFinitos:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in METODOS_CON_CUERPO or not _es_json(scope):
            await self.app(scope, receive, send)
            return

        mensajes: list[Message] = []
        while True:
            mensaje = await receive()
            mensajes.append(mensaje)
            if mensaje["type"] != "http.request" or not mensaje.get("more_body", False):
                break

        cuerpo = b"".join(m.get("body", b"") for m in mensajes if m["type"] == "http.request")
        if tiene_numeros_no_finitos(cuerpo):
            respuesta = JSONResponse(
                status_code=422,
                content={
                    "detail": [
                        {
                            "type": "finite_number",
                            "loc": ["body"],
                            "msg": "El cuerpo no admite NaN, Infinity ni números fuera de rango.",
                        }
                    ]
                },
            )
            await respuesta(scope, receive, send)
            return

        async def repetir() -> Message:
            return mensajes.pop(0) if mensajes else await receive()

        await self.app(scope, repetir, send)
