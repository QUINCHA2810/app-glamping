/*
 * APP-GLAMPING — Prueba de conexión WiFi del ESP32
 *
 * Primer paso del firmware: solo confirma que el ESP32 se conecta a la
 * red WiFi y obtiene una dirección IP. No publica nada por MQTT
 * todavía — eso es el siguiente paso, una vez esto funcione.
 */

#include <WiFi.h>
#include "secrets.h"

// ---- CONFIGURA ESTOS DOS VALORES CON LOS DATOS DE TU RED WIFI ----
const char* WIFI_SSID = SECRET_WIFI_SSID;
const char* WIFI_PASSWORD = SECRET_WIFI_PASSWORD;
// --------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.print("Conectando a WiFi: ");
  Serial.println(WIFI_SSID);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("¡Conectado a WiFi!");
  Serial.print("Direccion IP del ESP32: ");
  Serial.println(WiFi.localIP());
}

void loop() {
  // Por ahora no hace nada mas -- solo confirmamos la conexion.
}
