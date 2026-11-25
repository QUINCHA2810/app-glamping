/*
 * APP-GLAMPING -- Publicacion de telemetria de prueba por MQTT
 *
 * Segundo paso: ademas de conectarse a WiFi (ya confirmado en el paso
 * anterior), este sketch se conecta a Mosquitto y publica una lectura
 * cada 10 segundos en el tema domo/1/telemetria/fotovoltaica -- con
 * datos INVENTADOS por ahora, no de sensores reales todavia. Esto
 * confirma que la tuberia completa funciona:
 *
 *   ESP32 -> Mosquitto -> mqtt_listener.py (backend) -> Supabase
 *
 * antes de cablear ningun sensor real.
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "secrets.h"

// ---- WIFI: usa los MISMOS datos que ya confirmaste que funcionan ----
const char* WIFI_SSID = SECRET_WIFI_SSID;
const char* WIFI_PASSWORD = SECRET_WIFI_PASSWORD;
const char* MQTT_BROKER = SECRET_MQTT_BROKER;

// ---- MQTT: la IP de tu computador en la red local (donde corre Mosquitto) ----

const int MQTT_PORT = 1883;
const char* MQTT_TOPIC = "domo/1/telemetria/fotovoltaica";

WiFiClient espClient;
PubSubClient mqttClient(espClient);

void conectarWiFi() {
  Serial.print("Conectando a WiFi: ");
  Serial.println(WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("WiFi conectado. IP: ");
  Serial.println(WiFi.localIP());
}

void conectarMQTT() {
  while (!mqttClient.connected()) {
    Serial.print("Conectando a Mosquitto...");
    // ID de cliente unico -- si dos dispositivos usaran el mismo id,
    // Mosquitto desconectaria uno al conectarse el otro.
    String clientId = "ESP32-domo1-" + String(random(0xffff), HEX);
    if (mqttClient.connect(clientId.c_str())) {
      Serial.println(" conectado.");
    } else {
      Serial.print(" fallo, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" -- reintentando en 5s");
      delay(5000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  conectarWiFi();
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    conectarWiFi();
  }
  if (!mqttClient.connected()) {
    conectarMQTT();
  }
  mqttClient.loop();

  static unsigned long ultimoEnvio = 0;
  if (millis() - ultimoEnvio > 10000) {  // cada 10 segundos
    ultimoEnvio = millis();

    // Datos INVENTADOS por ahora -- se reemplazan por lecturas reales
    // de sensores cuando esten cableados.
    JsonDocument doc;
    doc["voltaje"] = 24.6;
    doc["corriente"] = 1.8;
    doc["potencia_generada"] = 44.3;
    doc["energia_generada"] = 0.82;
    doc["estado_generacion"] = "generando";

    char payload[256];
    serializeJson(doc, payload);

    Serial.print("Publicando: ");
    Serial.println(payload);

    mqttClient.publish(MQTT_TOPIC, payload);
  }
}
