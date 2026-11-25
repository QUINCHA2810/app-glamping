#pragma once
// ============================================================================
//  Protocolo local de Tuya (puerto TCP 6668), versiones 3.3, 3.4 y 3.5.
//
//  Una sola responsabilidad: armar y abrir tramas, y negociar la clave de
//  sesión. No sabe nada de sockets, WiFi, MQTT ni Arduino: recibe bytes y
//  devuelve bytes. La conexión está en TuyaLocal.ino; el cifrado (AES, GCM,
//  HMAC-SHA256) en tuya_cripto.h, implementado con mbedtls en el ESP32.
//
//  Referencia: tinytuya (Jason Cox y colaboradores, licencia MIT),
//  https://github.com/jasonacox/tinytuya, versión 1.20.0:
//    - core/header.py           prefijos, sufijos, encabezado "3.x" + 12 ceros,
//                               comandos que van sin ese encabezado
//    - core/command_types.py    números de comando
//    - core/message_helper.py   pack_message / unpack_message / parse_header
//    - core/XenonDevice.py      _encode_message, _decode_payload,
//                               _negotiate_session_key_generate_step_1/_3/finalize,
//                               payload_dict (JSON de cada comando por versión)
//  Los vectores de prueba (tuya_vectores.h) salen de esas mismas funciones de
//  tinytuya, con claves y nonces ficticios: ver firmware/pruebas_tuya/.
//
//  Formatos (enteros de 32 bits, big-endian):
//
//   55AA (3.3 y 3.4)
//     000055aa SSSSSSSS CCCCCCCC LLLLLLLL [RRRRRRRR] datos... suma 0000aa55
//     L cuenta desde el código de retorno R (solo en lo que manda el
//     dispositivo) hasta el sufijo incluido. suma: CRC32 (4 bytes) en 3.3,
//     HMAC-SHA256 con la clave de sesión (32 bytes) en 3.4, sobre todo lo
//     anterior a ella. datos: AES-128-ECB con relleno PKCS#7.
//       3.3: clave = local key. Los comandos con encabezado lo llevan EN CLARO
//            delante de lo cifrado: "3.3" + 12 ceros + cifrado(JSON).
//       3.4: clave = la de sesión. El encabezado va DENTRO de lo cifrado:
//            cifrado("3.4" + 12 ceros + JSON).
//
//   6699 (3.5)
//     00006699 UUUU SSSSSSSS CCCCCCCC LLLLLLLL IV(12) cifrado... tag(16) 00009966
//     AES-128-GCM con la clave de sesión. Datos autenticados: los 14 bytes que
//     siguen al prefijo (UUUU..LLLL). L cuenta del IV al tag. Lo que manda el
//     dispositivo trae, dentro de lo cifrado, 4 bytes de código de retorno.
//
//   Negociación de la clave de sesión (3.4 y 3.5), con la local key:
//     -> 3 SESS_KEY_NEG_START   nonce local (16 bytes)
//     <- 4 SESS_KEY_NEG_RESP    nonce remoto (16) + HMAC-SHA256(local key, nonce local) (32)
//     -> 5 SESS_KEY_NEG_FINISH  HMAC-SHA256(local key, nonce remoto)
//     clave de sesión = AES(local key, nonce local XOR nonce remoto):
//       3.4: AES-128-ECB del bloque.
//       3.5: AES-128-GCM con IV = los 12 primeros bytes del nonce local; se
//            toman los 16 bytes cifrados (sin el tag).
// ============================================================================

#include <stddef.h>
#include <stdint.h>

namespace tuya {

// Comandos (tinytuya core/command_types.py)
enum : uint32_t {
    CMD_NEG_INICIO     = 0x03,   // SESS_KEY_NEG_START
    CMD_NEG_RESPUESTA  = 0x04,   // SESS_KEY_NEG_RESP
    CMD_NEG_FIN        = 0x05,   // SESS_KEY_NEG_FINISH
    CMD_CONTROL        = 0x07,   // CONTROL (3.3)
    CMD_ESTADO         = 0x08,   // STATUS: el dispositivo informa sus DPS
    CMD_LATIDO         = 0x09,   // HEART_BEAT
    CMD_CONSULTA       = 0x0a,   // DP_QUERY (3.3)
    CMD_CONTROL_NUEVO  = 0x0d,   // CONTROL_NEW (3.4 y 3.5)
    CMD_CONSULTA_NUEVA = 0x10,   // DP_QUERY_NEW (3.4 y 3.5)
};

const size_t LARGO_CLAVE     = 16;
const size_t LARGO_NONCE     = 16;
const size_t LARGO_IV        = 12;
const size_t MAX_TRAMA       = 1440;   // tinytuya const.py MAX_PAYLOAD_LENGTH: más es basura

struct Sesion {
    uint8_t  version;                 // 33, 34 o 35
    uint8_t  claveLocal[LARGO_CLAVE]; // la local key, 16 bytes ASCII tal cual
    uint8_t  clave[LARGO_CLAVE];      // la que se usa ahora: la local antes de negociar, la de sesión después
    uint8_t  nonceLocal[LARGO_NONCE];
    uint32_t secuencia;               // la de la próxima trama que se arma
    bool     negociada;               // 3.3 no negocia: siempre true
};

// Lo que trae una trama abierta. `datos` apunta al buffer `claro` que se pasó a
// abrirTrama: el JSON sin el encabezado "3.x" + 12 ceros ni el código de retorno.
struct Mensaje {
    uint32_t       secuencia;
    uint32_t       comando;
    uint32_t       retorno;           // 0 = bien
    const uint8_t* datos;
    size_t         largo;
};

// Prepara la sesión de una conexión nueva. version: 33, 34 o 35. false si no es ninguna.
bool iniciarSesion(Sesion& s, uint8_t version, const uint8_t claveLocal[LARGO_CLAVE]);

// Arma una trama del cliente con `datos` (el JSON) y avanza la secuencia.
// iv: 12 bytes para el GCM de 3.5 (aleatorios; se ignora en 3.3 y 3.4).
// Devuelve el largo escrito en `salida`, o 0 si no cabe o el cifrado falla.
size_t armarTrama(Sesion& s, uint32_t comando, const uint8_t* datos, size_t largo,
                  const uint8_t iv[LARGO_IV], uint8_t* salida, size_t tam);

// Cuántos bytes ocupa la trama que empieza en `buf`:
//   > 0  su largo total (puede ser mayor que n: faltan bytes por leer)
//     0  todavía no hay encabezado completo
//    -1  `buf` no empieza con un prefijo válido o el largo es absurdo:
//        descartar un byte y volver a mirar
long largoTrama(const uint8_t* buf, size_t n);

// Verifica (CRC32, HMAC o tag GCM) y descifra la trama completa `trama` de
// `largo` bytes que mandó el dispositivo. false si la suma o el tag no
// coinciden, si el formato no corresponde a la versión de la sesión o si
// no cabe en `claro`.
bool abrirTrama(const Sesion& s, const uint8_t* trama, size_t largo, Mensaje& m,
                uint8_t* claro, size_t tamClaro);

// Negociación (3.4 y 3.5). Paso 1: arma SESS_KEY_NEG_START con `nonce`.
size_t armarInicioNegociacion(Sesion& s, const uint8_t nonce[LARGO_NONCE],
                              const uint8_t iv[LARGO_IV], uint8_t* salida, size_t tam);

// Paso 2: con la respuesta ya abierta (comando 4), comprueba que el dispositivo
// conoce la local key (HMAC del nonce local), arma SESS_KEY_NEG_FINISH y
// cambia la clave de la sesión a la de sesión. 0 si la respuesta no sirve.
size_t armarFinNegociacion(Sesion& s, const Mensaje& respuesta,
                           const uint8_t iv[LARGO_IV], uint8_t* salida, size_t tam);

// JSON de cada orden, exactamente como lo arma tinytuya (payload_dict, sin espacios).
//   dps: el contenido de "dps":{...} sin las llaves, p. ej. "\"1\":true".
//   t:   hora Unix.
//   Control:  3.3 {"devId":ID,"uid":ID,"t":"T","dps":{..}}
//             3.4/3.5 {"protocol":5,"t":T,"data":{"dps":{..}}}
//   Consulta: 3.3 {"gwId":ID,"devId":ID,"uid":ID,"t":"T"}
//             3.4/3.5 {}
// Devuelven el largo escrito (sin el '\0'), o 0 si no cabe.
size_t jsonControl(uint8_t version, const char* idTuya, uint32_t t, const char* dps, char* salida, size_t tam);
size_t jsonConsulta(uint8_t version, const char* idTuya, uint32_t t, char* salida, size_t tam);
// Algunos 3.3 ("device22") contestan "data unvalid" a la consulta normal. A esos
// se les pregunta con CMD_CONTROL_NUEVO y jsonControl(33, ..., "\"1\":null"): el
// mismo JSON del control 3.3 con los DPS en null (tinytuya payload_dict "device22").

// Comando con que se manda una orden o una consulta en esa versión.
uint32_t comandoControl(uint8_t version);
uint32_t comandoConsulta(uint8_t version);

uint32_t crc32(const uint8_t* datos, size_t n);

}  // namespace tuya
