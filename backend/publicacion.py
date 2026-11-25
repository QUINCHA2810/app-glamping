"""
Ajustes para publicar la app en Internet (Tailscale Funnel, docs/publicacion_web.md).

- API_DOCS: "true" por defecto (desarrollo). Con "false" la app está en modo público:
  sin /docs, /redoc ni /openapi.json, y el secreto JWT corto impide arrancar
  (security.validar_secreto).
- CabecerasSeguridad: cabeceras que el navegador debe recibir en cada respuesta del
  backend. HSTS no va aquí: lo pone Funnel, que es quien sirve el HTTPS.
"""

import os

from starlette.middleware.base import BaseHTTPMiddleware


def docs_habilitadas() -> bool:
    """False solo si API_DOCS vale false/0/no/off (sin importar mayúsculas)."""
    return os.getenv("API_DOCS", "true").strip().lower() not in {"false", "0", "no", "off"}


def opciones_documentacion() -> dict:
    """Argumentos de FastAPI() para la documentación: todo en None si está cerrada."""
    if docs_habilitadas():
        return {}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


CABECERAS_SEGURIDAD = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}


class CabecerasSeguridad(BaseHTTPMiddleware):
    """Agrega CABECERAS_SEGURIDAD sin pisar las que ya traiga la respuesta."""

    async def dispatch(self, request, call_next):
        respuesta = await call_next(request)
        for nombre, valor in CABECERAS_SEGURIDAD.items():
            respuesta.headers.setdefault(nombre, valor)
        return respuesta
