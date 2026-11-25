/*
    domo_nodo: el ESP32 del domo (proyecto Vatia / APP-GLAMPING). Ver firmware/README.md.

    Mide (Sensores.ino), publica la telemetría por MQTT y ejecuta las órdenes de la app
    sobre las tomas y el bombillo Tuya por la red local, sin la nube:
      - protocolo Tuya 3.3 / 3.4 / 3.5: tuya_protocolo.h/.cpp (referencia: tinytuya, MIT)
      - conexión TCP y orden completa: TuyaLocal.ino
      - descubrimiento UDP (IP y versión): Descubrimiento.ino
    Empezó como una adaptación de EspTuya (FrBerger83, https://github.com/FrBerger83/EspTuya);
    el original sigue en firmware/EspTuya/.
*/

const char* thisPgm  = "APP-GLAMPING/domo_nodo"  ;

/*  Escucha (sin retener, los publica el backend, mqtt_publisher.py):
      domo/1/comando/toma      {"accion":"on"|"off","id_fisico":"toma1","origen":"manual"|"r13"}
      domo/1/comando/bombillo  {"id_fisico":"bombillo1","origen":"manual","accion":"on"|"off","brillo":0-100}
                               (accion y brillo opcionales, al menos uno)
      domo/1/comando/soc       {"accion":"iniciar"|"confirmar"|"cancelar"}   (QoS 1; recalibración
                               guiada del SoC, Sensores.ino. Nunca trae ni usa un valor de SoC)

    Publica:
      domo/1/estado/toma/<id_fisico>      (retenido) {"id_fisico":..,"estado":"on|off|desconocido","verificado":bool[,"error":..]}
      domo/1/estado/bombillo/<id_fisico>  (retenido) igual, con "brillo" 0-100 si se conoce
          verificado:true  -> el dispositivo confirmó lo pedido EN EL DPS DE ESA ENTRADA.
          verificado:false -> 3 intentos fallidos; "estado" es el último CONFIRMADO, no el pedido.
                              error "ip_desconocida": la IP o la versión van en 0 en secrets.h y
                              el dispositivo todavía no se ha anunciado (Descubrimiento.ino).
      domo/1/estado/esp32                 "online" / "offline" (retenido, con Last Will)
      domo/1/estado/soc                   (retenido) {"estado":"inactiva|esperando|lista|aplicada|cancelada|
                                          vencida|rechazada","minutos_ok","requeridos","motivo","soc_actual",
                                          "soc_propuesto","voltaje","t"}; "inactiva" al arrancar
      domo/1/telemetria/{fotovoltaica,bateria,consumo,sensores}   cada 10 s (Sensores.ino)
*/

#include <ArduinoJson.h>
#include <WiFi.h>
#include "time.h"
#include "tipos_domo.h"
#include "dispositivos_config.h"
#include "sensores_config.h"
#include "soc_estimador.h"        // tipos del SoC (Sensores.ino), antes de los prototipos
#include "tuya_protocolo.h"
#include "tuya_autotest.h"
#include "secrets.h"
#include <PubSubClient.h>   // MQTT (la misma libreria que ya usa mqtt_test)

const char* ssid = WIFI_SSID;
const char* password = WIFI_PASSWORD;

//--------------------- Dispositivos del domo: la lista está en secrets.h
const DispositivoTuya DISPOSITIVOS[] = DISPOSITIVOS_TUYA;
const int NUM_DISPOSITIVOS = sizeof(DISPOSITIVOS) / sizeof(DISPOSITIVOS[0]);

bool dispositivoValido[NUM_DISPOSITIVOS];
int  estadoConocido[NUM_DISPOSITIVOS];      // último encendido CONFIRMADO: -1 desconocido, 0 off, 1 on
int  brilloConocido[NUM_DISPOSITIVOS];      // último brillo confirmado, 0-100, -1 desconocido
bool ordenNuevaPara[NUM_DISPOSITIVOS];      // llegó otra orden mientras se ejecutaba una

int  modoConocido[NUM_DISPOSITIVOS];        // bombillo: último modo confirmado (MODO_*), -1 desconocido
int  tempConocida[NUM_DISPOSITIVOS];        // bombillo: última temperatura confirmada, escala Tuya 0-1000, -1
int  hConocido[NUM_DISPOSITIVOS];           // bombillo: último color confirmado, H 0-360, S y V 0-1000, -1
int  sConocido[NUM_DISPOSITIVOS];
int  vConocido[NUM_DISPOSITIVOS];

int  switchVisto  = -1;                     // DPS de encendido de la entrada actual en las respuestas
int  brilloVisto  = -1;                     // DPS de brillo (escala Tuya) en las respuestas
int  modoVisto    = -1;                     // bombillo: DPS+1 (MODO_*)
int  tempVisto    = -1;                     // bombillo: DPS+3, escala Tuya
int  hVisto = -1, sVisto = -1, vVisto = -1; // bombillo: DPS+4, escala Tuya

// ---------------------------- MQTT
#define DOMO_ID          1
const char* TOPIC_CMD_TOMA     = "domo/1/comando/toma";
const char* TOPIC_CMD_BOMBILLO = "domo/1/comando/bombillo";
const char* TOPIC_CMD_SOC      = "domo/1/comando/soc";
const char* TOPIC_ESTADO_ESP   = "domo/1/estado/esp32";

WiFiClient   mqttNet;                 // conexion al broker (distinta de la del dispositivo Tuya, TuyaLocal.ino)
PubSubClient mqtt(mqttNet);
unsigned long lastMqttTry  = 0;

// ---------------------------- Cola de órdenes (las anota el callback o el monitor serie; las ejecuta el loop)
Orden ordenes[MAX_ORDENES_PENDIENTES];
int   numOrdenes = 0;

//-------------------------------------------------------------
const char* textoEstado(int e) {
    return e == 1 ? "on" : e == 0 ? "off" : "desconocido";
}

const char* nombreTipo(uint8_t tipo) {
    return tipo == TIPO_BOMBILLO ? "bombillo" : "toma";
}

// DPS de encendido de la entrada (0 en secrets.h = el de su tipo).
int dpsEncendido(const DispositivoTuya &d) {
    return d.dps ? d.dps : (d.tipo == TIPO_BOMBILLO ? DPS_BOMBILLO_POR_DEFECTO : DPS_TOMA_POR_DEFECTO);
}

int brilloATuya(int brillo) {                   // 0-100 de la app -> escala del bombillo
    brillo = constrain(brillo, 0, 100);
    return BRILLO_TUYA_MIN + (long)(BRILLO_TUYA_MAX - BRILLO_TUYA_MIN) * brillo / 100;
}

int brilloDeTuya(int valor) {
    return constrain((int)lround((valor - BRILLO_TUYA_MIN) * 100.0 / (BRILLO_TUYA_MAX - BRILLO_TUYA_MIN)), 0, 100);
}

int tempATuya(int t)  { return constrain(t, 0, 100) * TEMP_TUYA_MAX / 100; }   // 0 = más cálido
int satATuya(int s)   { return constrain(s, 0, 100) * SAT_TUYA_MAX / 100; }
int deEscalaTuya(int valor, int maximo) { return constrain((int)lround(valor * 100.0 / maximo), 0, 100); }

const char* nombreModo(int m) {             // como lo publica el ESP32 y lo pide la app
    return m == MODO_BLANCO ? "blanco" : m == MODO_COLOR ? "color" : m == MODO_ESCENA ? "escena" : "otro";
}
const char* modoTuya(int m) {               // valor del DPS 21
    return m == MODO_BLANCO ? "white" : m == MODO_COLOR ? "colour" : "scene";
}
int modoDeTuya(const char* s) {
    return !strcmp(s, "white") ? MODO_BLANCO : !strcmp(s, "colour") ? MODO_COLOR : !strcmp(s, "scene") ? MODO_ESCENA : MODO_OTRO;
}
int buscarEscena(const char* nombre) {
    for (int k = 0; k < NUM_ESCENAS_BOMBILLO; k++) if (!strcmp(ESCENAS_BOMBILLO[k].nombre, nombre)) return k;
    return -1;
}

// Lo de `pedidos` (máscara FALTA_*) que todavía no llegó en las respuestas. 0 = está todo.
int faltanDps(int pedidos) {
    int f = 0;
    if ((pedidos & FALTA_SWITCH) && switchVisto < 0) f |= FALTA_SWITCH;
    if ((pedidos & FALTA_BRILLO) && brilloVisto < 0) f |= FALTA_BRILLO;
    if ((pedidos & FALTA_MODO)   && modoVisto   < 0) f |= FALTA_MODO;
    if ((pedidos & FALTA_TEMP)   && tempVisto   < 0) f |= FALTA_TEMP;
    if ((pedidos & FALTA_COLOR)  && hVisto      < 0) f |= FALTA_COLOR;
    return f;
}

// Mismo equipo físico: mismo ID de Tuya. validarDispositivos exige que entonces
// también compartan IP, local key y versión.
bool mismoFisico(int a, int b) {
    return a >= 0 && b >= 0 && !strcmp(DISPOSITIVOS[a].tuyaId, DISPOSITIVOS[b].tuyaId);
}

// Un id que se puede poner en un tema MQTT sin romperlo.
bool idSeguro(const char* id) {
    size_t n = id ? strlen(id) : 0;
    if (n == 0 || n > 32) return false;
    for (size_t k = 0; k < n; k++) if (!isalnum((unsigned char)id[k]) && id[k] != '_' && id[k] != '-') return false;
    return true;
}

//-------------------------------------------------------------
// TuyaLocal.ino la llama con el JSON de cada respuesta del dispositivo i:
// {"dps":{"1":true}} (consulta) o {"protocol":4,"t":..,"data":{"dps":{..}}}.
// Anota lo de ESA entrada (su DPS) y, de paso, el estado de las otras salidas
// del mismo equipo que vengan en la respuesta.
void registrarRespuestaTuya(int i, JsonDocument &r) {
    JsonVariant dps = r["dps"];
    if (dps.isNull()) dps = r["data"]["dps"];
    if (dps.isNull()) return;
    char clave[8];
    for (int j = 0; j < NUM_DISPOSITIVOS; j++) {
        if (j != i && !(dispositivoValido[j] && mismoFisico(i, j))) continue;
        const DispositivoTuya &d = DISPOSITIVOS[j];
        snprintf(clave, sizeof(clave), "%d", dpsEncendido(d));
        JsonVariant sw = dps[clave];
        if (!sw.is<bool>()) continue;
        if (j == i) switchVisto = sw.as<bool>() ? 1 : 0;
        else estadoConocido[j] = sw.as<bool>() ? 1 : 0;
    }
    if (DISPOSITIVOS[i].tipo == TIPO_BOMBILLO) {
        int base = dpsEncendido(DISPOSITIVOS[i]);
        snprintf(clave, sizeof(clave), "%d", base + 2);
        JsonVariant b = dps[clave];
        if (b.is<int>()) brilloVisto = b.as<int>();
        snprintf(clave, sizeof(clave), "%d", base + 1);
        JsonVariant m = dps[clave];
        if (m.is<const char*>()) modoVisto = modoDeTuya(m.as<const char*>());
        snprintf(clave, sizeof(clave), "%d", base + 3);
        JsonVariant t = dps[clave];
        if (t.is<int>()) tempVisto = t.as<int>();
        snprintf(clave, sizeof(clave), "%d", base + 4);
        JsonVariant c = dps[clave];
        unsigned h, s, v;
        if (c.is<const char*>() && strlen(c.as<const char*>()) == 12 && sscanf(c.as<const char*>(), "%4x%4x%4x", &h, &s, &v) == 3) {
            hVisto = h; sVisto = s; vVisto = v;
        }
    }
}

//-------------------------------------------------------------
void publicarEstado(int i, bool verificado, const char* error) {
    const DispositivoTuya &d = DISPOSITIVOS[i];
    char tema[80], cuerpo[256];
    snprintf(tema, sizeof(tema), "domo/%d/estado/%s/%s", DOMO_ID, nombreTipo(d.tipo), d.idFisico);
    JsonDocument e;
    e["id_fisico"]  = d.idFisico;
    e["estado"]     = textoEstado(estadoConocido[i]);
    e["verificado"] = verificado;
    if (d.tipo == TIPO_BOMBILLO) {
        // En modo color el brillo que se ve es V (el DPS de brillo no cambia).
        int brillo = modoConocido[i] == MODO_COLOR && vConocido[i] >= 0 ? brilloDeTuya(vConocido[i]) : brilloConocido[i];
        if (brillo >= 0) e["brillo"] = brillo;
        if (modoConocido[i] >= 0) e["modo"] = nombreModo(modoConocido[i]);
        if (tempConocida[i] >= 0) e["temperatura"] = deEscalaTuya(tempConocida[i], TEMP_TUYA_MAX);
        if (hConocido[i] >= 0) {
            JsonObject c = e["color"].to<JsonObject>();
            c["h"] = hConocido[i];
            c["s"] = deEscalaTuya(sConocido[i], SAT_TUYA_MAX);
        }
    }
    if (error) e["error"] = error;
    serializeJson(e, cuerpo, sizeof(cuerpo));
    bool ok = mqtt.publish(tema, cuerpo, true);   // true = retenido
    Serial.print(ok ? "MQTT " : "MQTT (sin broker, no se publicó) "); Serial.print(tema); Serial.print(" -> "); Serial.println(cuerpo);
}

// Para un id_fisico que no está en secrets.h: se responde en su tema, si el id es seguro.
void publicarDesconocido(const char* tipo, const char* id) {
    if (!idSeguro(id)) return;
    char tema[80], cuerpo[160];
    snprintf(tema, sizeof(tema), "domo/%d/estado/%s/%s", DOMO_ID, tipo, id);
    snprintf(cuerpo, sizeof(cuerpo), "{\"id_fisico\":\"%s\",\"estado\":\"desconocido\",\"verificado\":false,\"error\":\"id_fisico_desconocido\"}", id);
    mqtt.publish(tema, cuerpo, true);
}

//-------------------------------------------------------------
// Atiende MQTT, la telemetría y el monitor serie. NUNCA habla con un dispositivo
// Tuya: se puede llamar en medio de una orden (TuyaLocal.ino lo hace al esperar).
void atenderFondo() {
    mqtt.loop();
    atenderTelemetria();
    atenderRecal();                 // recalibración del SoC (Sensores.ino): no habla con Tuya
    atenderDescubrimiento();
    atenderSerie();
}

void esperarAtendiendo(unsigned long ms) {
    unsigned long t0 = millis();
    while (millis() - t0 < ms) {
        atenderFondo();
        delay(5);
    }
}

// Espera entre reintentos. false si llegó otra orden para la misma entrada.
bool esperarReintento(unsigned long ms, int i) {
    unsigned long t0 = millis();
    while (millis() - t0 < ms) {
        atenderFondo();
        if (ordenNuevaPara[i]) return false;
        delay(10);
    }
    return true;
}

//-------------------------------------------------------------
// Una orden por ENTRADA (id_fisico): la nueva para la misma entrada reemplaza a la
// pendiente. Entradas distintas del mismo equipo (toma1_s2 y toma1_usb) son órdenes
// distintas: se ejecutan las dos, una detrás de otra (el loop ejecuta de a una, así
// que nunca hay dos conexiones abiertas al mismo equipo).
void encolarOrden(Orden o) {
    for (int k = 0; k < numOrdenes; k++) {
        if (ordenes[k].disp == o.disp) {             // la última manda; se combinan los campos
            if (o.accion >= 0) ordenes[k].accion = o.accion;
            if (o.brillo >= 0) ordenes[k].brillo = o.brillo;
            if (o.modo >= 0) {
                if (o.modo != ordenes[k].modo)       // otro modo: se descartan los campos del anterior
                    ordenes[k].temperatura = ordenes[k].h = ordenes[k].s = ordenes[k].escena = -1;
                ordenes[k].modo = o.modo;
                if (o.accion < 0 && ordenes[k].accion == 0) ordenes[k].accion = -1;   // el modo enciende: anula el "off" pendiente
            }
            if (o.temperatura >= 0) ordenes[k].temperatura = o.temperatura;
            if (o.h >= 0)           ordenes[k].h = o.h;
            if (o.s >= 0)           ordenes[k].s = o.s;
            if (o.escena >= 0)      ordenes[k].escena = o.escena;
            ordenes[k].desdeSerie = ordenes[k].desdeSerie || o.desdeSerie;
            ordenNuevaPara[o.disp] = true;
            return;
        }
    }
    if (numOrdenes == MAX_ORDENES_PENDIENTES) {
        Serial.println("Cola de órdenes llena: se descarta la más antigua.");
        for (int k = 1; k < numOrdenes; k++) ordenes[k - 1] = ordenes[k];
        numOrdenes--;
    }
    ordenes[numOrdenes++] = o;
    ordenNuevaPara[o.disp] = true;                    // por si se está ejecutando una de este mismo
}

int buscarDispositivo(const char* id, int tipo) {   // tipo -1 = cualquiera
    for (int i = 0; i < NUM_DISPOSITIVOS; i++)
        if (dispositivoValido[i] && (tipo < 0 || DISPOSITIVOS[i].tipo == tipo) && !strcmp(DISPOSITIVOS[i].idFisico, id)) return i;
    return -1;
}

//-------------------------------------------------------------
// Campos de luz opcionales de una orden de bombillo: "modo" ("blanco"|"color"|"escena"),
// "temperatura" (0-100), "color":{"h":0-360,"s":0-100} y "escena" (nombre en la tabla).
// Sin "modo", lo deducen temperatura (blanco), color o escena. false = orden inválida.
bool leerCamposDeLuz(JsonDocument &cmd, Orden &o) {
    const char* modo = cmd["modo"] | "";
    if (*modo) {
        if      (!strcmp(modo, "blanco")) o.modo = MODO_BLANCO;
        else if (!strcmp(modo, "color"))  o.modo = MODO_COLOR;
        else if (!strcmp(modo, "escena")) o.modo = MODO_ESCENA;
        else { Serial.printf("Modo desconocido: %s\n", modo); return false; }
    }
    if (!cmd["temperatura"].isNull()) {
        int t = cmd["temperatura"].is<int>() ? cmd["temperatura"].as<int>() : -1;
        if (t < 0 || t > 100) { Serial.println("Temperatura fuera de 0-100: se ignora la orden."); return false; }
        o.temperatura = t;
    }
    if (!cmd["color"].isNull()) {
        JsonVariant c = cmd["color"];
        int h = c["h"].is<int>() ? c["h"].as<int>() : -1, s = c["s"].is<int>() ? c["s"].as<int>() : -1;
        if (h < 0 || h > 360 || s < 0 || s > 100) { Serial.println("Color sin h 0-360 y s 0-100: se ignora la orden."); return false; }
        o.h = h; o.s = s;
    }
    if (!cmd["escena"].isNull()) {
        const char* nombre = cmd["escena"] | "";
        o.escena = buscarEscena(nombre);
        if (o.escena < 0) { Serial.printf("Escena desconocida: %s\n", nombre); return false; }
    }
    int grupos = (o.temperatura >= 0) + (o.h >= 0) + (o.escena >= 0);
    if (grupos > 1) { Serial.println("Campos de modos distintos en la misma orden: se ignora."); return false; }
    int deducido = o.escena >= 0 ? MODO_ESCENA : o.h >= 0 ? MODO_COLOR : o.temperatura >= 0 ? MODO_BLANCO : -1;
    if (o.modo < 0) o.modo = deducido;
    else if (deducido >= 0 && deducido != o.modo) { Serial.println("Los campos no corresponden al modo pedido: se ignora."); return false; }
    return true;
}

void onMqttMessage(char* topic, byte* payload, unsigned int length) {
    // IMPORTANTE: aquí NO se habla con el dispositivo (tarda segundos). Solo se anota la orden.
    if (!strcmp(topic, TOPIC_CMD_SOC)) {
        // Recalibración guiada: solo cuenta "accion"; un valor de SoC en el mensaje se ignora.
        JsonDocument cmd;
        DeserializationError err = deserializeJson(cmd, payload, length);
        if (err) { Serial.print("[RECAL] comando JSON invalido: "); Serial.println(err.c_str()); return; }
        const char* accion = cmd["accion"] | "";
        if (!pedirRecal(accion)) Serial.printf("[RECAL] accion desconocida: \"%s\"\n", accion);
        return;
    }
    uint8_t tipo;
    if      (!strcmp(topic, TOPIC_CMD_TOMA))     tipo = TIPO_TOMA;
    else if (!strcmp(topic, TOPIC_CMD_BOMBILLO)) tipo = TIPO_BOMBILLO;
    else return;

    JsonDocument cmd;
    DeserializationError err = deserializeJson(cmd, payload, length);
    if (err) { Serial.print("Comando JSON invalido: "); Serial.println(err.c_str()); return; }

    const char* id     = cmd["id_fisico"] | "";
    const char* accion = cmd["accion"] | "";
    const char* origen = cmd["origen"] | "?";
    Orden o = nuevaOrden(-1, false);

    o.disp = buscarDispositivo(id, tipo);
    if (o.disp < 0) {
        Serial.printf("Orden para %s \"%s\": no está en secrets.h (o sus datos no son válidos).\n", nombreTipo(tipo), id);
        publicarDesconocido(nombreTipo(tipo), id);
        return;
    }
    if      (!strcmp(accion, "on"))  o.accion = 1;
    else if (!strcmp(accion, "off")) o.accion = 0;
    else if (*accion) { Serial.print("Accion desconocida: "); Serial.println(accion); return; }

    if (tipo == TIPO_BOMBILLO && cmd["brillo"].is<int>()) {
        int b = cmd["brillo"].as<int>();
        if (b < 0 || b > 100) { Serial.println("Brillo fuera de 0-100: se ignora la orden."); return; }
        o.brillo = b;
    }
    if (tipo == TIPO_BOMBILLO && !leerCamposDeLuz(cmd, o)) return;
    if (o.accion < 0 && o.brillo < 0 && o.modo < 0) { Serial.println("Orden sin accion, brillo ni modo: se ignora."); return; }

    Serial.printf("Orden MQTT (%s) para %s: accion %s, brillo %d", origen, id,
                  o.accion < 0 ? "-" : o.accion ? "on" : "off", o.brillo);
    if (o.modo >= 0) Serial.printf(", modo %s, temperatura %d, color h %d s %d, escena %s", nombreModo(o.modo), o.temperatura,
                                   o.h, o.s, o.escena >= 0 ? ESCENAS_BOMBILLO[o.escena].nombre : "-");
    Serial.println();
    encolarOrden(o);
}

//-------------------------------------------------------------
void mqttReconnect() {
    if (mqtt.connected()) return;
    if (millis() - lastMqttTry < 5000) return;          // reintenta cada 5 s sin bloquear
    lastMqttTry = millis();

    Serial.print("Conectando a Mosquitto... ");
    // Last Will: si el ESP32 se cae, el broker publica "offline" (retenido)
    if (mqtt.connect("esp32-domo-1", nullptr, nullptr, TOPIC_ESTADO_ESP, 0, true, "offline")) {
        Serial.println("conectado.");
        mqtt.publish(TOPIC_ESTADO_ESP, "online", true);
        mqtt.subscribe(TOPIC_CMD_TOMA);
        mqtt.subscribe(TOPIC_CMD_BOMBILLO);
        mqtt.subscribe(TOPIC_CMD_SOC, 1);               // QoS 1
    } else {
        Serial.print("fallo, rc="); Serial.println(mqtt.state());
    }
}

//-------------------------------------------------------------
// El contenido de "dps":{...} de la orden, en `out`. Devuelve su largo.
// Bombillo: el brillo va SIEMPRE con el modo blanco en la misma orden
// ({"21":"white","22":valor}): en modo color el DPS de brillo no cambia lo que se ve.
int armarDps(const DispositivoTuya &d, const Orden &o, char* out, size_t tam) {
    int dps = dpsEncendido(d);
    int n = 0;
    if (d.tipo == TIPO_TOMA) {
        return snprintf(out, tam, "\"%d\":%s", dps, o.accion ? "true" : "false");
    }
    if (o.modo >= 0 && o.accion != 0) return armarDpsModo(dps, o, out, tam);
    if (o.accion >= 0) n += snprintf(out + n, tam - n, "\"%d\":%s", dps, o.accion ? "true" : "false");
    if (o.brillo >= 0 && o.accion != 0) {
        if (n) n += snprintf(out + n, tam - n, ",");
        n += snprintf(out + n, tam - n, "\"%d\":\"white\",\"%d\":%d", dps + 1, dps + 2, brilloATuya(o.brillo));
    }
    return n;
}

// Agrega texto a `out` (que ya tiene n caracteres). -1 si no cabe (o si ya no cabía).
int agregarDps(char* out, size_t tam, int n, const char* formato, ...) {
    if (n < 0 || (size_t)n >= tam) return -1;
    va_list args;
    va_start(args, formato);
    int m = vsnprintf(out + n, tam - n, formato, args);
    va_end(args);
    return m < 0 || (size_t)(n + m) >= tam ? -1 : n + m;
}

// El color que se manda en modo color, en escala Tuya. H y S que no vienen: los últimos
// confirmados; V = brillo pedido, o el último V confirmado, o 1000. false si no se sabe H o S.
bool colorPedido(const Orden &o, int &h, int &s, int &v) {
    h = o.h >= 0 ? o.h : hConocido[o.disp];
    s = o.s >= 0 ? satATuya(o.s) : sConocido[o.disp];
    v = o.brillo >= 0 ? brilloATuya(o.brillo) : vConocido[o.disp] >= 0 ? vConocido[o.disp] : BRILLO_TUYA_MAX;
    return h >= 0 && s >= 0;
}

// Bombillo con modo: siempre enciende y fija el modo, más lo de ese modo.
//   blanco: "22" brillo y "23" temperatura, si vienen
//   color:  "24" HHHHSSSSVVVV (sin H o S conocidos: solo el modo)
//   escena: "25" texto de la tabla (sin escena: solo el modo, retoma la última)
// -1 si no cabe en `out`.
int armarDpsModo(int dps, const Orden &o, char* out, size_t tam) {
    int n = agregarDps(out, tam, 0, "\"%d\":true,\"%d\":\"%s\"", dps, dps + 1, modoTuya(o.modo));
    if (o.modo == MODO_BLANCO) {
        if (o.brillo >= 0)      n = agregarDps(out, tam, n, ",\"%d\":%d", dps + 2, brilloATuya(o.brillo));
        if (o.temperatura >= 0) n = agregarDps(out, tam, n, ",\"%d\":%d", dps + 3, tempATuya(o.temperatura));
    } else if (o.modo == MODO_COLOR) {
        int h, s, v;
        if (colorPedido(o, h, s, v)) n = agregarDps(out, tam, n, ",\"%d\":\"%04x%04x%04x\"", dps + 4, h, s, v);
        else Serial.println("  -> sin H y S conocidos: solo se pide el modo color.");
    } else if (o.modo == MODO_ESCENA && o.escena >= 0) {
        n = agregarDps(out, tam, n, ",\"%d\":\"%s\"", dps + 5, ESCENAS_BOMBILLO[o.escena].datos);
    }
    return n;
}

//-------------------------------------------------------------
// UN intento completo: conectar, negociar la clave (3.4/3.5), mandar la orden y, si
// las respuestas no traen el DPS de esta entrada, consultar el estado en la misma conexión.
bool intentoOrden(const Orden &o, uint8_t ip[4], uint8_t version) {
    const DispositivoTuya &d = DISPOSITIVOS[o.disp];
    bool conModo = d.tipo == TIPO_BOMBILLO && o.modo >= 0 && o.accion != 0;
    bool brilloPedido = d.tipo == TIPO_BOMBILLO && o.brillo >= 0 && o.accion != 0 && (!conModo || o.modo == MODO_BLANCO);
    int accionPedida = conModo ? 1 : o.accion;        // con modo, la orden siempre enciende
    int h = -1, s = -1, v = -1;
    bool colorEnviado = conModo && o.modo == MODO_COLOR && colorPedido(o, h, s, v);
    switchVisto = -1; brilloVisto = -1; modoVisto = -1; tempVisto = -1; hVisto = -1; sVisto = -1; vVisto = -1;

    char dps[256];
    if (armarDps(d, o, dps, sizeof(dps)) < 0) { Serial.println("  -> la orden no cabe en el buffer de DPS."); return false; }

    int pedidos = 0;
    if (accionPedida >= 0) pedidos |= FALTA_SWITCH;
    if (brilloPedido)      pedidos |= FALTA_BRILLO;
    if (conModo)           pedidos |= FALTA_MODO;
    if (conModo && o.modo == MODO_BLANCO && o.temperatura >= 0) pedidos |= FALTA_TEMP;
    if (colorEnviado)      pedidos |= FALTA_COLOR;

    if (!tuyaConectar(o.disp, ip, version)) { tuyaCerrar(o.disp); return false; }

    if (tuyaOrden(o.disp, dps, pedidos)) {
        int faltan = faltanDps(pedidos);
        if (faltan) {
            Serial.println("  -> la respuesta no trajo el DPS de esta entrada; consultando el estado...");
            tuyaConsulta(o.disp, faltan);
        }
    }
    tuyaCerrar(o.disp);

    if (switchVisto >= 0) estadoConocido[o.disp] = switchVisto;
    if (brilloVisto >= 0) brilloConocido[o.disp] = brilloDeTuya(brilloVisto);
    if (modoVisto >= 0)   modoConocido[o.disp] = modoVisto;
    if (tempVisto >= 0)   tempConocida[o.disp] = tempVisto;
    if (hVisto >= 0) { hConocido[o.disp] = hVisto; sConocido[o.disp] = sVisto; vConocido[o.disp] = vVisto; }

    if (accionPedida >= 0 && switchVisto != accionPedida) {
        Serial.printf("  -> DPS %d reportado: %s, distinto de lo pedido.\n", dpsEncendido(d), textoEstado(switchVisto));
        return false;
    }
    if (brilloPedido && (brilloVisto < 0 || abs(brilloVisto - brilloATuya(o.brillo)) > (conModo ? 2 : 1))) {
        Serial.printf("  -> brillo reportado %d (escala Tuya), pedido %d.\n", brilloVisto, brilloATuya(o.brillo));
        return false;
    }
    if (conModo && modoVisto != o.modo) {
        Serial.printf("  -> modo reportado %s, pedido %s.\n", modoVisto < 0 ? "-" : nombreModo(modoVisto), nombreModo(o.modo));
        return false;
    }
    if ((pedidos & FALTA_TEMP) && (tempVisto < 0 || abs(tempVisto - tempATuya(o.temperatura)) > 2)) {
        Serial.printf("  -> temperatura reportada %d (escala Tuya), pedida %d.\n", tempVisto, tempATuya(o.temperatura));
        return false;
    }
    if (colorEnviado && (hVisto < 0 || abs(hVisto - h) > 2 || abs(sVisto - s) > 2 || abs(vVisto - v) > 2)) {
        Serial.printf("  -> color reportado H %d S %d V %d, pedido H %d S %d V %d (escala Tuya).\n", hVisto, sVisto, vVisto, h, s, v);
        return false;
    }
    return true;
}

//-------------------------------------------------------------
void ejecutarOrden(const Orden &o) {
    const DispositivoTuya &d = DISPOSITIVOS[o.disp];
    ordenNuevaPara[o.disp] = false;
    Serial.printf("Ejecutando orden para %s (%s, DPS %d): accion %s, brillo %d", d.idFisico, nombreTipo(d.tipo),
                  dpsEncendido(d), o.accion < 0 ? "-" : o.accion ? "on" : "off", o.brillo);
    if (o.modo >= 0) Serial.printf(", modo %s, temperatura %d, color h %d s %d, escena %s", nombreModo(o.modo), o.temperatura,
                                   o.h, o.s, o.escena >= 0 ? ESCENAS_BOMBILLO[o.escena].nombre : "-");
    Serial.println();
    unsigned long inicio = millis();

    uint8_t ip[4];
    uint8_t version;
    if (!resolverDireccion(o.disp, ip, &version)) {
        Serial.printf("ERROR: %s no tiene IP o versión: van en 0 en secrets.h y no se ha anunciado por la red "
                      "(comando \"red\"). No se intentó.\n", d.idFisico);
        publicarEstado(o.disp, false, "ip_desconocida");
        if (o.desdeSerie) Serial.printf("probar %s: NO VERIFICADO (ip_desconocida)\n", d.idFisico);
        return;
    }

    for (int intento = 1; intento <= MAX_INTENTOS_ORDEN; intento++) {
        Serial.printf("Intento %d de %d (v3.%d, %d.%d.%d.%d)\n", intento, MAX_INTENTOS_ORDEN, version % 10, ip[0], ip[1], ip[2], ip[3]);
        unsigned long t1 = millis();
        bool ok = intentoOrden(o, ip, version);
        Serial.printf("  tiempo del intento: %lu ms\n", millis() - t1);

        if (ok) {
            Serial.println("VERIFICADO: el dispositivo confirmo lo pedido.");
            publicarEstado(o.disp, true, nullptr);
            if (o.desdeSerie) Serial.printf("probar %s: VERIFICADO, estado %s%s%s, %lu ms (intento %d)\n", d.idFisico,
                                            textoEstado(estadoConocido[o.disp]), o.modo >= 0 ? ", modo " : "",
                                            o.modo >= 0 ? nombreModo(o.modo) : "", millis() - inicio, intento);
            return;
        }
        if (intento < MAX_INTENTOS_ORDEN) {
            Serial.println("  -> reintento en 1,5 s...");
            if (!esperarReintento(PAUSA_REINTENTO_MS, o.disp)) {
                // Llegó otra orden para esta entrada: esa manda y publica su resultado.
                Serial.println("Llego una orden nueva: se abandona esta sin publicar.");
                return;
            }
        }
    }

    // Todos los intentos fallaron: NO se publica lo pedido, sino lo último confirmado.
    Serial.printf("ERROR: %s no confirmo la orden tras %d intentos. Se publica el ultimo estado conocido: %s\n",
                  d.idFisico, MAX_INTENTOS_ORDEN, textoEstado(estadoConocido[o.disp]));
    publicarEstado(o.disp, false, d.tipo == TIPO_BOMBILLO ? "sin_conexion_bombillo" : "sin_conexion_toma");
    if (o.desdeSerie) Serial.printf("probar %s: NO VERIFICADO tras %d intentos, %lu ms. Último estado confirmado: %s\n",
                                    d.idFisico, MAX_INTENTOS_ORDEN, millis() - inicio, textoEstado(estadoConocido[o.disp]));
}

//-------------------------------------------------------------
// Comandos del monitor serie de Tuya (los demás están en Sensores.ino). true si era uno de estos.
void imprimirLinea(const char* linea) { Serial.println(linea); }

bool comandoSerieTuya(const char* linea) {
    if (!strcmp(linea, "autotest")) {
        Serial.println("autotest: protocolo Tuya contra los vectores de tinytuya (sin red, claves ficticias)...");
        unsigned long t0 = millis();
        tuya::autotest(imprimirLinea);
        Serial.printf("autotest: %lu ms\n", millis() - t0);
        return true;
    }
    if (strncmp(linea, "probar", 6) || (linea[6] != ' ' && linea[6] != '\0')) return false;

    char id[40] = "", accion[8] = "", escena[16] = "";
    int a = -1, b = -1, c = -1;
    int leidos = sscanf(linea + 6, " %39s %7s", id, accion);
    bool esOnOff = leidos == 2 && (!strcmp(accion, "on") || !strcmp(accion, "off"));
    bool esBlanco = leidos == 2 && !strcmp(accion, "blanco") && sscanf(linea + 6, " %*s %*s %d %d", &a, &b) == 2
                    && a >= 0 && a <= 100 && b >= 0 && b <= 100;
    bool esColor = leidos == 2 && !strcmp(accion, "color") && sscanf(linea + 6, " %*s %*s %d %d %d", &a, &b, &c) == 3
                   && a >= 0 && a <= 360 && b >= 0 && b <= 100 && c >= 0 && c <= 100;
    bool esEscena = leidos == 2 && !strcmp(accion, "escena") && sscanf(linea + 6, " %*s %*s %15s", escena) == 1
                    && buscarEscena(escena) >= 0;
    if (!esOnOff && !esBlanco && !esColor && !esEscena) {
        Serial.println("Uso: probar <id_fisico> on|off   (los id_fisico salen con el comando \"estado\")");
        Serial.println("     probar <bombillo> blanco <temp 0-100> <brillo 0-100>   (temp 0 = más cálido)");
        Serial.println("     probar <bombillo> color <h 0-360> <s 0-100> <brillo 0-100>");
        Serial.println("     probar <bombillo> escena fiesta|arcoiris|romantico|disco|estrobo|vela|atardecer|oceano");
        return true;
    }
    int i = buscarDispositivo(id, -1);
    if (i < 0) { Serial.printf("probar: \"%s\" no está en secrets.h o está desactivado.\n", id); return true; }
    if (!esOnOff && DISPOSITIVOS[i].tipo != TIPO_BOMBILLO) { Serial.printf("probar: \"%s\" no es un bombillo.\n", id); return true; }
    Orden o = nuevaOrden(i, true);
    if (esOnOff)  o.accion = strcmp(accion, "on") ? 0 : 1;
    if (esBlanco) { o.modo = MODO_BLANCO; o.temperatura = a; o.brillo = b; }
    if (esColor)  { o.modo = MODO_COLOR; o.h = a; o.s = b; o.brillo = c; }
    if (esEscena) { o.modo = MODO_ESCENA; o.escena = buscarEscena(escena); }
    Serial.printf("probar %s %s: en cola (se ejecuta en el loop; el resultado sale como \"probar %s: ...\").\n", id, accion, id);
    encolarOrden(o);
    return true;
}

//-------------------------------------------------------------
void listarDispositivos() {
    Serial.printf("Dispositivos Tuya (%d entradas). Ni la local key ni el ID completo se muestran:\n", NUM_DISPOSITIVOS);
    for (int i = 0; i < NUM_DISPOSITIVOS; i++) {
        const DispositivoTuya &d = DISPOSITIVOS[i];
        size_t nid = d.tuyaId ? strlen(d.tuyaId) : 0;
        uint8_t ip[4] = { 0, 0, 0, 0 };
        uint8_t version = 0;
        bool hay = dispositivoValido[i] && resolverDireccion(i, ip, &version);
        const char* origen = !hay ? "SIN IP O VERSIÓN (aún no se anuncia)" : ipEscrita(d) ? "escrita" : "descubierta";
        Serial.printf("  %-10s %-8s DPS %-3d %d.%d.%d.%d (%s)  v3.%d%s  ID ...%s  %s  estado %s\n", d.idFisico ? d.idFisico : "?",
                      nombreTipo(d.tipo), dpsEncendido(d), ip[0], ip[1], ip[2], ip[3], origen, version % 10,
                      d.version ? "" : " (anunciada)", nid > 4 ? d.tuyaId + nid - 4 : "",
                      dispositivoValido[i] ? "ok" : "DESACTIVADO", textoEstado(estadoConocido[i]));
        uint8_t ipAnunciada[4];
        int versionAnunciada;
        if (dispositivoValido[i] && ipEscrita(d) && direccionDescubierta(d.tuyaId, ipAnunciada, &versionAnunciada) && memcmp(ipAnunciada, d.ip, 4))
            Serial.printf("             ATENCIÓN: se anuncia en %d.%d.%d.%d, distinta de la escrita. Manda la escrita.\n",
                          ipAnunciada[0], ipAnunciada[1], ipAnunciada[2], ipAnunciada[3]);
    }
}

// Por qué la entrada i no sirve, o nullptr si sirve.
const char* problemaDispositivo(int i) {
    const DispositivoTuya &d = DISPOSITIVOS[i];
    if (!idSeguro(d.idFisico)) return "id_fisico vacío, de más de 32 caracteres o con caracteres fuera de letras, números, _ y -";
    if (d.tipo != TIPO_TOMA && d.tipo != TIPO_BOMBILLO) return "tipo distinto de TIPO_TOMA y TIPO_BOMBILLO";
    if (!d.tuyaId || !*d.tuyaId || strlen(d.tuyaId) >= sizeof(((Descubierto*)0)->gwId)) return "falta el ID de Tuya (o es demasiado largo)";
    if (!d.localKey || strlen(d.localKey) != tuya::LARGO_CLAVE) return "la local key no tiene 16 caracteres";
    if (d.version != 0 && d.version != 33 && d.version != 34 && d.version != 35) return "versión distinta de 0, 33, 34 o 35";
    if ((!ipEscrita(d) || d.version == 0) && !DESCUBRIR_TUYA) return "IP o versión en 0 con el descubrimiento apagado (DESCUBRIR_TUYA 0)";
    for (int j = 0; j < i; j++) {
        const DispositivoTuya &o = DISPOSITIVOS[j];
        if (o.idFisico && !strcmp(o.idFisico, d.idFisico)) return "id_fisico repetido";
        if (!o.tuyaId || strcmp(o.tuyaId, d.tuyaId)) continue;
        // Otra salida del mismo equipo: mismos datos de conexión, otro DPS.
        if (dpsEncendido(o) == dpsEncendido(d)) return "mismo ID de Tuya y mismo DPS que otra entrada";
        if (memcmp(o.ip, d.ip, 4) || o.version != d.version || !o.localKey || strcmp(o.localKey, d.localKey))
            return "mismo ID de Tuya que otra entrada pero distinta IP, versión o local key";
    }
    return nullptr;
}

void validarDispositivos() {
    for (int i = 0; i < NUM_DISPOSITIVOS; i++) {
        const char* problema = problemaDispositivo(i);
        dispositivoValido[i] = problema == nullptr;
        estadoConocido[i] = -1; brilloConocido[i] = -1; ordenNuevaPara[i] = false;
        modoConocido[i] = -1; tempConocida[i] = -1; hConocido[i] = -1; sConocido[i] = -1; vConocido[i] = -1;
        if (problema) Serial.printf("ATENCION: la entrada %d (%s) de DISPOSITIVOS_TUYA queda desactivada: %s.\n",
                                    i, DISPOSITIVOS[i].idFisico ? DISPOSITIVOS[i].idFisico : "?", problema);
    }
    listarDispositivos();
}

//-------------------------------------------------------------
void setup() {

    Serial.begin(115200);

    Serial.println("********** Debut Program **********");
    Serial.println(thisPgm);
    Serial.println("***********************************");

    validarDispositivos();
    iniciarSensores();        // antes del WiFi: los ceros se calibran sin esperar la red

    //------------------------- Acceso WiFi
        WiFi.onEvent(eventoWiFi);
        WiFi.mode(WIFI_STA);
        WiFi.begin(ssid, password);
        Serial.print("Connecting to WiFi ..");
        while (WiFi.status() != WL_CONNECTED) {
          delay(500);
          Serial.print(".");
        }
        Serial.println();
        Serial.print("WiFi conectado. IP del ESP32: ");
        Serial.println(WiFi.localIP());
        WiFi.setSleep(false);
        iniciarDescubrimiento();

    //------------------------- Hora Unix por NTP (va en el campo "t" de cada orden Tuya)
        configTime(0, 0, "time.google.com", "pool.ntp.org", "time.cloudflare.com");
        time_t now = time(nullptr);
        int intentos = 0;
        while (now < 1700000000 && intentos < 40) {   // espera hasta ~20 s a que se sincronice
          delay(500);
          Serial.print("*");
          now = time(nullptr);
          intentos++;
        }
        Serial.println();
        if (now < 1700000000) {
          Serial.println("ATENCION: no se pudo obtener la hora por NTP.");
        } else {
          Serial.print("Hora Unix sincronizada: ");
          Serial.println((unsigned long) now);
        }

    //------------------------- MQTT
        mqtt.setServer(MQTT_BROKER_IP, 1883);
        mqtt.setCallback(onMqttMessage);
        mqtt.setKeepAlive(60);
        mqtt.setSocketTimeout(3);              // un broker caído no frena el loop 15 s
        mqtt.setBufferSize(512);

    ayudaSerie();
    Serial.println("*** End Setup ***");
}

//-------------------------------------------------------------
// --- Autorrecuperación de red ---------------------------------------
static uint32_t ultimoMqttOk = 0;
static bool wifiReiniciado = false;
static uint32_t ultimoIntentoWiFi = 0;                  // último WiFi.begin (loop y vigilarRed)

void eventoWiFi(WiFiEvent_t evento, WiFiEventInfo_t info) {
    if (evento == ARDUINO_EVENT_WIFI_STA_DISCONNECTED)
        Serial.printf("[WiFi] desconectado, motivo=%d\n", info.wifi_sta_disconnected.reason);
}

// Ajustes del Wi-Fi que hay que repetir cada vez que se reinicia la radio
void ajustesWiFi() {
    WiFi.setSleep(false);
}

void vigilarRed() {
    uint32_t ahora = millis();
    if (mqtt.connected()) {
        ultimoMqttOk = ahora;
        wifiReiniciado = false;
        return;
    }
    if (ultimoMqttOk == 0) ultimoMqttOk = ahora;
    uint32_t sinMqtt = ahora - ultimoMqttOk;

    if (sinMqtt > 100000UL) {
        Serial.println("[RED] 100 s sin MQTT: reinicio la ESP32");
        Serial.flush();
        delay(200);
        ESP.restart();
    } else if (sinMqtt > 40000UL && !wifiReiniciado) {
        Serial.printf("[RED] 40 s sin MQTT (Wi-Fi %s, RSSI %d dBm, memoria libre %u B): reinicio el Wi-Fi\n",
                      WiFi.status() == WL_CONNECTED ? "asociado" : "caido", WiFi.RSSI(), (unsigned)ESP.getFreeHeap());
        wifiReiniciado = true;
        WiFi.disconnect();                              // sin apagar la radio
        WiFi.begin(ssid, password);
        ultimoIntentoWiFi = millis();                   // loop no repite el begin antes de 20 s
    }
}

// Cada 30 s: memoria y señal, para ver si algo se va agotando con el tiempo
void reporteSalud() {
    static uint32_t ultimo = 0;
    if (millis() - ultimo < 30000UL) return;
    ultimo = millis();
    Serial.printf("[SALUD] activa %lu s | memoria libre %u B (minima %u B, mayor bloque %u B) | RSSI %d dBm | MQTT %s\n",
                  (unsigned long)(millis() / 1000), (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap(),
                  (unsigned)ESP.getMaxAllocHeap(), WiFi.RSSI(), mqtt.connected() ? "ok" : "caido");
}

void loop() {

    atenderSerie();                                      // la calibración y el autotest funcionan aun sin WiFi
    vigilarRed();
    reporteSalud();

    static bool reconectando = false;
    if (WiFi.status() != WL_CONNECTED) {                // WiFi caido: reintenta cada 20 s sin cortar el intento anterior
        reconectando = true;
        if (millis() - ultimoIntentoWiFi > 20000UL) {
            ultimoIntentoWiFi = millis();
            WiFi.begin(ssid, password);
        }
        esperarAtendiendo(500);
        return;
    }
    if (reconectando) {                                 // volvió: se reabren los sockets UDP
        reconectando = false;
        ajustesWiFi();
        iniciarDescubrimiento();
    }

    mqttReconnect();
    atenderFondo();

    if (numOrdenes > 0) {
        Orden o = ordenes[0];
        for (int k = 1; k < numOrdenes; k++) ordenes[k - 1] = ordenes[k];
        numOrdenes--;
        ejecutarOrden(o);
    }
}

//-------------------------------------------------------------
bool ipEscrita(const DispositivoTuya &d) {
    return d.ip[0] || d.ip[1] || d.ip[2] || d.ip[3];
}

// IP y versión con las que se habla con la entrada i: lo escrito en secrets.h
// manda; lo que esté en 0 sale de la tabla de descubrimiento. false si falta algo.
bool resolverDireccion(int i, uint8_t ip[4], uint8_t* version) {
    const DispositivoTuya &d = DISPOSITIVOS[i];
    uint8_t ipAnunciada[4];
    int versionAnunciada = 0;
    bool anunciado = direccionDescubierta(d.tuyaId, ipAnunciada, &versionAnunciada);
    if (ipEscrita(d)) memcpy(ip, d.ip, 4);
    else if (anunciado) memcpy(ip, ipAnunciada, 4);
    else return false;
    if (d.version) *version = d.version;
    else if (anunciado && (versionAnunciada == 33 || versionAnunciada == 34 || versionAnunciada == 35)) *version = versionAnunciada;
    else return false;
    return true;
}
