"""
Límite de intentos fallidos de POST /auth/login (docs/publicacion_web.md).

Como máximo MAX_FALLOS fallos en VENTANA_S segundos, contados por separado por IP del
cliente y por correo. Pasado el límite, /auth/login responde 429 con un mensaje que no
dice si el correo existe. Un inicio correcto borra el contador de ese correo.

Vive en memoria del proceso: se pierde al reiniciar el backend (que también sirve para
desbloquear una cuenta) y no se comparte entre procesos, lo que basta con un solo
uvicorn detrás del proxy de Vite.

IP del cliente: si la petición viene de 127.0.0.1 (el proxy de Vite, detrás de Funnel)
se toma la entrada de X-Forwarded-For más a la derecha que no sea de loopback; lo que
el cliente haya escrito en esa cabecera queda más a la izquierda y no se usa. Si no hay
ninguna, no se sabe la IP real y solo se aplica el límite por correo.
"""

import ipaddress
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import HTTPException, Request, status

MAX_FALLOS = 5
VENTANA_S = 15 * 60

MENSAJE_BLOQUEO = "Demasiados intentos fallidos. Espera unos minutos e inténtalo de nuevo."

# Hijo de "uvicorn.error": uvicorn ya lo muestra en consola a nivel INFO.
logger = logging.getLogger("uvicorn.error.limite_login")

_candado = threading.Lock()
_fallos: dict[str, deque] = defaultdict(deque)

# Reemplazable en las pruebas.
reloj = time.monotonic


def _es_loopback(valor: str) -> bool:
    try:
        return ipaddress.ip_address(valor.strip()).is_loopback
    except ValueError:
        return False


def _es_ip(valor: str) -> bool:
    try:
        ipaddress.ip_address(valor.strip())
        return True
    except ValueError:
        return False


def ip_cliente(request: Request) -> Optional[str]:
    """IP real del cliente, o None si la petición llega del proxy sin una IP real."""
    directa = request.client.host if request.client else None
    if directa is None or not _es_loopback(directa):
        return directa
    for entrada in reversed(request.headers.get("x-forwarded-for", "").split(",")):
        entrada = entrada.strip()
        if _es_ip(entrada) and not _es_loopback(entrada):
            return entrada
    return None


def _claves(ip: Optional[str], correo: str) -> list[str]:
    claves = [f"correo:{correo.strip().lower()}"]
    if ip is not None:
        claves.append(f"ip:{ip}")
    return claves


def _purgar(cola: deque, ahora: float) -> None:
    while cola and ahora - cola[0] >= VENTANA_S:
        cola.popleft()


def comprobar(ip: Optional[str], correo: str) -> None:
    """429 si la IP o el correo ya llegaron al límite de fallos."""
    ahora = reloj()
    with _candado:
        for clave in _claves(ip, correo):
            cola = _fallos.get(clave)
            if cola is None:
                continue
            _purgar(cola, ahora)
            if not cola:
                del _fallos[clave]
            elif len(cola) >= MAX_FALLOS:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=MENSAJE_BLOQUEO)


def registrar_fallo(ip: Optional[str], correo: str) -> None:
    ahora = reloj()
    logger.info("Inicio de sesión fallido desde %s", ip if ip is not None else "sin IP real")
    with _candado:
        for clave in _claves(ip, correo):
            cola = _fallos[clave]
            _purgar(cola, ahora)
            cola.append(ahora)


def registrar_exito(correo: str) -> None:
    with _candado:
        _fallos.pop(f"correo:{correo.strip().lower()}", None)


def reiniciar() -> None:
    """Borra todos los contadores (pruebas)."""
    with _candado:
        _fallos.clear()
