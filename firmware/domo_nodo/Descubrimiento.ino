/*
  Descubrimiento de dispositivos Tuya en la red local, sin la nube.

  Los dispositivos Tuya se anuncian por UDP broadcast con un JSON que trae su ID
  ("gwId"), su IP y su versión de protocolo. Con eso se arma una tabla ID -> IP y
  versión, y en secrets.h la IP (y la versión) de un dispositivo pueden quedar en 0:
  se usa la descubierta, y si el router le cambia la IP, el siguiente anuncio la
  actualiza sola.

  Puertos y cifrado (los mismos que usa tinytuya, que es de donde salen):
    6666  versión 3.1: JSON sin cifrar dentro de un paquete 55AA.
    6667  versiones 3.3 y 3.4: paquete 55AA cuyo payload está cifrado con
          AES-128-ECB y relleno PKCS#7. Se anuncian solos, cada ~5 s.
    7000  versión 3.5: paquete 6699 cifrado con AES-128-GCM (IV de 12 bytes, tag de
          16, y como datos autenticados los 14 bytes del encabezado sin el prefijo).
          Estos solo se anuncian si alguien se lo pide: el ESP32 envía a
          255.255.255.255:7000 un 6699 con el comando REQ_DEVINFO (0x25) y el JSON
          {"from":"app","ip":"<IP del ESP32>"}, como hace el escáner de tinytuya.

  La clave es fija y la misma para todos los dispositivos de Tuya: los 16 bytes de
  md5("yGAdlopoPVldABfn") = 6c1ec8e2bb9bb59ab50b0daf649b410a (UDP_key, abajo).
  Fuente: tinytuya/core/udp_helper.py
  (udpkey = md5(b"yGAdlopoPVldABfn").digest() y decrypt_udp), tinytuya/core/const.py
  (UDPPORT 6666, UDPPORTS 6667, UDPPORTAPP 7000) y tinytuya/scanner.py
  (send_discovery_request). Como la clave es pública, el anuncio no es secreto: no
  trae la local key, que es la que protege las órdenes.

  Usa sus PROPIOS buffers, nunca los de TuyaLocal.ino: atenderDescubrimiento()
  corre dentro de atenderFondo(), también en medio de una orden a un dispositivo.
  El cifrado es el de tuya_cripto.h (mbedtls), sin estado compartido.
*/

#include "tuya_cripto.h"

// md5("yGAdlopoPVldABfn"): pública e igual para todos los dispositivos Tuya.
static const uint8_t UDP_key[16] = { 0x6C, 0x1E, 0xC8, 0xE2, 0xBB, 0x9B, 0xB5, 0x9A, 0xB5, 0x0B, 0x0D, 0xAF, 0x64, 0x9B, 0x41, 0x0A };

WiFiUDP udpTuya[3];
const uint16_t PUERTOS_TUYA[3] = { PUERTO_TUYA_31, PUERTO_TUYA_33, PUERTO_TUYA_35 };

Descubierto descubiertos[MAX_DESCUBIERTOS];
int numDescubiertos = 0;
bool descubrimientoActivo = false;
unsigned long ultimaSolicitud35 = 0;
uint32_t secuencia35 = 1;

static uint8_t bufAnuncio[640];
static uint8_t bufClaro[640];

//-------------------------------------------------------------
void iniciarDescubrimiento() {
    if (!DESCUBRIR_TUYA) return;
    for (int i = 0; i < 3; i++) {
        udpTuya[i].stop();
        if (!udpTuya[i].begin(PUERTOS_TUYA[i])) Serial.printf("No se pudo escuchar el puerto UDP %u.\n", PUERTOS_TUYA[i]);
    }
    descubrimientoActivo = true;
    ultimaSolicitud35 = 0;
    Serial.println("Descubrimiento Tuya: escuchando UDP 6666, 6667 y 7000 (comando \"red\" para ver la tabla).");
}

//-------------------------------------------------------------
const char* idCorto(const char* id) {           // solo los 4 últimos caracteres
    size_t n = strlen(id);
    return n > 4 ? id + n - 4 : id;
}

int buscarDescubierto(const char* gwId) {
    for (int k = 0; k < numDescubiertos; k++) if (!strcmp(descubiertos[k].gwId, gwId)) return k;
    return -1;
}

void registrarDescubierto(const char* gwId, IPAddress ip, int version, uint16_t puerto) {
    if (!*gwId || strlen(gwId) >= sizeof(descubiertos[0].gwId)) return;
    int k = buscarDescubierto(gwId);
    uint8_t nueva[4] = { ip[0], ip[1], ip[2], ip[3] };
    if (k < 0) {
        if (numDescubiertos < MAX_DESCUBIERTOS) k = numDescubiertos++;
        else {                                      // tabla llena: se reemplaza el más viejo
            k = 0;
            for (int j = 1; j < numDescubiertos; j++) if (descubiertos[j].visto < descubiertos[k].visto) k = j;
        }
        strncpy(descubiertos[k].gwId, gwId, sizeof(descubiertos[k].gwId) - 1);
        descubiertos[k].gwId[sizeof(descubiertos[k].gwId) - 1] = '\0';
        for (int b = 0; b < 4; b++) descubiertos[k].ip[b] = ip[b];
        Serial.printf("Tuya descubierto: ...%s en %s (v%d.%d, puerto %u)\n", idCorto(gwId), ip.toString().c_str(),
                      version / 10, version % 10, puerto);
    } else if (memcmp(descubiertos[k].ip, nueva, 4) != 0) {
        Serial.printf("Tuya ...%s cambió de IP: %d.%d.%d.%d -> %s\n", idCorto(gwId), descubiertos[k].ip[0],
                      descubiertos[k].ip[1], descubiertos[k].ip[2], descubiertos[k].ip[3], ip.toString().c_str());
        for (int b = 0; b < 4; b++) descubiertos[k].ip[b] = ip[b];
    }
    if (version) descubiertos[k].version = version;
    descubiertos[k].puerto = puerto;
    descubiertos[k].visto = millis();
}

// IP y versión anunciadas por el dispositivo con ese ID de Tuya. false si no se ha visto.
bool direccionDescubierta(const char* tuyaId, uint8_t ip[4], int* version) {
    if (!tuyaId || !*tuyaId) return false;
    int k = buscarDescubierto(tuyaId);
    if (k < 0) return false;
    memcpy(ip, descubiertos[k].ip, 4);
    *version = descubiertos[k].version;
    return true;
}

//-------------------------------------------------------------
// Copia a `out` el JSON (de '{' a '}') que haya en `datos`. Devuelve su largo, o 0.
int extraerJson(const uint8_t* datos, int n, char* out, int tam) {
    int ini = -1, fin = -1;
    for (int i = 0; i < n && i < 8; i++) if (datos[i] == '{') { ini = i; break; }
    for (int i = n - 1; i > ini && ini >= 0; i--) if (datos[i] == '}') { fin = i; break; }
    if (ini < 0 || fin < 0 || fin - ini + 1 >= tam) return 0;
    memcpy(out, datos + ini, fin - ini + 1);
    out[fin - ini + 1] = '\0';
    return fin - ini + 1;
}

// AES-128-ECB con la clave UDP y quita el relleno PKCS#7. Devuelve el largo en claro, o 0.
int descifrarEcb(const uint8_t* in, int n, uint8_t* out) {
    if (n <= 0 || n % 16 || n > (int)sizeof(bufClaro)) return 0;
    if (!tuya::aesEcb(false, UDP_key, in, out, n)) return 0;
    int relleno = out[n - 1];
    if (relleno < 1 || relleno > 16) return 0;
    for (int i = n - relleno; i < n; i++) if (out[i] != relleno) return 0;
    return n - relleno;
}

// Paquete 55AA: prefijo(4) secuencia(4) comando(4) largo(4) [código(4)] payload checksum(4 o 32) sufijo(4).
// El largo cuenta desde el código de retorno hasta el sufijo. Se prueban las variantes
// conocidas (con y sin código de retorno, CRC32 o HMAC) hasta que salga JSON.
int descifrar55AA(const uint8_t* p, int n, char* json, int tam) {
    if (n < 24) return 0;
    uint32_t largo = ((uint32_t)p[12] << 24) | ((uint32_t)p[13] << 16) | ((uint32_t)p[14] << 8) | p[15];
    if (largo + 16 > (uint32_t)n || largo < 8) return 0;
    const int codigos[] = { 4, 0 }, sumas[] = { 4, 32 };
    for (int c : codigos) for (int s : sumas) {
        int plen = (int)largo - c - s - 4;
        if (plen <= 0) continue;
        const uint8_t* payload = p + 16 + c;
        if (payload[0] == '{') return extraerJson(payload, plen, json, tam);   // 3.1, sin cifrar
        int claro = descifrarEcb(payload, plen, bufClaro);
        if (claro > 0) {
            int nj = extraerJson(bufClaro, claro, json, tam);
            if (nj) return nj;
        }
    }
    return 0;
}

// Paquete 6699: prefijo(4) desconocido(2) secuencia(4) comando(4) largo(4) IV(12) cifrado tag(16) sufijo(4).
// El largo cuenta del IV al tag. Datos autenticados: los 14 bytes que siguen al prefijo.
int descifrar6699(const uint8_t* p, int n, char* json, int tam) {
    if (n < 18 + 12 + 16 + 4) return 0;
    uint32_t largo = ((uint32_t)p[14] << 24) | ((uint32_t)p[15] << 16) | ((uint32_t)p[16] << 8) | p[17];
    if (largo < 12 + 16 + 1 || 18 + largo + 4 > (uint32_t)n) return 0;
    int cifrado = (int)largo - 12 - 16;
    if (cifrado > (int)sizeof(bufClaro)) return 0;
    if (!tuya::gcmDescifrar(UDP_key, p + 18, p + 4, 14, p + 30, cifrado, bufClaro, p + 30 + cifrado)) return 0;
    return extraerJson(bufClaro, cifrado, json, tam);      // puede traer 4 bytes de código antes del '{'
}

//-------------------------------------------------------------
void procesarAnuncio(const uint8_t* p, int n, uint16_t puerto, IPAddress remitente) {
    char json[512];
    int nj = 0;
    if (n >= 4 && p[0] == 0 && p[1] == 0 && p[2] == 0x55 && p[3] == 0xAA) nj = descifrar55AA(p, n, json, sizeof(json));
    else if (n >= 4 && p[0] == 0 && p[1] == 0 && p[2] == 0x66 && p[3] == 0x99) nj = descifrar6699(p, n, json, sizeof(json));
    else if (n > 0 && p[0] == '{') nj = extraerJson(p, n, json, sizeof(json));
    else {
        int claro = descifrarEcb(p, n, bufClaro);           // algunos firmwares mandan el cifrado sin marco
        if (claro > 0) nj = extraerJson(bufClaro, claro, json, sizeof(json));
    }
    if (nj <= 0) return;

    JsonDocument d;                                          // local: no toca el global "doc"
    if (deserializeJson(d, json, nj)) return;
    const char* id = d["gwId"] | "";
    if (!*id) id = d["devId"] | "";
    if (!*id) return;                                        // p. ej. nuestra propia petición REQ_DEVINFO
    IPAddress ip;
    if (!ip.fromString(d["ip"] | "")) ip = remitente;
    const char* v = d["version"] | "";
    int version = 0;
    if (strlen(v) >= 3 && v[1] == '.') version = (v[0] - '0') * 10 + (v[2] - '0');
    registrarDescubierto(id, ip, version, puerto);
}

//-------------------------------------------------------------
// Pide a los 3.5 que se anuncien: 6699 con REQ_DEVINFO a 255.255.255.255:7000.
void solicitarAnuncios35() {
    char cuerpo[64];
    int n = snprintf(cuerpo, sizeof(cuerpo), "{\"from\":\"app\",\"ip\":\"%s\"}", WiFi.localIP().toString().c_str());
    uint8_t paquete[18 + 12 + sizeof(cuerpo) + 16 + 4];
    uint32_t largo = 12 + n + 16;
    uint32_t seq = secuencia35++;
    uint8_t cabecera[18] = { 0x00, 0x00, 0x66, 0x99, 0x00, 0x00,
                             (uint8_t)(seq >> 24), (uint8_t)(seq >> 16), (uint8_t)(seq >> 8), (uint8_t)seq,
                             0x00, 0x00, 0x00, CMD_REQ_DEVINFO,
                             (uint8_t)(largo >> 24), (uint8_t)(largo >> 16), (uint8_t)(largo >> 8), (uint8_t)largo };
    memcpy(paquete, cabecera, 18);
    uint8_t* iv = paquete + 18;
    esp_fill_random(iv, 12);

    if (!tuya::gcmCifrar(UDP_key, iv, paquete + 4, 14, (const uint8_t*)cuerpo, n, paquete + 30, paquete + 30 + n)) return;
    const uint8_t sufijo[4] = { 0x00, 0x00, 0x99, 0x66 };
    memcpy(paquete + 30 + n + 16, sufijo, 4);

    udpTuya[2].beginPacket(IPAddress(255, 255, 255, 255), PUERTO_TUYA_35);
    udpTuya[2].write(paquete, 30 + n + 16 + 4);
    udpTuya[2].endPacket();
}

//-------------------------------------------------------------
// No bloquea: lee como mucho ANUNCIOS_POR_VUELTA paquetes por puerto y vuelve.
void atenderDescubrimiento() {
    if (!descubrimientoActivo || WiFi.status() != WL_CONNECTED) return;
    for (int i = 0; i < 3; i++) {
        for (int k = 0; k < ANUNCIOS_POR_VUELTA; k++) {
            int tam = udpTuya[i].parsePacket();
            if (tam <= 0) break;
            int n = udpTuya[i].read(bufAnuncio, min(tam, (int)sizeof(bufAnuncio)));
            if (n > 0) procesarAnuncio(bufAnuncio, n, PUERTOS_TUYA[i], udpTuya[i].remoteIP());
        }
    }
    if (ultimaSolicitud35 == 0 || millis() - ultimaSolicitud35 >= PERIODO_SOLICITUD_35_MS) {
        ultimaSolicitud35 = millis();
        solicitarAnuncios35();
    }
}

//-------------------------------------------------------------
// Comando "red": la tabla, sin local keys ni IDs completos.
void mostrarRed() {
    if (!DESCUBRIR_TUYA) { Serial.println("Descubrimiento desactivado (DESCUBRIR_TUYA 0)."); return; }
    Serial.printf("Tuya en la red (%d). IDs: solo los 4 últimos caracteres.\n", numDescubiertos);
    for (int k = 0; k < numDescubiertos; k++) {
        const Descubierto &x = descubiertos[k];
        const char* nuestro = "-";
        for (int i = 0; i < NUM_DISPOSITIVOS; i++)
            if (DISPOSITIVOS[i].tuyaId && !strcmp(DISPOSITIVOS[i].tuyaId, x.gwId)) nuestro = DISPOSITIVOS[i].idFisico;
        Serial.printf("  ...%-4s  %3d.%3d.%3d.%3d  v%d.%d  puerto %u  hace %lu s  -> %s\n", idCorto(x.gwId),
                      x.ip[0], x.ip[1], x.ip[2], x.ip[3], x.version / 10, x.version % 10, x.puerto,
                      (unsigned long)((millis() - x.visto) / 1000), nuestro);
    }
    listarDispositivos();
}
