// Protocolo local de Tuya 3.3 / 3.4 / 3.5. Ver tuya_protocolo.h (formatos y
// referencias a tinytuya, MIT). Sin Arduino: se compila igual en el PC para
// las pruebas de firmware/pruebas_tuya/.
#include "tuya_protocolo.h"
#include "tuya_cripto.h"

#include <stdio.h>
#include <string.h>

namespace tuya {

namespace {

const uint8_t PREFIJO_55AA[4] = { 0x00, 0x00, 0x55, 0xAA };
const uint8_t SUFIJO_55AA[4]  = { 0x00, 0x00, 0xAA, 0x55 };
const uint8_t PREFIJO_6699[4] = { 0x00, 0x00, 0x66, 0x99 };
const uint8_t SUFIJO_6699[4]  = { 0x00, 0x00, 0x99, 0x66 };
const size_t  ENCABEZADO_VERSION = 15;   // "3.x" + 12 ceros (header.py PROTOCOL_3x_HEADER)
const size_t  LARGO_TAG = 16;
const size_t  LARGO_HMAC = 32;

void poner32(uint8_t* p, uint32_t v) {
    p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16); p[2] = (uint8_t)(v >> 8); p[3] = (uint8_t)v;
}

uint32_t leer32(const uint8_t* p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

// header.py NO_PROTOCOL_HEADER_CMDS: estos van sin "3.x" + 12 ceros.
bool sinEncabezado(uint32_t c) {
    return c == CMD_CONSULTA || c == CMD_CONSULTA_NUEVA || c == 0x12 /* UPDATEDPS */ || c == CMD_LATIDO ||
           c == CMD_NEG_INICIO || c == CMD_NEG_RESPUESTA || c == CMD_NEG_FIN || c == 0x40 /* LAN_EXT_STREAM */;
}

bool esNegociacion(uint32_t c) {
    return c == CMD_NEG_INICIO || c == CMD_NEG_RESPUESTA || c == CMD_NEG_FIN;
}

void escribirEncabezadoVersion(uint8_t version, uint8_t* p) {
    p[0] = '3'; p[1] = '.'; p[2] = (uint8_t)('0' + version % 10);
    memset(p + 3, 0, ENCABEZADO_VERSION - 3);
}

bool empiezaConVersion(uint8_t version, const uint8_t* p, size_t n) {
    return n >= ENCABEZADO_VERSION && p[0] == '3' && p[1] == '.' && p[2] == (uint8_t)('0' + version % 10);
}

// Comparación sin salida temprana: no revela cuántos bytes coinciden.
bool igualesSeguro(const uint8_t* a, const uint8_t* b, size_t n) {
    uint8_t d = 0;
    for (size_t i = 0; i < n; i++) d |= a[i] ^ b[i];
    return d == 0;
}

// Relleno PKCS#7 en `p` (que tiene n bytes y espacio para 16 más). Devuelve el largo nuevo.
size_t rellenar(uint8_t* p, size_t n) {
    uint8_t r = (uint8_t)(16 - n % 16);
    memset(p + n, r, r);
    return n + r;
}

// Quita el relleno como tinytuya (_unpad sin verify_padding: solo mira el último byte).
bool quitarRelleno(const uint8_t* p, size_t* n) {
    if (*n == 0) return false;
    uint8_t r = p[*n - 1];
    if (r < 1 || r > 16 || r > *n) return false;
    *n -= r;
    return true;
}

}  // namespace

//-----------------------------------------------------------------------------
uint32_t crc32(const uint8_t* datos, size_t n) {
    // El mismo CRC-32 que binascii.crc32 / zlib (polinomio 0xEDB88320), bit a bit:
    // las tramas son cortas y así no ocupa 1 KB de tabla.
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) {
        c ^= datos[i];
        for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u)));
    }
    return ~c;
}

bool iniciarSesion(Sesion& s, uint8_t version, const uint8_t claveLocal[LARGO_CLAVE]) {
    if (version != 33 && version != 34 && version != 35) return false;
    memset(&s, 0, sizeof(s));
    s.version = version;
    memcpy(s.claveLocal, claveLocal, LARGO_CLAVE);
    memcpy(s.clave, claveLocal, LARGO_CLAVE);
    s.secuencia = 1;                      // tinytuya: seqno = 1 en cada dispositivo nuevo
    s.negociada = version == 33;
    return true;
}

uint32_t comandoControl(uint8_t version)  { return version == 33 ? CMD_CONTROL : CMD_CONTROL_NUEVO; }
uint32_t comandoConsulta(uint8_t version) { return version == 33 ? CMD_CONSULTA : CMD_CONSULTA_NUEVA; }

//-----------------------------------------------------------------------------
// message_helper.pack_message + XenonDevice._encode_message
size_t armarTrama(Sesion& s, uint32_t comando, const uint8_t* datos, size_t largo,
                  const uint8_t iv[LARGO_IV], uint8_t* salida, size_t tam) {
    bool encabezado = !sinEncabezado(comando);
    size_t plano = largo + (encabezado ? ENCABEZADO_VERSION : 0);

    if (s.version == 35) {
        // 6699: prefijo(4) 0000(2) secuencia(4) comando(4) largo(4) IV(12) cifrado tag(16) sufijo(4)
        size_t total = 18 + LARGO_IV + plano + LARGO_TAG + 4;
        if (total > tam || total > MAX_TRAMA) return 0;
        memcpy(salida, PREFIJO_6699, 4);
        salida[4] = 0; salida[5] = 0;
        poner32(salida + 6, s.secuencia);
        poner32(salida + 10, comando);
        poner32(salida + 14, (uint32_t)(LARGO_IV + plano + LARGO_TAG));
        memcpy(salida + 18, iv, LARGO_IV);
        uint8_t* p = salida + 18 + LARGO_IV;
        if (encabezado) escribirEncabezadoVersion(35, p);
        memcpy(p + (encabezado ? ENCABEZADO_VERSION : 0), datos, largo);
        if (!gcmCifrar(s.clave, iv, salida + 4, 14, p, plano, p, p + plano)) return 0;
        memcpy(p + plano + LARGO_TAG, SUFIJO_6699, 4);
        s.secuencia++;
        return total;
    }

    if (s.version != 33 && s.version != 34) return 0;
    // 55AA: prefijo(4) secuencia(4) comando(4) largo(4) payload suma(4 o 32) sufijo(4)
    size_t suma = s.version == 34 ? LARGO_HMAC : 4;
    size_t cifrado = (s.version == 34 ? plano : largo) / 16 * 16 + 16;      // con el relleno PKCS#7
    size_t payload = cifrado + (s.version == 33 && encabezado ? ENCABEZADO_VERSION : 0);
    size_t total = 16 + payload + suma + 4;
    if (total > tam || total > MAX_TRAMA) return 0;

    memcpy(salida, PREFIJO_55AA, 4);
    poner32(salida + 4, s.secuencia);
    poner32(salida + 8, comando);
    poner32(salida + 12, (uint32_t)(payload + suma + 4));
    uint8_t* p = salida + 16;
    if (s.version == 34) {
        // 3.4: el encabezado de versión se cifra junto con el JSON.
        if (encabezado) escribirEncabezadoVersion(34, p);
        memcpy(p + (encabezado ? ENCABEZADO_VERSION : 0), datos, largo);
        rellenar(p, plano);
        if (!aesEcb(true, s.clave, p, p, cifrado)) return 0;
    } else {
        // 3.3: se cifra el JSON y el encabezado va delante, en claro.
        if (encabezado) { escribirEncabezadoVersion(33, p); p += ENCABEZADO_VERSION; }
        memcpy(p, datos, largo);
        rellenar(p, largo);
        if (!aesEcb(true, s.clave, p, p, cifrado)) return 0;
    }

    uint8_t* fin = salida + 16 + payload;
    if (s.version == 34) {
        if (!hmacSha256(s.clave, LARGO_CLAVE, salida, 16 + payload, fin)) return 0;
    } else {
        poner32(fin, crc32(salida, 16 + payload));
    }
    memcpy(fin + suma, SUFIJO_55AA, 4);
    s.secuencia++;
    return total;
}

//-----------------------------------------------------------------------------
// message_helper.parse_header
long largoTrama(const uint8_t* buf, size_t n) {
    size_t mirar = n < 4 ? n : 4;
    bool es55 = memcmp(buf, PREFIJO_55AA, mirar) == 0;
    bool es66 = memcmp(buf, PREFIJO_6699, mirar) == 0;
    if (!es55 && !es66) return -1;
    if (n < 4) return 0;
    if (es55) {
        if (n < 16) return 0;
        uint32_t l = leer32(buf + 12);
        if (l < 8 || l > MAX_TRAMA) return -1;
        return (long)(16 + l);
    }
    if (n < 18) return 0;
    uint32_t l = leer32(buf + 14);
    if (l < LARGO_IV + LARGO_TAG || l > MAX_TRAMA) return -1;
    return (long)(18 + l + 4);
}

//-----------------------------------------------------------------------------
// message_helper.unpack_message (no_retcode=False) + XenonDevice._decode_payload
bool abrirTrama(const Sesion& s, const uint8_t* t, size_t largo, Mensaje& m, uint8_t* claro, size_t tamClaro) {
    long esperado = largoTrama(t, largo);
    if (esperado <= 0 || (size_t)esperado != largo) return false;
    memset(&m, 0, sizeof(m));
    m.datos = claro;

    if (t[2] == 0x66) {
        if (s.version != 35) return false;
        m.secuencia = leer32(t + 6);
        m.comando   = leer32(t + 10);
        size_t n = leer32(t + 14) - LARGO_IV - LARGO_TAG;
        if (n > tamClaro) return false;
        const uint8_t* iv = t + 18;
        const uint8_t* cifrado = iv + LARGO_IV;
        if (!gcmDescifrar(s.clave, iv, t + 4, 14, cifrado, n, claro, cifrado + n)) return false;
        // Lo que manda el dispositivo trae el código de retorno dentro de lo cifrado.
        const uint8_t* p = claro;
        if (n >= 4) { m.retorno = leer32(p); p += 4; n -= 4; } else n = 0;
        if (!esNegociacion(m.comando) && empiezaConVersion(35, p, n)) { p += ENCABEZADO_VERSION; n -= ENCABEZADO_VERSION; }
        m.datos = p;
        m.largo = n;
        return true;
    }

    if (s.version != 33 && s.version != 34) return false;
    size_t suma = s.version == 34 ? LARGO_HMAC : 4;
    if (largo < 16 + 4 + suma + 4) return false;              // encabezado, retorno, suma y sufijo
    size_t finDatos = largo - suma - 4;
    if (s.version == 34) {
        uint8_t h[LARGO_HMAC];
        if (!hmacSha256(s.clave, LARGO_CLAVE, t, finDatos, h) || !igualesSeguro(h, t + finDatos, LARGO_HMAC)) return false;
    } else if (crc32(t, finDatos) != leer32(t + finDatos)) {
        return false;
    }
    m.secuencia = leer32(t + 4);
    m.comando   = leer32(t + 8);
    m.retorno   = leer32(t + 16);
    const uint8_t* p = t + 20;
    size_t n = finDatos - 20;

    if (s.version == 33 && empiezaConVersion(33, p, n)) { p += ENCABEZADO_VERSION; n -= ENCABEZADO_VERSION; }
    if (n == 0) { m.largo = 0; return true; }                  // acuse sin datos
    if (s.version == 33 && p[0] == '{') {                      // algunos errores llegan sin cifrar
        if (n > tamClaro) return false;
        memcpy(claro, p, n);
        m.largo = n;
        return true;
    }
    if (n % 16 || n > tamClaro) return false;
    if (!aesEcb(false, s.clave, p, claro, n) || !quitarRelleno(claro, &n)) return false;
    p = claro;
    if (s.version == 34 && !esNegociacion(m.comando) && empiezaConVersion(34, p, n)) { p += ENCABEZADO_VERSION; n -= ENCABEZADO_VERSION; }
    m.datos = p;
    m.largo = n;
    return true;
}

//-----------------------------------------------------------------------------
// XenonDevice._negotiate_session_key_generate_step_1
size_t armarInicioNegociacion(Sesion& s, const uint8_t nonce[LARGO_NONCE], const uint8_t iv[LARGO_IV],
                              uint8_t* salida, size_t tam) {
    if (s.version != 34 && s.version != 35) return 0;
    memcpy(s.nonceLocal, nonce, LARGO_NONCE);
    memcpy(s.clave, s.claveLocal, LARGO_CLAVE);
    s.negociada = false;
    return armarTrama(s, CMD_NEG_INICIO, s.nonceLocal, LARGO_NONCE, iv, salida, tam);
}

// XenonDevice._negotiate_session_key_generate_step_3 + _finalize
size_t armarFinNegociacion(Sesion& s, const Mensaje& r, const uint8_t iv[LARGO_IV], uint8_t* salida, size_t tam) {
    if (s.version != 34 && s.version != 35) return 0;
    if (r.comando != CMD_NEG_RESPUESTA || r.largo < LARGO_NONCE + LARGO_HMAC) return 0;

    uint8_t h[LARGO_HMAC];
    if (!hmacSha256(s.claveLocal, LARGO_CLAVE, s.nonceLocal, LARGO_NONCE, h)) return 0;
    if (!igualesSeguro(h, r.datos + LARGO_NONCE, LARGO_HMAC)) return 0;   // no conoce la local key

    uint8_t remoto[LARGO_NONCE];
    memcpy(remoto, r.datos, LARGO_NONCE);
    if (!hmacSha256(s.claveLocal, LARGO_CLAVE, remoto, LARGO_NONCE, h)) return 0;
    size_t n = armarTrama(s, CMD_NEG_FIN, h, LARGO_HMAC, iv, salida, tam);   // aún con la local key
    if (!n) return 0;

    uint8_t x[LARGO_CLAVE], tag[LARGO_TAG];
    for (size_t i = 0; i < LARGO_CLAVE; i++) x[i] = s.nonceLocal[i] ^ remoto[i];
    bool ok = s.version == 34 ? aesEcb(true, s.claveLocal, x, s.clave, LARGO_CLAVE)
                              : gcmCifrar(s.claveLocal, s.nonceLocal, nullptr, 0, x, LARGO_CLAVE, s.clave, tag);
    if (!ok) return 0;
    s.negociada = true;
    return n;
}

//-----------------------------------------------------------------------------
// XenonDevice.payload_dict / generate_payload (json.dumps sin espacios)
size_t jsonControl(uint8_t version, const char* idTuya, uint32_t t, const char* dps, char* salida, size_t tam) {
    int n = version == 33
        ? snprintf(salida, tam, "{\"devId\":\"%s\",\"uid\":\"%s\",\"t\":\"%lu\",\"dps\":{%s}}", idTuya, idTuya, (unsigned long)t, dps)
        : snprintf(salida, tam, "{\"protocol\":5,\"t\":%lu,\"data\":{\"dps\":{%s}}}", (unsigned long)t, dps);
    return n > 0 && (size_t)n < tam ? (size_t)n : 0;
}

size_t jsonConsulta(uint8_t version, const char* idTuya, uint32_t t, char* salida, size_t tam) {
    int n = version == 33
        ? snprintf(salida, tam, "{\"gwId\":\"%s\",\"devId\":\"%s\",\"uid\":\"%s\",\"t\":\"%lu\"}", idTuya, idTuya, idTuya, (unsigned long)t)
        : snprintf(salida, tam, "{}");
    return n > 0 && (size_t)n < tam ? (size_t)n : 0;
}

}  // namespace tuya
