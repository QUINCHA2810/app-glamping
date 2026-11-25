/*
  Conversación TCP con un dispositivo Tuya (puerto 6668): conectar, negociar la
  clave de sesión (3.4 y 3.5), mandar una orden o una consulta y leer las
  respuestas. El formato de las tramas y el cifrado están en tuya_protocolo.h;
  aquí solo van el socket, los tiempos y la lectura del JSON.

  - Una sola conexión a la vez (la usa el loop, de a una orden). Entre dos
    conexiones seguidas al mismo equipo físico se espera PAUSA_MISMO_FISICO_MS.
  - Mientras espera respuestas atiende MQTT, telemetría y monitor serie
    (atenderFondo), que nunca hablan con un dispositivo Tuya.
  - Nonce local e IV de GCM: aleatorios del hardware (esp_fill_random), uno
    nuevo por negociación y por trama, como tinytuya (os.urandom).
  - La local key nunca se imprime.
*/

#include <esp_random.h>

WiFiClient tuyaCli;
tuya::Sesion tuyaSesion;

static uint8_t tuyaRx[tuya::MAX_TRAMA + 64];   // lo recibido y aún no procesado
static size_t  tuyaNrx = 0;
static uint8_t tuyaTx[tuya::MAX_TRAMA];
static uint8_t tuyaClaro[tuya::MAX_TRAMA];
static bool    tuyaDevice22 = false;             // el 3.3 contestó "data unvalid" a la consulta normal

static int           tuyaUltimo = -1;            // entrada de la última conexión
static unsigned long tuyaUltimoCierre = 0;

#define ESPERA_TRAS_ACUSE_MS  1000               // tras el acuse vacío, cuánto más se espera el STATUS

//-------------------------------------------------------------
bool tuyaEnviar(uint32_t comando, const char* json) {
    uint8_t iv[tuya::LARGO_IV];
    esp_fill_random(iv, sizeof(iv));
    size_t n = tuya::armarTrama(tuyaSesion, comando, (const uint8_t*)json, strlen(json), iv, tuyaTx, sizeof(tuyaTx));
    if (!n) { Serial.println("  -> no se pudo armar la trama (demasiado larga)."); return false; }
    if (tuyaCli.write(tuyaTx, n) != n) { Serial.println("  -> no se pudo enviar: la conexión se cerró."); return false; }
    return true;
}

// La próxima trama válida que llegue, en m. false si vence el plazo o se cierra la conexión.
// Las tramas cuya suma, HMAC o tag no coincide se descartan (y se avisa).
bool tuyaRecibir(tuya::Mensaje &m, unsigned long plazoMs) {
    unsigned long t0 = millis();
    for (;;) {
        while (tuyaNrx > 0) {
            long l = tuya::largoTrama(tuyaRx, tuyaNrx);
            if (l < 0) { memmove(tuyaRx, tuyaRx + 1, --tuyaNrx); continue; }   // basura: se busca el prefijo
            if (l == 0 || (size_t)l > tuyaNrx) break;                          // faltan bytes
            // sizeof - 1: siempre queda lugar para el '\0' que pone tuyaJson.
            bool ok = tuya::abrirTrama(tuyaSesion, tuyaRx, (size_t)l, m, tuyaClaro, sizeof(tuyaClaro) - 1);
            memmove(tuyaRx, tuyaRx + l, tuyaNrx - (size_t)l);
            tuyaNrx -= (size_t)l;
            if (ok) return true;
            Serial.println("  -> trama descartada: la suma, el HMAC o el tag no coinciden (¿local key o versión equivocadas?).");
        }
        if (millis() - t0 >= plazoMs) return false;
        int disponible = tuyaCli.available();
        if (disponible > 0) {
            size_t cabe = sizeof(tuyaRx) - tuyaNrx;
            int n = tuyaCli.read(tuyaRx + tuyaNrx, min((size_t)disponible, cabe));
            if (n > 0) tuyaNrx += (size_t)n;
        } else if (!tuyaCli.connected()) {
            return false;
        } else {
            atenderFondo();
            delay(5);
        }
    }
}

//-------------------------------------------------------------
// Conecta y, en 3.4/3.5, negocia la clave de sesión. Imprime la causa si falla.
bool tuyaConectar(int i, const uint8_t ip[4], uint8_t version) {
    const DispositivoTuya &d = DISPOSITIVOS[i];

    if (mismoFisico(tuyaUltimo, i)) {                  // otra salida del mismo equipo, recién cerrada
        unsigned long pasado = millis() - tuyaUltimoCierre;
        if (pasado < PAUSA_MISMO_FISICO_MS) esperarAtendiendo(PAUSA_MISMO_FISICO_MS - pasado);
    }

    tuya::iniciarSesion(tuyaSesion, version, (const uint8_t*)d.localKey);
    tuyaNrx = 0;
    tuyaDevice22 = false;
    if (!tuyaCli.connect(IPAddress(ip[0], ip[1], ip[2], ip[3]), 6668, CONEXION_TIMEOUT_MS)) {
        Serial.println("  -> sin conexión TCP con el dispositivo (puerto 6668): ¿apagado, otra IP, o la app Tuya lo tiene ocupado?");
        return false;
    }
    tuyaCli.setNoDelay(true);
    if (version == 33) return true;

    uint8_t nonce[tuya::LARGO_NONCE], iv[tuya::LARGO_IV];
    esp_fill_random(nonce, sizeof(nonce));
    esp_fill_random(iv, sizeof(iv));
    size_t n = tuya::armarInicioNegociacion(tuyaSesion, nonce, iv, tuyaTx, sizeof(tuyaTx));
    if (!n || tuyaCli.write(tuyaTx, n) != n) { Serial.println("  -> no se pudo iniciar la negociación."); return false; }

    tuya::Mensaje m;
    unsigned long t0 = millis();
    bool llego = false;
    while (millis() - t0 < RECV_TIMEOUT_MS && tuyaRecibir(m, RECV_TIMEOUT_MS - (millis() - t0))) {
        if (m.comando == tuya::CMD_NEG_RESPUESTA) { llego = true; break; }
    }
    if (!llego) {
        Serial.printf("  -> no respondió a la negociación de clave (¿la versión es 3.%d? ¿la local key es la vigente?).\n", version % 10);
        return false;
    }
    esp_fill_random(iv, sizeof(iv));
    n = tuya::armarFinNegociacion(tuyaSesion, m, iv, tuyaTx, sizeof(tuyaTx));
    if (!n) {
        Serial.println("  -> la respuesta de negociación no prueba que el dispositivo conozca la local key: revisa la local key.");
        return false;
    }
    if (tuyaCli.write(tuyaTx, n) != n) { Serial.println("  -> no se pudo terminar la negociación."); return false; }
    return true;
}

void tuyaCerrar(int i) {
    tuyaCli.stop();
    memset(&tuyaSesion, 0, sizeof(tuyaSesion));      // no deja claves en la RAM
    tuyaNrx = 0;
    tuyaUltimo = i;
    tuyaUltimoCierre = millis();
}

//-------------------------------------------------------------
// JSON de una respuesta: se anotan los DPS (registrarRespuestaTuya, domo_nodo.ino).
void tuyaJson(int i, const tuya::Mensaje &m) {
    char* texto = (char*)m.datos;
    texto[m.largo] = '\0';                            // cabe: abrirTrama recibió sizeof - 1
    if (strstr(texto, "data unvalid")) { tuyaDevice22 = true; return; }
    char* ini = strchr(texto, '{');
    if (!ini) return;
    JsonDocument r;
    if (deserializeJson(r, ini)) { Serial.println("  -> respuesta con JSON inválido."); return; }
    JsonVariant dps = r["dps"];
    if (dps.isNull()) dps = r["data"]["dps"];
    if (dps.isNull()) return;
    Serial.printf("  <- 0x%02lx dps ", (unsigned long)m.comando);
    serializeJson(dps, Serial);                       // solo los DPS: el JSON de 3.3 trae el ID completo
    Serial.println();
    registrarRespuestaTuya(i, r);
}

// Lee respuestas hasta ver lo que falta (`pedidos`: máscara FALTA_*, tipos_domo.h) o vencer el plazo.
void tuyaEsperar(int i, int pedidos, uint32_t comandoEnviado) {
    unsigned long t0 = millis(), plazo = RECV_TIMEOUT_MS;
    tuya::Mensaje m;
    while (millis() - t0 < plazo && tuyaRecibir(m, plazo - (millis() - t0))) {
        if (m.retorno != 0) Serial.printf("  -> el dispositivo respondió código %lu al comando 0x%02lx.\n",
                                          (unsigned long)m.retorno, (unsigned long)m.comando);
        if (m.largo) tuyaJson(i, m);
        if (!faltanDps(pedidos)) return;
        // Acuse vacío de la orden: el STATUS con los DPS suele llegar enseguida; si no llega, se consulta.
        if (m.comando == comandoEnviado && m.largo == 0) plazo = min(plazo, millis() - t0 + ESPERA_TRAS_ACUSE_MS);
    }
}

//-------------------------------------------------------------
uint32_t tuyaAhora() {
    return (uint32_t)time(nullptr);
}

// Manda la orden (`dps`: lo que va dentro de "dps":{...}) y lee las respuestas.
// false si no se pudo enviar.
bool tuyaOrden(int i, const char* dps, int pedidos) {
    const DispositivoTuya &d = DISPOSITIVOS[i];
    char json[400];                                   // cabe la escena más larga (158 caracteres)
    uint8_t v = tuyaSesion.version;
    if (!tuya::jsonControl(v, d.tuyaId, tuyaAhora(), dps, json, sizeof(json))) {
        Serial.println("  -> la orden no cabe en el JSON de control.");
        return false;
    }
    Serial.printf("  -> orden dps {%s}\n", dps);
    uint32_t comando = tuya::comandoControl(v);
    if (!tuyaEnviar(comando, json)) return false;
    tuyaEsperar(i, pedidos, comando);
    return true;
}

// Consulta el estado (DP_QUERY en 3.3, DP_QUERY_NEW en 3.4/3.5) y lee las respuestas.
void tuyaConsulta(int i, int pedidos) {
    const DispositivoTuya &d = DISPOSITIVOS[i];
    char json[400];
    uint8_t v = tuyaSesion.version;
    uint32_t comando = tuya::comandoConsulta(v);
    if (!tuya::jsonConsulta(v, d.tuyaId, tuyaAhora(), json, sizeof(json)) || !tuyaEnviar(comando, json)) return;
    tuyaEsperar(i, pedidos, comando);
    if (v == 33 && tuyaDevice22 && faltanDps(pedidos)) {
        // "device22": se pregunta con CONTROL_NEW y el DPS en null (tinytuya payload_dict).
        Serial.println("  -> \"data unvalid\": se consulta como device22.");
        char nulos[16];
        snprintf(nulos, sizeof(nulos), "\"%d\":null", dpsEncendido(d));
        if (tuya::jsonControl(33, d.tuyaId, tuyaAhora(), nulos, json, sizeof(json)) && tuyaEnviar(tuya::CMD_CONTROL_NUEVO, json))
            tuyaEsperar(i, pedidos, tuya::CMD_CONTROL_NUEVO);
    }
}
