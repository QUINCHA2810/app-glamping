"""
Errores 422 de validación en español. Es el único lugar donde se traducen.

Pydantic genera sus mensajes en inglés. Aquí se reemplazan según el tipo de error, con
el formato "Campo: qué pasa". La respuesta conserva `type` y `loc`, pero ya no trae
`input` ni `ctx`: repetir lo recibido devolvería contraseñas, y un NaN ni siquiera se
puede convertir a JSON.

Los mensajes propios de los validadores (`raise ValueError("...")`) ya están en
español: solo se les quita el prefijo "Value error, " de Pydantic. Un tipo de error que
no esté aquí recibe un mensaje genérico, nunca el texto en inglés.
"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Nombres legibles de los campos; los demás salen de su nombre técnico.
NOMBRES_CAMPOS = {
    "correo": "Correo",
    "contrasena": "Contraseña",
    "contrasena_actual": "Contraseña actual",
    "contrasena_nueva": "Contraseña nueva",
    "contrasena_temporal": "Contraseña temporal",
    "id_fisico": "Identificador físico",
    "umbral_apagado_soc": "Umbral de apagado",
    "umbral_reactivacion_soc": "Umbral de reactivación",
    "umbral_soc_bajo": "Umbral global de apagado",
    "umbral_soc_recuperacion": "Umbral global de reactivación",
    "estado_carga_soc": "Estado de carga",
    "capacidad_wh": "Capacidad",
    "perfil_control": "Perfil de control",
    "ubicacion": "Ubicación",
    "categoria": "Categoría",
    "temperatura_color": "Temperatura de color",
    "modo_luz": "Modo de luz",
    "telefono": "Teléfono",
    "domo_id": "Domo",
    "desde": "Fecha inicial",
    "hasta": "Fecha final",
}

SECCIONES = {"body", "query", "path", "header", "cookie"}


def _nombre_campo(loc) -> str | None:
    partes = [p for p in loc if p not in SECCIONES and not isinstance(p, int)]
    # Un validador con unión u opcional agrega el nombre del tipo al final: no es el campo.
    partes = [p for p in partes if p[:1].islower() or p in NOMBRES_CAMPOS]
    if not partes:
        return None
    campo = str(partes[-1])
    return NOMBRES_CAMPOS.get(campo, campo.replace("_", " ").capitalize())


def _numero(valor) -> str:
    return f"{valor:g}" if isinstance(valor, (int, float)) else str(valor)


def _mensaje(error: dict) -> str:
    tipo = error.get("type", "")
    ctx = error.get("ctx") or {}

    if tipo == "value_error":
        texto = error.get("msg", "").removeprefix("Value error, ")
        # El validador de correos es el único value_error que viene en inglés.
        return "No es un correo válido." if texto.startswith("value is not a valid email") else texto
    if tipo == "missing":
        return "Es obligatorio."
    if tipo == "extra_forbidden":
        return "Este campo no se admite."
    if tipo == "json_invalid":
        return "El contenido enviado no es JSON válido."
    # Un null donde se esperaba otra cosa llega como error de tipo.
    if "input" in error and error["input"] is None:
        return "No puede quedar vacío."
    if tipo == "string_too_short":
        return f"Debe tener al menos {ctx.get('min_length')} caracteres."
    if tipo == "string_too_long":
        return f"No puede tener más de {ctx.get('max_length')} caracteres."
    if tipo == "greater_than_equal":
        return f"Debe ser mayor o igual que {_numero(ctx.get('ge'))}."
    if tipo == "greater_than":
        return f"Debe ser mayor que {_numero(ctx.get('gt'))}."
    if tipo == "less_than_equal":
        return f"Debe ser menor o igual que {_numero(ctx.get('le'))}."
    if tipo == "less_than":
        return f"Debe ser menor que {_numero(ctx.get('lt'))}."
    if tipo == "finite_number":
        return "Debe ser un número válido."
    if tipo.startswith(("float_", "int_", "decimal_")) or tipo == "int_from_float":
        return "Debe ser un número."
    if tipo.startswith("bool_"):
        return "Debe ser verdadero o falso."
    if tipo.startswith(("date_", "datetime_", "time_")):
        return "No es una fecha válida."
    if tipo == "literal_error":
        return "No es una opción válida."
    if tipo.startswith("string_"):
        return "Debe ser texto."
    if tipo in ("dict_type", "model_attributes_type", "model_type", "list_type"):
        return "Tiene un formato que no es válido."
    return "El dato no es válido."


def traducir_errores(errores) -> list[dict]:
    salida = []
    for error in errores:
        loc = list(error.get("loc", ()))
        campo = _nombre_campo(loc)
        mensaje = _mensaje(error)
        salida.append({"type": error.get("type"), "loc": loc, "msg": f"{campo}: {mensaje}" if campo else mensaje})
    return salida


async def manejar_error_validacion(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": traducir_errores(exc.errors())})
