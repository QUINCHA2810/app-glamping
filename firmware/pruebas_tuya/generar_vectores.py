"""Genera firmware/domo_nodo/tuya_vectores.h con tinytuya y datos FICTICIOS.

Los vectores salen de las funciones de tinytuya (MIT, https://github.com/jasonacox/tinytuya),
no de una reimplementación: _encode_message, generate_payload y los pasos de
_negotiate_session_key del lado del cliente; pack_message para las tramas que
mandaría el dispositivo. Después se comprueba que tinytuya las vuelva a abrir.

Nada aquí toca la red: el "dispositivo" apunta a 127.0.0.1, nunca se conecta, y
socket.socket queda bloqueado mientras corre. Las claves, nonces, IV, ID y hora
son inventados; las local keys reales no se usan ni se leen.

Uso (desde la raíz del repo, con el venv del backend, que trae tinytuya):
    backend/.venv/Scripts/python firmware/pruebas_tuya/generar_vectores.py
"""

import hashlib
import hmac
import json
import socket
import struct
import sys
from pathlib import Path
from unittest import mock

import tinytuya
from tinytuya.core import command_types as CT
from tinytuya.core import header as H
from tinytuya.core import message_helper as MH
from tinytuya.core.crypto_helper import AESCipher
from tinytuya.core.XenonDevice import XenonDevice

SALIDA = Path(__file__).resolve().parents[1] / "domo_nodo" / "tuya_vectores.h"

# ---- Datos ficticios (nunca los de un dispositivo real) -------------------------------
ID_TUYA = "vatiaficticio0000001"          # 20 caracteres, como un ID de Tuya
CLAVE = b"ClaveFicticia#01"               # 16 bytes, como una local key
OTRA_CLAVE = b"OtraClaveFalsa02"          # la de un impostor: su HMAC no debe pasar
NONCE_LOCAL = b"nonceLocal-00001"
NONCE_REMOTO = b"nonceRemoto-0002"
T = 1767225600                            # 2026-01-01 00:00:00 UTC
IV = [b"ivFicticio-%d" % i for i in range(1, 5)]   # 12 bytes: inicio, fin, control, consulta
IV_DISPOSITIVO = [b"ivDisposit-%d" % i for i in range(1, 5)]
SEC_DISPOSITIVO = 0x2A

DPS_CONTROL = {33: {"1": True, "2": False}, 34: {"1": False}, 35: {"21": "white", "22": 505}}
ESTADO = {
    33: {"devId": ID_TUYA, "dps": {"1": True, "2": False, "3": True}, "t": T},
    34: {"protocol": 4, "t": T, "data": {"dps": {"1": False}}},
    35: {"protocol": 4, "t": T, "data": {"dps": {"20": True, "21": "white", "22": 505}}},
}


def sin_espacios(obj) -> bytes:
    return json.dumps(obj).replace(" ", "").encode()


def trama_dispositivo(version, clave, comando, datos, iv, retorno=0):
    """Una trama como la manda el dispositivo: con código de retorno."""
    if version == 35:
        msg = MH.TuyaMessage(SEC_DISPOSITIVO, comando, retorno, datos, 0, True, H.PREFIX_6699_VALUE, iv)
        return MH.pack_message(msg, hmac_key=clave)
    cifrado = AESCipher(clave).encrypt(datos, False) if datos else b""
    if version == 33 and datos and comando not in H.NO_PROTOCOL_HEADER_CMDS:
        cifrado = H.PROTOCOL_33_HEADER + cifrado
    payload = struct.pack(">I", retorno) + cifrado
    msg = MH.TuyaMessage(SEC_DISPOSITIVO, comando, None, payload, 0, True, H.PREFIX_55AA_VALUE, None)
    return MH.pack_message(msg, hmac_key=clave if version == 34 else None)


def vector(version):
    aleatorios = []
    v = {"version": version}
    with mock.patch("os.urandom", side_effect=lambda n: aleatorios.pop(0)[:n]), \
         mock.patch("time.time", return_value=T + 0.25):
        d = XenonDevice(ID_TUYA, "127.0.0.1", CLAVE.decode(), version=version / 10)
        v["clave_sesion"] = CLAVE
        if version >= 34:
            aleatorios[:] = [NONCE_LOCAL] + ([IV[0]] if version == 35 else [])
            v["inicio"] = d._encode_message(d._negotiate_session_key_generate_step_1())
            assert d.local_nonce == NONCE_LOCAL

            respuesta = NONCE_REMOTO + hmac.new(CLAVE, NONCE_LOCAL, hashlib.sha256).digest()
            impostor = NONCE_REMOTO + hmac.new(OTRA_CLAVE, NONCE_LOCAL, hashlib.sha256).digest()
            if version == 34:
                # 3.4: el dispositivo cifra la respuesta con la local key (ECB), sin encabezado.
                respuesta = AESCipher(CLAVE).encrypt(respuesta, False)
                impostor = AESCipher(CLAVE).encrypt(impostor, False)
                # Ya va cifrado: la trama se arma a mano (código de retorno + datos).
                v["respuesta"] = MH.pack_message(MH.TuyaMessage(SEC_DISPOSITIVO, CT.SESS_KEY_NEG_RESP, None, struct.pack(">I", 0) + respuesta, 0, True, H.PREFIX_55AA_VALUE, None), hmac_key=CLAVE)
                v["respuesta_mala"] = MH.pack_message(MH.TuyaMessage(SEC_DISPOSITIVO, CT.SESS_KEY_NEG_RESP, None, struct.pack(">I", 0) + impostor, 0, True, H.PREFIX_55AA_VALUE, None), hmac_key=CLAVE)
            else:
                v["respuesta"] = trama_dispositivo(35, CLAVE, CT.SESS_KEY_NEG_RESP, respuesta, IV_DISPOSITIVO[0])
                v["respuesta_mala"] = trama_dispositivo(35, CLAVE, CT.SESS_KEY_NEG_RESP, impostor, IV_DISPOSITIVO[0])

            paso3 = d._negotiate_session_key_generate_step_3(MH.unpack_message(v["respuesta"], hmac_key=CLAVE))
            assert paso3, "tinytuya rechazó la respuesta de negociación"
            aleatorios[:] = [IV[1]] if version == 35 else []
            v["fin"] = d._encode_message(paso3)
            d._negotiate_session_key_generate_finalize()
            v["clave_sesion"] = d.local_key
            # El impostor no pasa la comprobación de tinytuya.
            d2 = XenonDevice(ID_TUYA, "127.0.0.1", CLAVE.decode(), version=version / 10)
            d2.local_nonce = NONCE_LOCAL
            assert not d2._negotiate_session_key_generate_step_3(MH.unpack_message(v["respuesta_mala"], hmac_key=CLAVE))

        aleatorios[:] = [IV[2]] if version == 35 else []
        v["dps"] = sin_espacios(DPS_CONTROL[version])[1:-1].decode()
        v["control"] = d._encode_message(d.generate_payload(CT.CONTROL, DPS_CONTROL[version]))
        aleatorios[:] = [IV[3]] if version == 35 else []
        v["consulta"] = d._encode_message(d.generate_payload(CT.DP_QUERY))

        clave = v["clave_sesion"]
        comando_acuse = CT.CONTROL if version == 33 else CT.CONTROL_NEW
        v["acuse"] = trama_dispositivo(version, clave, comando_acuse, b"", IV_DISPOSITIVO[1])
        texto = sin_espacios(ESTADO[version])
        datos = texto if version == 33 else (b"3.%d" % (version % 10)) + H.PROTOCOL_3x_HEADER + texto
        v["estado"] = trama_dispositivo(version, clave, CT.STATUS, datos, IV_DISPOSITIVO[2])
        v["json_estado"] = texto.decode()

        # tinytuya abre lo que mandaría el dispositivo y obtiene el mismo JSON.
        clave_hmac = clave if version >= 34 else None
        msg = MH.unpack_message(v["estado"], hmac_key=clave_hmac)
        assert msg.crc_good and msg.cmd == CT.STATUS
        assert d._decode_payload(msg.payload) == ESTADO[version] | ({"dps": ESTADO[version]["data"]["dps"]} if version >= 34 else {})
        acuse = MH.unpack_message(v["acuse"], hmac_key=clave_hmac)
        assert acuse.crc_good and acuse.payload == b"" and acuse.retcode == 0
    assert not aleatorios
    return v


def c_bytes(nombre, datos):
    filas = [", ".join(f"0x{b:02x}" for b in datos[i:i + 16]) for i in range(0, len(datos), 16)]
    return f"static const uint8_t {nombre}[{len(datos)}] = {{\n    " + ",\n    ".join(filas) + "\n};\n"


def c_texto(texto):
    return json.dumps(texto)   # comillas y escapes válidos también en C para ASCII


def generar() -> str:
    """El contenido de tuya_vectores.h (las pruebas del backend lo comparan con el archivo)."""
    def sin_red(*_a, **_k):
        raise RuntimeError("generar_vectores.py no usa la red")

    with mock.patch.object(socket, "socket", sin_red):
        vectores = [vector(v) for v in (33, 34, 35)]

    partes = [
        "#pragma once\n",
        "// GENERADO por firmware/pruebas_tuya/generar_vectores.py con tinytuya %s. No editar a mano.\n" % tinytuya.__version__,
        "// Claves, nonces, IV, ID y hora FICTICIOS: no son de ningún dispositivo real.\n",
        "// Lo usa tuya_autotest.cpp (comando \"autotest\" del monitor serie y pruebas en el PC).\n\n",
        "#include <stddef.h>\n#include <stdint.h>\n\n",
        "namespace tuya_vectores {\n\n",
        f"static const char ID_TUYA[] = {c_texto(ID_TUYA)};\n",
        f"static const uint32_t T = {T}u;\n",
        c_bytes("CLAVE", CLAVE),
        c_bytes("NONCE_LOCAL", NONCE_LOCAL),
        c_bytes("IV", b"".join(IV)),
        "\n",
    ]
    campos = ("inicio", "respuesta", "respuesta_mala", "fin", "control", "consulta", "acuse", "estado")
    for v in vectores:
        n = v["version"]
        for campo in campos:
            if campo in v:
                partes.append(c_bytes(f"V{n}_{campo.upper()}", v[campo]))
        partes.append(c_bytes(f"V{n}_CLAVE_SESION", v["clave_sesion"]))
        partes.append("\n")

    partes.append(
        "struct Vector {\n"
        "    uint8_t version;\n"
        "    const uint8_t* inicio;         size_t nInicio;         // 3.3: sin negociación (nullptr)\n"
        "    const uint8_t* respuesta;      size_t nRespuesta;\n"
        "    const uint8_t* respuestaMala;  size_t nRespuestaMala;  // HMAC con otra clave: se rechaza\n"
        "    const uint8_t* fin;            size_t nFin;\n"
        "    const uint8_t* claveSesion;\n"
        "    const char*    dps;                                    // lo que va dentro de \"dps\":{...}\n"
        "    const uint8_t* control;        size_t nControl;\n"
        "    const uint8_t* consulta;       size_t nConsulta;\n"
        "    const uint8_t* acuse;          size_t nAcuse;          // respuesta vacía a la orden\n"
        "    const uint8_t* estado;         size_t nEstado;         // STATUS (0x08) del dispositivo\n"
        "    const char*    jsonEstado;\n"
        "};\n\n"
        "static const Vector VECTORES[] = {\n"
    )
    for v in vectores:
        n = v["version"]

        def ref(campo):
            return f"V{n}_{campo.upper()}, sizeof(V{n}_{campo.upper()})" if campo in v else "nullptr, 0"

        partes.append(
            f"    {{ {n}, {ref('inicio')}, {ref('respuesta')}, {ref('respuesta_mala')}, {ref('fin')}, V{n}_CLAVE_SESION,\n"
            f"      {c_texto(v['dps'])}, {ref('control')}, {ref('consulta')}, {ref('acuse')}, {ref('estado')},\n"
            f"      {c_texto(v['json_estado'])} }},\n"
        )
    partes.append("};\n\n}  // namespace tuya_vectores\n")
    return "".join(partes)


def main():
    SALIDA.write_text(generar(), encoding="utf-8", newline="\n")
    print(f"Escrito {SALIDA}")


if __name__ == "__main__":
    sys.exit(main())
