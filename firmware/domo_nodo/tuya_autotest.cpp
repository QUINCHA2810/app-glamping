// Ver tuya_autotest.h. C++ puro: el mismo código corre en el ESP32 y en el PC.
#include "tuya_autotest.h"
#include "tuya_protocolo.h"
#include "tuya_vectores.h"

#include <new>
#include <stdio.h>
#include <string.h>

namespace tuya {

namespace {

using namespace tuya_vectores;

// Se reservan solo mientras corre el autotest: no ocupan RAM el resto del tiempo.
struct Buffers {
    uint8_t salida[MAX_TRAMA];
    uint8_t claro[MAX_TRAMA];
    uint8_t copia[MAX_TRAMA];
};
Buffers* b = nullptr;

struct Contexto {
    void (*imprimir)(const char*);
    const char* version;
    int fallos;
    bool comprobar(bool ok, const char* paso) {
        if (!ok) {
            char linea[96];
            snprintf(linea, sizeof(linea), "  %s FALLO: %s", version, paso);
            imprimir(linea);
            fallos++;
        }
        return ok;
    }
};

bool iguales(const uint8_t* a, size_t na, const uint8_t* b, size_t nb) {
    return na == nb && memcmp(a, b, na) == 0;
}

// La trama con un byte cambiado en `pos` ya no debe abrirse.
bool rechazaAlterada(const Sesion& s, const uint8_t* t, size_t n, size_t pos) {
    memcpy(b->copia, t, n);
    b->copia[pos] ^= 0x01;
    Mensaje m;
    return !abrirTrama(s, b->copia, n, m, b->claro, sizeof(b->claro));
}

void probarVector(const Vector& v, Contexto& c) {
    const uint8_t* iv = IV;           // inicio, fin, control y consulta: 12 bytes cada uno
    Sesion s;
    Mensaje m;
    if (!c.comprobar(iniciarSesion(s, v.version, CLAVE), "iniciarSesion")) return;

    if (v.inicio) {
        size_t n = armarInicioNegociacion(s, NONCE_LOCAL, iv, b->salida, sizeof(b->salida));
        c.comprobar(iguales(b->salida, n, v.inicio, v.nInicio), "SESS_KEY_NEG_START distinto de tinytuya");

        Sesion impostor = s;
        bool abre = abrirTrama(impostor, v.respuestaMala, v.nRespuestaMala, m, b->claro, sizeof(b->claro));
        c.comprobar(abre && armarFinNegociacion(impostor, m, iv + 12, b->salida, sizeof(b->salida)) == 0,
                    "acepto un HMAC hecho con otra clave");

        abre = abrirTrama(s, v.respuesta, v.nRespuesta, m, b->claro, sizeof(b->claro));
        if (!c.comprobar(abre && m.comando == CMD_NEG_RESPUESTA, "no abrio SESS_KEY_NEG_RESP")) return;
        n = armarFinNegociacion(s, m, iv + 12, b->salida, sizeof(b->salida));
        c.comprobar(iguales(b->salida, n, v.fin, v.nFin), "SESS_KEY_NEG_FINISH distinto de tinytuya");
        if (!c.comprobar(s.negociada && memcmp(s.clave, v.claveSesion, LARGO_CLAVE) == 0, "clave de sesion distinta")) return;
    }

    char json[160];
    size_t nj = jsonControl(v.version, ID_TUYA, T, v.dps, json, sizeof(json));
    size_t n = armarTrama(s, comandoControl(v.version), (const uint8_t*)json, nj, iv + 24, b->salida, sizeof(b->salida));
    c.comprobar(nj && iguales(b->salida, n, v.control, v.nControl), "orden (CONTROL) distinta de tinytuya");

    nj = jsonConsulta(v.version, ID_TUYA, T, json, sizeof(json));
    n = armarTrama(s, comandoConsulta(v.version), (const uint8_t*)json, nj, iv + 36, b->salida, sizeof(b->salida));
    c.comprobar(nj && iguales(b->salida, n, v.consulta, v.nConsulta), "consulta (DP_QUERY) distinta de tinytuya");

    bool abre = abrirTrama(s, v.acuse, v.nAcuse, m, b->claro, sizeof(b->claro));
    c.comprobar(abre && m.largo == 0 && m.retorno == 0 && m.comando == comandoControl(v.version), "acuse vacio");

    abre = abrirTrama(s, v.estado, v.nEstado, m, b->claro, sizeof(b->claro));
    c.comprobar(abre && m.comando == CMD_ESTADO &&
                iguales(m.datos, m.largo, (const uint8_t*)v.jsonEstado, strlen(v.jsonEstado)),
                "STATUS: JSON distinto");

    // Integridad: un byte cambiado en los datos, en la suma/tag o en el encabezado.
    c.comprobar(rechazaAlterada(s, v.estado, v.nEstado, v.nEstado / 2), "acepto datos alterados");
    c.comprobar(rechazaAlterada(s, v.estado, v.nEstado, v.nEstado - 6), "acepto suma o tag alterado");
    c.comprobar(rechazaAlterada(s, v.estado, v.nEstado, 7), "acepto encabezado alterado");

    // Otra versión no abre la trama.
    Sesion otra;
    iniciarSesion(otra, v.version == 35 ? 34 : 35, CLAVE);
    c.comprobar(!abrirTrama(otra, v.estado, v.nEstado, m, b->claro, sizeof(b->claro)), "abrio la trama con otra version");

    // Lectura por flujo: dos tramas seguidas y basura delante.
    memcpy(b->copia, v.acuse, v.nAcuse);
    memcpy(b->copia + v.nAcuse, v.estado, v.nEstado);
    c.comprobar(largoTrama(b->copia, v.nAcuse + v.nEstado) == (long)v.nAcuse &&
                largoTrama(b->copia + v.nAcuse, v.nEstado) == (long)v.nEstado &&
                largoTrama(b->copia, 10) == 0 && largoTrama(b->copia + 1, 20) == -1,
                "largoTrama");
}

}  // namespace

int autotest(void (*imprimir)(const char*)) {
    Contexto c = { imprimir, "", 0 };
    b = new (std::nothrow) Buffers;
    if (!b) { imprimir("autotest: FALLO (sin memoria para los buffers)"); return 1; }
    const char* nueve = "123456789";
    c.version = "CRC32";
    c.comprobar(crc32((const uint8_t*)nueve, 9) == 0xCBF43926u, "valor de referencia");

    for (const Vector& v : VECTORES) {
        char nombre[8], linea[64];
        snprintf(nombre, sizeof(nombre), "3.%d", v.version % 10);
        c.version = nombre;
        int antes = c.fallos;
        probarVector(v, c);
        snprintf(linea, sizeof(linea), "Tuya %s: %s", nombre, c.fallos == antes ? "OK" : "FALLO");
        imprimir(linea);
    }
    char linea[64];
    snprintf(linea, sizeof(linea), "autotest: %s (%d fallos)", c.fallos ? "FALLO" : "OK", c.fallos);
    imprimir(linea);
    delete b;
    b = nullptr;
    return c.fallos;
}

}  // namespace tuya
