"""
Prueba local de la toma desde el PC con tinytuya (sin pasar por la nube).

Lee ID, IP y Local Key de devices.json (que Git ignora), asi la llave
nunca queda escrita en un archivo versionado ni se imprime en pantalla.

Uso (desde docs/Componentes, con el .venv activado):
    python test_local.py          -> lee el estado de la toma
    python test_local.py on       -> enciende
    python test_local.py off      -> apaga
"""

import json
import sys

import tinytuya

with open("devices.json", encoding="utf-8") as f:
    info = json.load(f)[0]

ip = info.get("ip")
if not ip:
    sys.exit("devices.json no trae la IP. Corre el wizard otra vez (y responde Y al escaneo local).")

version = float(info.get("version") or 3.4)

print(f"Dispositivo: {info.get('name')}  IP: {ip}  Version: {version}")

dev = tinytuya.OutletDevice(info["id"], ip, info["key"])
dev.set_version(version)
dev.set_socketTimeout(5)

accion = sys.argv[1].lower() if len(sys.argv) > 1 else "status"

if accion == "on":
    resultado = dev.turn_on()
elif accion == "off":
    resultado = dev.turn_off()
else:
    resultado = dev.status()

print("Respuesta:", resultado)

if isinstance(resultado, dict) and "Error" in resultado:
    print()
    print("La toma no respondio bien. Pistas:")
    print(" - Cierra la app Tuya a la fuerza y apaga el Bluetooth del celular")
    print("   (la toma solo admite una conexion local a la vez).")
    print(" - Revisa que el PC y la toma esten en el mismo hotspot y que la IP")
    print("   siga siendo la misma (python -m tinytuya scan).")
    print(" - Si el error habla de la llave (914): repite el wizard.")
