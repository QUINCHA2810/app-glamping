/*
  Sensores del domo: ACS712 del panel y del inversor, voltaje de la batería y
  PZEM-004T. Configuración en sensores_config.h; procedimiento en firmware/README.md.

  - Una tarea de FreeRTOS (tareaSensores) muestrea cada PERIODO_BLOQUE_MS: un
    bloque de MUESTRAS_POR_BLOQUE lecturas por canal con analogReadMilliVolts
    (atenuación 11 dB, calibrada de fábrica), promediadas y suavizadas con un
    filtro exponencial; lee el PZEM cada PERIODO_PZEM_MS e integra la energía.
    Así ni las órdenes a las tomas (4-6 s) ni MQTT frenan las mediciones.
  - El loop publica cada PERIODO_TELEMETRIA_MS (atenderTelemetria), también
    durante una orden a una toma, porque Functions.ino espera con esperarAtendiendo().
  - La calibración (ceros de los ACS712, factor del divisor de la batería y
    voltaje del panel) se guarda en la flash con Preferences.
*/

#include <Preferences.h>
#include <PZEM004Tv30.h>
#include "soc_estimador.h"
#include "soc_recalibracion.h"

// Corriente del panel por debajo de esto (en valor absoluto): 0 A, para publicar y para
// integrar la energía. Se suma a ACS_PV_BANDA_MUERTA_A de sensores_config.h.
#define ZONA_MUERTA_PV_A        0.20f

// SoC en flash (mismo espacio "sensores" que la calibración): se guarda cada 5 min si
// cambió al menos 0,5 puntos, y al arrancar se restaura SIEMPRE que haya uno válido, sin
// límite de edad (soc::decidirArranque). Solo sin guardado se usa la tabla de voltaje.
#define SOC_GUARDAR_CADA_MS   300000UL
#define SOC_GUARDAR_DELTA         0.5f
#define HORA_MINIMA_VALIDA  1700000000UL

Preferences prefs;
#if USAR_PZEM
PZEM004Tv30 pzem(Serial2, PIN_PZEM_RX, PIN_PZEM_TX);
#endif

Lecturas lecturas = {};
portMUX_TYPE muxLecturas = portMUX_INITIALIZER_UNLOCKED;

// Calibración vigente (la tarea la lee; el loop la cambia por comandos serie).
volatile float ceroPvMv  = ACS_CERO_NOMINAL_MV;   // en el lado del sensor (pin × divisor)
volatile float ceroInvMv = ACS_CERO_NOMINAL_MV;
volatile float factorVbat = VBAT_FACTOR_DIVISOR;
volatile float voltajePv  = VOLTAJE_PV_NOMINAL;
// Ganancia de cada ACS712 (1 = la sensibilidad nominal). La ajustan "ipv"/"iinv" con una pinza.
volatile float gananciaPv  = 1.0f;
volatile float gananciaInv = 1.0f;
volatile bool  pedirCero  = false;                // lo atiende la tarea, que es la dueña del ADC

// SoC (soc_estimador.h): conteo de energía y corrección por voltaje solo en reposo real.
const soc::Config configSoc = {
    BATERIA_CAPACIDAD_WH, EFICIENCIA_CARGA, EFICIENCIA_DESCARGA,
    REPOSO_REAL_IPV_A, REPOSO_REAL_IBAT_A, REPOSO_REAL_S, REPOSO_DT_MAX_S, SIN_CARGA_S,
    TAU_CORRECCION_S, TAU_ESTADO_S, P_ESTABLE_W, R_COMPENSACION_OHM,
    V_FLOTACION_MIN, SOC_CARGADA_MIN,
    SOC_TABLA_V, SOC_TABLA_PCT, SOC_TABLA_PUNTOS,
    V_CARGA_COMPLETA, I_COLA_MAX_A, CARGA_COMPLETA_S,
};
soc::Estimador estSoc;
soc::Estado   estadoSoc = soc::Estado::Ninguno;   // el último publicado
float    socGuardado = NAN;                // último SoC escrito (o restaurado) en flash
unsigned long ultimoGuardadoSoc = 0;
unsigned long socEsperaNtpDesde = 0;       // al arrancar sin hora NTP

// Recalibración guiada (soc_recalibracion.h): la pide la app por domo/{id}/comando/soc.
const soc::recal::Config configRecal = {
    RECAL_IPV_MAX_A, RECAL_IINV_MAX_A, RECAL_IBAT_MAX_A,
    RECAL_REPOSO_MIN * 60.0f, RECAL_MAX_MIN * 60.0f, RECAL_PROPUESTA_MIN * 60.0f, REPOSO_DT_MAX_S,
};
soc::recal::Recal recalSoc;
// Pedido de la app (lo anota el callback de MQTT, lo ejecuta atenderRecal en el loop).
#define RECAL_NADA       0
#define RECAL_INICIAR    1
#define RECAL_CONFIRMAR  2
#define RECAL_CANCELAR   3
volatile int pedidoRecal = RECAL_NADA;
// Al arrancar se publica "inactiva" (retenido): reemplaza un "esperando"/"lista" de antes
// del reinicio, que ya no existe. También se republica en cada reconexión al broker.
bool recalPorPublicar = true;
unsigned long ultimaPublicacionRecal = 0;
int  muestrasRecal = 0;
unsigned long ultimaTelemetria = 0;
unsigned long ultimosSensoresPzem = 0;
unsigned long ultimaVista = 0;
bool     modoCalibracion = false;

//-------------------------------------------------------------
void iniciarSensores() {
    prefs.begin("sensores", false);
    ceroPvMv   = prefs.getFloat("ceroPv",  ACS_CERO_NOMINAL_MV);
    ceroInvMv  = prefs.getFloat("ceroInv", ACS_CERO_NOMINAL_MV);
    factorVbat = prefs.getFloat("fVbat",   VBAT_FACTOR_DIVISOR);
    voltajePv  = prefs.getFloat("vPv",     VOLTAJE_PV_NOMINAL);
    gananciaPv  = prefs.getFloat("gPv",  1.0f);
    gananciaInv = prefs.getFloat("gInv", 1.0f);

    analogReadResolution(12);
    if (USAR_ACS_PV)       analogSetPinAttenuation(PIN_ACS_PV, ADC_11db);
    if (USAR_ACS_INVERSOR) analogSetPinAttenuation(PIN_ACS_INVERSOR, ADC_11db);
    if (USAR_VBAT)         analogSetPinAttenuation(PIN_VBAT, ADC_11db);

    Serial.printf("Calibración guardada: cero PV %.1f mV, cero inversor %.1f mV, ganancias %.3f / %.3f, factor batería %.4f, voltaje PV %.1f V\n",
                  ceroPvMv, ceroInvMv, gananciaPv, gananciaInv, factorVbat, voltajePv);
    if (CALIBRAR_CERO_AL_ARRANCAR) calibrarCeros(false);

#if USAR_PZEM
    Serial.printf("PZEM-004T en UART2 (RX %d, TX %d).\n", PIN_PZEM_RX, PIN_PZEM_TX);
#endif
    // Núcleo 0, prioridad baja: el loop de Arduino (MQTT y Tuya) corre en el 1.
    xTaskCreatePinnedToCore(tareaSensores, "sensores", 6144, nullptr, 1, nullptr, 0);
}

//-------------------------------------------------------------
float promedioMv(int pin, int muestras) {
    uint32_t suma = 0;
    for (int i = 0; i < muestras; i++) suma += analogReadMilliVolts(pin);
    return (float)suma / muestras;
}

//-------------------------------------------------------------
// forzar = false (al arrancar): solo mide e informa; NUNCA cambia ni guarda los ceros,
// porque con corriente circulando se calibraría mal. forzar = true: comando "cero".
void calibrarCeros(bool forzar) {
    Serial.println(forzar ? "Calibrando ceros (forzado): sin corriente en el panel ni en el inversor..."
                          : "Midiendo ceros al arrancar (solo informativo, no se guardan)...");
    struct { bool usar; int pin; volatile float* cero; const char* nombre; const char* clave; } canales[] = {
        { USAR_ACS_PV,       PIN_ACS_PV,       &ceroPvMv,  "ACS712-20A (panel)",    "ceroPv"  },
        { USAR_ACS_INVERSOR, PIN_ACS_INVERSOR, &ceroInvMv, "ACS712-30A (inversor)", "ceroInv" },
    };
    for (auto& c : canales) {
        if (!c.usar) continue;
        float medido = promedioMv(c.pin, CERO_MUESTRAS) * ACS_FACTOR_DIVISOR;
        float anterior = *c.cero;
        if (!forzar) {
            Serial.printf("  %s: cero medido %.1f mV, guardado %.1f mV; se usa el guardado%s\n", c.nombre, medido, anterior,
                          fabs(medido - anterior) > CERO_TOLERANCIA_MV
                              ? " (diferencia grande: ¿hay corriente? Para recalibrar, comando \"cero\" sin corriente)" : "");
            continue;
        }
        *c.cero = medido;
        prefs.putFloat(c.clave, medido);
        Serial.printf("  %s: cero %.1f mV (antes %.1f mV)\n", c.nombre, medido, anterior);
    }
}

//-------------------------------------------------------------
float corrienteDe(float mvPin, float cero, float sensibilidad, float ganancia, int signo, float bandaMuerta) {
    float i = signo * ganancia * (mvPin * ACS_FACTOR_DIVISOR - cero) / sensibilidad;
    return fabs(i) < bandaMuerta ? 0.0f : i;
}

//-------------------------------------------------------------
// Potencia neta ESTIMADA de la batería (+ cargando): lo que entrega el MPPT − lo que va
// al inversor − el consumo propio. La misma para publicar y para el conteo del SoC.
float potenciaNetaBateria(float iPv, float iInv, float vBat) {
    return (USAR_ACS_PV ? max(0.0f, iPv) * voltajePv * EFICIENCIA_MPPT : 0)
         - (USAR_ACS_INVERSOR ? vBat * iInv : 0) - CONSUMO_PROPIO_W;
}

//-------------------------------------------------------------
void tareaSensores(void*) {
    TickType_t siguiente = xTaskGetTickCount();
    unsigned long ultimoPzem = 0, ultimoAvisoPzem = 0, ultimoAvisoSaturacion = 0, anterior = millis();
    bool primerBloque = true, pzemAvisado = false;
    bool episodioSaturado = false;                       // para avisar del conteo una vez por episodio
    bool potenciaDescartada = false;                     // última potencia del PZEM > 3000 W: no se integra
    float fPv = 0, fInv = 0, fVbat = 0;
    Lecturas l = {};

    for (;;) {
        if (pedirCero) { calibrarCeros(true); pedirCero = false; }

        // ---- ADC: bloque intercalado de los tres canales
        uint32_t sPv = 0, sInv = 0, sVbat = 0;
        for (int i = 0; i < MUESTRAS_POR_BLOQUE; i++) {
            if (USAR_ACS_PV)       sPv   += analogReadMilliVolts(PIN_ACS_PV);
            if (USAR_ACS_INVERSOR) sInv  += analogReadMilliVolts(PIN_ACS_INVERSOR);
            if (USAR_VBAT)         sVbat += analogReadMilliVolts(PIN_VBAT);
        }
        float mPv = (float)sPv / MUESTRAS_POR_BLOQUE, mInv = (float)sInv / MUESTRAS_POR_BLOQUE, mVbat = (float)sVbat / MUESTRAS_POR_BLOQUE;
        if (primerBloque) { fPv = mPv; fInv = mInv; fVbat = mVbat; primerBloque = false; }
        fPv   += ALFA_FILTRO * (mPv - fPv);
        fInv  += ALFA_FILTRO * (mInv - fInv);
        fVbat += ALFA_FILTRO * (mVbat - fVbat);

        l.hayPv = USAR_ACS_PV; l.hayInv = USAR_ACS_INVERSOR; l.hayVbat = USAR_VBAT;
        l.mvPv = fPv; l.mvInv = fInv; l.mvVbat = fVbat;
        l.crudoPv   = USAR_ACS_PV       ? analogRead(PIN_ACS_PV)       : 0;
        l.crudoInv  = USAR_ACS_INVERSOR ? analogRead(PIN_ACS_INVERSOR) : 0;
        l.crudoVbat = USAR_VBAT         ? analogRead(PIN_VBAT)         : 0;
        l.iPv  = corrienteDe(fPv,  ceroPvMv,  ACS_PV_SENSIBILIDAD_MV_A,  gananciaPv,  ACS_PV_SIGNO,  ACS_PV_BANDA_MUERTA_A);
        if (fabs(l.iPv) < ZONA_MUERTA_PV_A) l.iPv = 0.0f;
        l.iInv = corrienteDe(fInv, ceroInvMv, ACS_INV_SENSIBILIDAD_MV_A, gananciaInv, ACS_INV_SIGNO, ACS_INV_BANDA_MUERTA_A);
        l.vBat = fVbat / 1000.0f * factorVbat;

        // ---- Saturación del ACS712-30A: la marca dura hasta la siguiente publicación
        if (USAR_ACS_INVERSOR && fabs(l.iInv) >= ACS_INV_SATURACION_A) {
            l.invSaturado = true;
            if (ultimoAvisoSaturacion == 0 || millis() - ultimoAvisoSaturacion >= AVISO_SATURACION_MS) {
                Serial.printf("ATENCIÓN: ACS712-30A saturado (%.1f A >= %.0f A). No se usa para el SoC y la corriente "
                              "de la batería se publica null.\n", l.iInv, ACS_INV_SATURACION_A);
                ultimoAvisoSaturacion = millis();
            }
        }

        // ---- PZEM: si no responde devuelve NAN; nunca se cuelga (timeout de la librería)
#if USAR_PZEM
        if (millis() - ultimoPzem >= PERIODO_PZEM_MS) {
            ultimoPzem = millis();
            float v = pzem.voltage(), i = pzem.current(), p = pzem.power(), e = pzem.energy(), hz = pzem.frequency(), fp = pzem.pf();
            bool ok = !isnan(v) && !isnan(i) && !isnan(p);
            l.pzemOk = ok;
            l.acV = v; l.acI = i; l.acP = p; l.acKwh = e; l.acHz = hz; l.acFp = fp;
            potenciaDescartada = ok && p > 3000.0f;
            if (potenciaDescartada) Serial.printf("[PZEM] lectura de potencia descartada: %.1f W\n", p);
            if (!ok) {
                l.pzemFallos++;
                if (!pzemAvisado || millis() - ultimoAvisoPzem >= PZEM_REINTENTO_LOG_MS) {
                    Serial.printf("ERROR: el PZEM-004T no responde (fallos seguidos: %lu). Se publica null en consumo.\n",
                                  (unsigned long)l.pzemFallos);
                    ultimoAvisoPzem = millis(); pzemAvisado = true;
                }
            } else {
                if (pzemAvisado) Serial.println("PZEM-004T responde de nuevo.");
                pzemAvisado = false; l.pzemFallos = 0;
            }
        }
#endif

        // ---- Energía del intervalo
        unsigned long ahora = millis();
        uint32_t dt = ahora - anterior; anterior = ahora;
        double horas = dt / 3600000.0;
        if (USAR_ACS_PV) { l.whGen += max(0.0f, l.iPv) * voltajePv * horas; l.msGen += dt; }
        if (USAR_PZEM && l.pzemOk && !potenciaDescartada) { l.whCons += max(0.0f, l.acP) * horas; l.msCons += dt; }
        // Conteo del SoC: la potencia neta de la batería de ESTE bloque. Con el ACS712-30A
        // saturado se cuenta con la lectura saturada: la descarga real es mayor (cota inferior).
        bool saturadoBloque = USAR_ACS_INVERSOR && fabs(l.iInv) >= ACS_INV_SATURACION_A;
        if (saturadoBloque && !episodioSaturado)
            Serial.println("SoC: ACS712-30A saturado; la descarga se cuenta con la lectura saturada (cota inferior: la real es mayor).");
        episodioSaturado = saturadoBloque;
        if (USAR_VBAT && l.vBat > 5.0f && (USAR_ACS_PV || USAR_ACS_INVERSOR)) {
            float pBat = potenciaNetaBateria(l.iPv, l.iInv, l.vBat);
            if (pBat >= 0) l.whCarga += pBat * horas; else l.whDescarga += -pBat * horas;
        }
        l.msTotal += dt;
        l.bloques++;

        portENTER_CRITICAL(&muxLecturas);
        double whGen = lecturas.whGen, whCons = lecturas.whCons;     // lo que el loop no ha consumido
        double whCarga = lecturas.whCarga, whDescarga = lecturas.whDescarga;
        uint32_t msGen = lecturas.msGen, msCons = lecturas.msCons, msTotal = lecturas.msTotal;
        bool saturado = lecturas.invSaturado || l.invSaturado;
        lecturas = l;
        lecturas.invSaturado = saturado;
        lecturas.whGen = whGen + (l.whGen); lecturas.whCons = whCons + l.whCons;
        lecturas.whCarga = whCarga + l.whCarga; lecturas.whDescarga = whDescarga + l.whDescarga;
        lecturas.msGen = msGen + l.msGen; lecturas.msCons = msCons + l.msCons; lecturas.msTotal = msTotal + l.msTotal;
        portEXIT_CRITICAL(&muxLecturas);
        l.whGen = 0; l.whCons = 0; l.whCarga = 0; l.whDescarga = 0;
        l.msGen = 0; l.msCons = 0; l.msTotal = 0; l.invSaturado = false;

        vTaskDelayUntil(&siguiente, pdMS_TO_TICKS(PERIODO_BLOQUE_MS));
    }
}

//-------------------------------------------------------------
// Copia las lecturas; con `vaciar`, deja los integradores en cero (se publicaron).
Lecturas tomarLecturas(bool vaciar) {
    portENTER_CRITICAL(&muxLecturas);
    Lecturas copia = lecturas;
    if (vaciar) { lecturas.whGen = 0; lecturas.whCons = 0; lecturas.whCarga = 0; lecturas.whDescarga = 0; lecturas.msGen = 0; lecturas.msCons = 0; lecturas.msTotal = 0; lecturas.invSaturado = false; }
    portEXIT_CRITICAL(&muxLecturas);
    return copia;
}

// =============================================================== SoC

// Una vez por publicación. iBat y pNeta: NAN sin corriente conocida (sensor ausente o
// ACS712-30A saturado); entonces no hay reposo real, pero el conteo sigue con la energía
// que integró la tarea (con la saturación como cota inferior). Devuelve el estado a publicar.
soc::Estado actualizarSoc(const Lecturas& l, float iBat, float pNeta, float dt) {
    if (!soc::iniciado(estSoc)) {
        // La energía de este intervalo se descarta: el SoC inicial ya es el de ahora.
        if (!iniciarSoc(l.vBat, iBat)) return soc::Estado::Ninguno;
    } else {
        soc::contar(estSoc, configSoc, l.whCarga, l.whDescarga);
    }
    soc::reposoYCorreccion(estSoc, configSoc, l.hayPv ? l.iPv : NAN, iBat, l.vBat, dt);
    float antes = estSoc.soc;
    if (soc::sincronizarCargaCompleta(estSoc, configSoc, l.vBat, iBat, dt))
        Serial.printf("[SoC] carga completa detectada: %.1f %% → 100 %%\n", antes);
    return soc::actualizarEstado(estSoc, configSoc, pNeta, l.vBat, dt);
}

// Al arrancar (soc::decidirArranque): un SoC guardado válido se restaura SIEMPRE, aunque
// tenga más de 6 h (aproximado: si la ESP32 estuvo apagada mucho tiempo, recalibrar desde la
// app). Sin guardado: se espera la hora NTP hasta SOC_ESPERA_NTP_MS (SoC null) y luego la
// tabla con el voltaje compensado, como antes.
bool iniciarSoc(float v, float iBat) {
    time_t ahora = time(nullptr);
    bool hayGuardado = prefs.isKey("soc");
    float guardado = hayGuardado ? prefs.getFloat("soc", NAN) : NAN;
    uint32_t cuando = prefs.isKey("socT") ? prefs.getUInt("socT", 0) : 0;
    bool esperaVencida = socEsperaNtpDesde != 0 && millis() - socEsperaNtpDesde >= SOC_ESPERA_NTP_MS;
    soc::DecisionArranque d = soc::decidirArranque(hayGuardado, guardado, cuando, ahora > 0 ? (uint32_t)ahora : 0,
                                                   HORA_MINIMA_VALIDA, esperaVencida);
    if (d.accion == soc::Arranque::Restaurar) {
        soc::iniciar(estSoc, d.soc, soc::Inicio::Restaurado);
        estSoc.edadRestauradoS = d.edadS;
        socGuardado = estSoc.soc;
        if (d.edadS >= 0)
            Serial.printf("SoC restaurado de flash: %.1f %% (hace %.1f h, aproximado). Si parece incorrecto, "
                          "recalibra desde la app (Configuración).\n", estSoc.soc, d.edadS / 3600.0f);
        else
            Serial.printf("SoC restaurado de flash: %.1f %% (edad desconocida: sin hora NTP o guardado sin fecha; "
                          "aproximado).\n", estSoc.soc);
        return true;
    }
    if (d.accion == soc::Arranque::EsperarHora) {
        if (socEsperaNtpDesde == 0) {
            socEsperaNtpDesde = millis();
            Serial.printf("SoC: no hay uno guardado; esperando la hora NTP (hasta %lu s; mientras, se publica null).\n",
                          SOC_ESPERA_NTP_MS / 1000);
        }
        return false;
    }
    float vComp = soc::vCompensado(configSoc, v, iBat);
    soc::iniciar(estSoc, soc::socDeVoltaje(configSoc, vComp), soc::Inicio::InicialVoltaje);
    Serial.printf("SoC inicial por voltaje (aproximado): %.1f %% con %.2f V compensados. Recalibra desde la app "
                  "o, con el monitor serie, \"soc <0-100>\".\n", estSoc.soc, vComp);
    return true;
}

// Cada SOC_GUARDAR_CADA_MS, con hora NTP válida: guarda el SoC y la hora si cambió >= SOC_GUARDAR_DELTA.
void guardarSocSiToca() {
    if (!soc::iniciado(estSoc) || millis() - ultimoGuardadoSoc < SOC_GUARDAR_CADA_MS) return;
    time_t ahora = time(nullptr);
    if (ahora <= (time_t)HORA_MINIMA_VALIDA) return;
    ultimoGuardadoSoc = millis();
    if (!isnan(socGuardado) && fabs(estSoc.soc - socGuardado) < SOC_GUARDAR_DELTA) return;
    prefs.putFloat("soc", estSoc.soc);
    prefs.putUInt("socT", (uint32_t)ahora);
    socGuardado = estSoc.soc;
}

// Fija el SoC y lo guarda en la flash: el comando serie "soc 66" (Inicio::Manual) y la
// confirmación de la recalibración de la app (Inicio::Recalibrado) usan esta misma función.
// Sin hora NTP se guarda sin fecha (al reiniciar se restaura con edad desconocida) y la
// fecha se completa en el próximo guardado con hora.
void fijarSoc(float valor, soc::Inicio inicio) {
    const char* como = inicio == soc::Inicio::Recalibrado ? "recalibrado desde la app" : "fijado a mano";
    soc::iniciar(estSoc, valor, inicio);
    estSoc.edadRestauradoS = -1;
    prefs.putFloat("soc", estSoc.soc);
    time_t ahora = time(nullptr);
    if (ahora > (time_t)HORA_MINIMA_VALIDA) {
        prefs.putUInt("socT", (uint32_t)ahora);
        socGuardado = estSoc.soc;
        ultimoGuardadoSoc = millis();
        Serial.printf("SoC %s: %.1f %%. Guardado en flash.\n", como, estSoc.soc);
    } else {
        prefs.remove("socT");
        socGuardado = NAN;                 // el próximo guardado con hora escribe valor y fecha
        Serial.printf("SoC %s: %.1f %%. Sin hora NTP: se guarda sin fecha y se completa cuando la haya.\n",
                      como, estSoc.soc);
    }
}

void mostrarSoc() {
    if (!soc::iniciado(estSoc)) { Serial.println("SoC: sin iniciar (esperando la primera publicación o la hora NTP)"); return; }
    const char* estado = soc::textoEstado(estadoSoc);
    char sinCarga[40];
    if (estSoc.cargaAhora) snprintf(sinCarga, sizeof(sinCarga), "cargando ahora");
    else snprintf(sinCarga, sizeof(sinCarga), "sin carga hace %.0f/%.0f min", estSoc.segundosSinCarga / 60.0f, SIN_CARGA_S / 60.0f);
    char edad[40] = "";
    if (estSoc.inicio == soc::Inicio::Restaurado) {
        if (estSoc.edadRestauradoS >= 0) snprintf(edad, sizeof(edad), ", hace %.1f h", estSoc.edadRestauradoS / 3600.0f);
        else snprintf(edad, sizeof(edad), ", edad desconocida");
    }
    Serial.printf("SoC %.1f %% | fuente: %s (inicio: %s%s) | %s | reposo real %.1f/%.0f min | carga completa %.1f/%.0f min | "
                  "tabla %.1f %% con %.3f V compensados | P media %+.1f W | estado %s\n",
                  estSoc.soc, soc::textoFuente(estSoc.fuente), soc::textoInicio(estSoc.inicio), edad, sinCarga,
                  estSoc.segundosReposo / 60.0f, REPOSO_REAL_S / 60.0f,
                  estSoc.segundosCargaCompleta / 60.0f, CARGA_COMPLETA_S / 60.0f, estSoc.ultimaTabla, estSoc.ultimoVComp,
                  estSoc.pMedia, estado ? estado : "-");
    if (recalSoc.estado != soc::recal::Estado::Inactiva) mostrarRecal();
}

// =============================================================== Recalibración guiada

// El callback de MQTT solo anota el pedido (no publica dentro del callback). false si la
// acción no existe. Cualquier otro campo del mensaje (p. ej. un valor de SoC) se ignora.
bool pedirRecal(const char* accion) {
    if      (!strcmp(accion, "iniciar"))   pedidoRecal = RECAL_INICIAR;
    else if (!strcmp(accion, "confirmar")) pedidoRecal = RECAL_CONFIRMAR;
    else if (!strcmp(accion, "cancelar"))  pedidoRecal = RECAL_CANCELAR;
    else return false;
    return true;
}

void ejecutarAccionRecal(int accion, const char* origen) {
    using namespace soc::recal;
    if (accion == RECAL_INICIAR) {
        if (activa(recalSoc)) Serial.println("[RECAL] ya había una sesión: se descarta y empieza otra.");
        iniciar(recalSoc, millis());
        recalSoc.socActual = soc::iniciado(estSoc) ? estSoc.soc : NAN;
        muestrasRecal = 0;
        Serial.printf("[RECAL] sesión iniciada (%s): hacen falta %d min seguidos en reposo (sin sol, inversor apagado), "
                      "como mucho %d min.\n", origen, RECAL_REPOSO_MIN, RECAL_MAX_MIN);
    } else if (accion == RECAL_CONFIRMAR) {
        float valor = NAN;
        float antes = soc::iniciado(estSoc) ? estSoc.soc : NAN;
        if (confirmar(recalSoc, configRecal, millis(), valor)) {
            fijarSoc(valor, soc::Inicio::Recalibrado);      // la misma función que el comando "soc"
            recalSoc.socActual = estSoc.soc;
            Serial.printf("[RECAL] aplicada (%s): %.1f %% → %.1f %% (medido con %.2f V compensados).\n",
                          origen, antes, valor, recalSoc.voltaje);
        } else {
            Serial.printf("[RECAL] confirmación rechazada (%s): %s.\n", origen, textoMotivo(recalSoc.motivo));
        }
    } else if (accion == RECAL_CANCELAR) {
        cancelar(recalSoc);
        Serial.printf("[RECAL] cancelada (%s): el SoC no cambia.\n", origen);
    }
    recalPorPublicar = true;
}

// Una muestra por publicación de la telemetría (cada 10 s).
void muestraRecal(const Lecturas& l, float iBat, float dt) {
    using namespace soc::recal;
    if (!activa(recalSoc)) return;
    Motivo anterior = recalSoc.motivo;
    bool cambio = muestra(recalSoc, configRecal, configSoc, l.hayPv ? l.iPv : NAN, l.hayInv ? l.iInv : NAN,
                          l.invSaturado, iBat, l.hayVbat ? l.vBat : NAN,
                          soc::iniciado(estSoc) ? estSoc.soc : NAN, dt, millis());
    if (cambio) {
        recalPorPublicar = true;
        mostrarRecal();
    } else if (recalSoc.motivo != anterior || ++muestrasRecal % 6 == 0) {
        mostrarRecal();
    }
}

// En cada vuelta del loop: el pedido de la app, el vencimiento y la publicación del estado.
void atenderRecal() {
    int pedido = pedidoRecal;
    pedidoRecal = RECAL_NADA;
    if (pedido != RECAL_NADA) ejecutarAccionRecal(pedido, "app");
    if (soc::recal::revisarVencimiento(recalSoc, configRecal, millis())) {
        Serial.printf("[RECAL] vencida: %s.\n", soc::recal::textoMotivo(recalSoc.motivo));
        recalPorPublicar = true;
    }
    static bool conectadoAntes = false;
    bool conectado = mqtt.connected();
    if (conectado && !conectadoAntes) recalPorPublicar = true;     // retenido al día tras reconectar
    conectadoAntes = conectado;
    if (!conectado) return;
    // Mientras la sesión está activa (esperando o lista), cada RECAL_PUBLICAR_MS: el backend
    // marca "interrumpida" una sesión activa sin noticias.
    bool periodica = soc::recal::activa(recalSoc) && millis() - ultimaPublicacionRecal >= RECAL_PUBLICAR_MS;
    if (!recalPorPublicar && !periodica) return;
    ultimaPublicacionRecal = millis();
    if (publicarEstadoSoc()) recalPorPublicar = false;
}

// domo/{id}/estado/soc, RETENIDO (es un estado, no un comando).
bool publicarEstadoSoc() {
    using namespace soc::recal;
    char tema[40], cuerpo[384];
    snprintf(tema, sizeof(tema), "domo/%d/estado/soc", DOMO_ID);
    JsonDocument d;
    d["estado"] = textoEstado(recalSoc.estado);
    if (recalSoc.estado != Estado::Inactiva) {
        ponerNumero(d, "minutos_ok", true, minutosOk(recalSoc), 1);
        d["requeridos"] = RECAL_REPOSO_MIN;
        const char* motivo = textoMotivo(recalSoc.motivo);
        if (*motivo) d["motivo"] = motivo; else d["motivo"] = nullptr;
        ponerNumero(d, "soc_actual", true, recalSoc.socActual, 1);
        ponerNumero(d, "soc_propuesto", true, recalSoc.socPropuesto, 1);
        ponerNumero(d, "voltaje", true, recalSoc.voltaje, 2);
    }
    time_t t = time(nullptr);
    if (t > (time_t)HORA_MINIMA_VALIDA) d["t"] = (uint32_t)t; else d["t"] = nullptr;
    size_t n = serializeJson(d, cuerpo, sizeof(cuerpo));
    bool ok = n > 0 && n < sizeof(cuerpo) && mqtt.publish(tema, cuerpo, true);   // true = retenido
    if (!ok) Serial.printf("[RECAL] no se pudo publicar %s.\n", tema);
    return ok;
}

void mostrarRecal() {
    using namespace soc::recal;
    Serial.printf("[RECAL] %s | %.1f/%d min en reposo | %s | SoC actual %.1f %% | propuesto %.1f %% | %.2f V compensados\n",
                  textoEstado(recalSoc.estado), minutosOk(recalSoc), RECAL_REPOSO_MIN,
                  *textoMotivo(recalSoc.motivo) ? textoMotivo(recalSoc.motivo) : "-",
                  recalSoc.socActual, recalSoc.socPropuesto, recalSoc.voltaje);
}

// =============================================================== Publicación

void ponerNumero(JsonDocument& d, const char* campo, bool hay, float valor, int decimales) {
    if (!hay || isnan(valor) || isinf(valor)) { d[campo] = nullptr; return; }
    float escala = powf(10, decimales);
    d[campo] = roundf(valor * escala) / escala;
}

bool publicarJson(const char* tipo, JsonDocument& d) {
    char tema[48], cuerpo[256];
    snprintf(tema, sizeof(tema), "domo/%d/telemetria/%s", DOMO_ID, tipo);
    size_t n = serializeJson(d, cuerpo, sizeof(cuerpo));
    bool ok = n > 0 && n < sizeof(cuerpo) && mqtt.publish(tema, cuerpo);
    if (!ok) Serial.printf("No se pudo publicar %s.\n", tema);
    return ok;
}

void atenderTelemetria() {
    unsigned long ahora = millis();
    // Con hora NTP valida se publica a mitad de cada bloque de 10 s del reloj (segundo 5;
    // hasta el 9 si el loop estuvo ocupado), una sola vez por bloque, porque el listener
    // agrupa por hora de llegada. Sin hora valida, cada PERIODO_TELEMETRIA_MS con millis().
    static time_t ultimoBloque = -1;
    time_t reloj = time(nullptr);
    time_t bloque = reloj / 10;
    unsigned long desde = ahora - ultimaTelemetria;
    if (reloj > 1700000000) {
        bool toca = (reloj % 10) >= 5 && bloque != ultimoBloque
                    && (ultimaTelemetria == 0 || desde >= PERIODO_TELEMETRIA_MS / 2);   // salto atras del reloj
        bool atrasada = ultimaTelemetria != 0 && desde >= 2 * PERIODO_TELEMETRIA_MS;  // salto adelante o reloj parado
        if (!toca && !atrasada) return;
    } else if (desde < PERIODO_TELEMETRIA_MS) return;
    if (!mqtt.connected()) return;                      // sin broker, los integradores siguen acumulando
    float dt = ultimaTelemetria == 0 ? PERIODO_TELEMETRIA_MS / 1000.0f : (ahora - ultimaTelemetria) / 1000.0f;
    ultimaTelemetria = ahora;
    ultimoBloque = bloque;

    // Se vacían al tomarlas: si una publicación falla, esa energía se pierde en vez de
    // contarse dos veces en los temas que sí salieron.
    Lecturas l = tomarLecturas(true);
    if (l.bloques == 0) return;

    // ---- Panel (potencia ESTIMADA: el voltaje del panel no se mide)
    float pGen = l.hayPv ? max(0.0f, l.iPv) * voltajePv : NAN;
    // ---- Batería: corriente neta ESTIMADA = lo que entrega el MPPT − lo que va al inversor
    bool hayBateria = l.hayVbat && l.vBat > 5.0f;
    // Con el ACS712-30A saturado, la corriente real es mayor que la leída: el balance
    // sería falso, así que no se publica (el conteo del SoC sí la usa, como cota inferior).
    bool hayCorriente = hayBateria && (l.hayPv || l.hayInv) && !l.invSaturado;
    float pNeta = NAN, iBat = NAN;
    if (hayCorriente) {
        pNeta = potenciaNetaBateria(l.iPv, l.iInv, l.vBat);
        iBat = pNeta / l.vBat;
    }
    estadoSoc = hayBateria ? actualizarSoc(l, iBat, pNeta, dt) : soc::Estado::Ninguno;
    guardarSocSiToca();
    muestraRecal(l, iBat, dt);

    // Energía del intervalo: null si el sensor no estuvo disponible la mayor parte del tiempo.
    bool hayWhGen  = l.hayPv && l.msTotal > 0 && l.msGen * 2 >= l.msTotal;
    bool hayWhCons = USAR_PZEM && l.msTotal > 0 && l.msCons * 2 >= l.msTotal;
    // Energía de consumo imposible para el ciclo (negativa o > 3 × potencia × dt): se publica 0.
    if (hayWhCons) {
        float pRef = (l.pzemOk && !isnan(l.acP)) ? max(50.0f, l.acP) : 50.0f;
        double maxWh = pRef * (dt / 3600.0) * 3.0;
        if (!(l.whCons >= 0 && l.whCons <= maxWh)) {
            Serial.printf("[PZEM] salto de contador descartado: %.1f Wh\n", l.whCons);
            l.whCons = 0;
        }
    }

    JsonDocument fv;
    fv["voltaje"] = nullptr;                                   // no se mide
    ponerNumero(fv, "corriente", l.hayPv, l.iPv, 2);
    ponerNumero(fv, "potencia_generada", l.hayPv, pGen, 1);
    ponerNumero(fv, "energia_generada", hayWhGen, l.whGen, 4);
    if (l.hayPv) fv["estado_generacion"] = pGen > UMBRAL_GENERANDO_W ? "generando" : "sin_generacion";
    else         fv["estado_generacion"] = nullptr;

    JsonDocument ba;
    ponerNumero(ba, "voltaje", hayBateria, l.vBat, 2);
    ponerNumero(ba, "corriente", hayCorriente, iBat, 2);
    ponerNumero(ba, "potencia", hayCorriente, pNeta, 1);
    ponerNumero(ba, "estado_carga_soc", hayBateria && soc::iniciado(estSoc), estSoc.soc, 1);
    const char* estado = soc::textoEstado(estadoSoc);
    if (hayCorriente && estado)          ba["estado_bateria"] = estado;
    else if (hayBateria && l.invSaturado) ba["estado_bateria"] = "descargando";   // >= 29 A hacia el inversor
    else                                 ba["estado_bateria"] = nullptr;

    JsonDocument co;
    bool hayAc = USAR_PZEM && l.pzemOk;
    ponerNumero(co, "voltaje", hayAc, l.acV, 1);
    ponerNumero(co, "corriente", hayAc, l.acI, 3);
    ponerNumero(co, "potencia_consumida", hayAc, l.acP, 1);
    ponerNumero(co, "energia_consumida", hayWhCons, l.whCons, 4);

    bool ok = publicarJson("fotovoltaica", fv) & publicarJson("bateria", ba) & publicarJson("consumo", co);

#if USAR_PZEM
    if (PERIODO_SENSORES_PZEM_MS > 0 && hayAc && ahora - ultimosSensoresPzem >= PERIODO_SENSORES_PZEM_MS) {
        ultimosSensoresPzem = ahora;
        struct { const char* variable; float valor; const char* unidad; int dec; } extra[] = {
            { "frecuencia", l.acHz, "Hz", 1 }, { "factor_potencia", l.acFp, nullptr, 2 },
        };
        for (auto& x : extra) {
            if (isnan(x.valor)) continue;
            JsonDocument s;
            s["tipo_sensor"] = "pzem004t";
            s["variable_medida"] = x.variable;
            ponerNumero(s, "valor", true, x.valor, x.dec);
            if (x.unidad) s["unidad_medida"] = x.unidad; else s["unidad_medida"] = nullptr;
            s["estado"] = "ok";
            publicarJson("sensores", s);
        }
    }
#endif

    Serial.printf("Telemetría%s | PV %.2f A × %.1f V = %.0f W (ESTIMADA) | Bat %.2f V, %+.2f A (estimada)%s, SoC %.1f %% %s | ",
                  ok ? "" : " (con fallos)", l.iPv, voltajePv, isnan(pGen) ? 0.0f : pGen, l.vBat,
                  isnan(iBat) ? 0.0f : iBat, l.invSaturado ? " ACS-30A SATURADO" : "",
                  soc::iniciado(estSoc) ? estSoc.soc : -1.0f, hayCorriente && estado ? estado : "-");
    if (hayAc) Serial.printf("AC %.1f V %.3f A %.1f W %.1f Hz FP %.2f\n", l.acV, l.acI, l.acP, l.acHz, l.acFp);
    else       Serial.println(USAR_PZEM ? "AC: PZEM sin respuesta (null)" : "AC: PZEM desactivado");
}

// =============================================================== Calibración por el monitor serie

void mostrarCalibracion() {
    Lecturas l = tomarLecturas(false);
    Serial.println("---- Calibración (\"cal\" para salir) ----");
    if (l.hayPv)  Serial.printf("ACS712-20A panel    GPIO%d  crudo %4d  pin %7.1f mV  sensor %7.1f mV  cero %7.1f mV  -> %+6.2f A\n",
                                PIN_ACS_PV, l.crudoPv, l.mvPv, l.mvPv * ACS_FACTOR_DIVISOR, ceroPvMv, l.iPv);
    if (l.hayInv) Serial.printf("ACS712-30A inversor GPIO%d  crudo %4d  pin %7.1f mV  sensor %7.1f mV  cero %7.1f mV  -> %+6.2f A%s\n",
                                PIN_ACS_INVERSOR, l.crudoInv, l.mvInv, l.mvInv * ACS_FACTOR_DIVISOR, ceroInvMv, l.iInv,
                                fabs(l.iInv) >= ACS_INV_SATURACION_A ? "  SATURADO" : "");
    Serial.printf("Referencia ACS712 sin corriente: sensor ~%.0f mV (Vcc/2), pin ~%.0f mV\n",
                  ACS_CERO_NOMINAL_MV, ACS_CERO_NOMINAL_MV / ACS_FACTOR_DIVISOR);
    if (l.hayVbat) Serial.printf("Batería             GPIO%d  crudo %4d  pin %7.1f mV  factor %.4f             -> %6.3f V (SoC tabla %.1f %%)\n",
                                 PIN_VBAT, l.crudoVbat, l.mvVbat, factorVbat, l.vBat, soc::socDeVoltaje(configSoc, l.vBat));
    if (l.hayVbat) mostrarSoc();
    if (USAR_PZEM) {
        if (l.pzemOk) Serial.printf("PZEM-004T           %.1f V  %.3f A  %.1f W  %.3f kWh  %.1f Hz  FP %.2f\n",
                                    l.acV, l.acI, l.acP, l.acKwh, l.acHz, l.acFp);
        else Serial.printf("PZEM-004T           sin respuesta (fallos seguidos: %lu)\n", (unsigned long)l.pzemFallos);
    }
    Serial.printf("Panel: %.1f V nominal (no medido) -> %.0f W estimados. Ganancias ACS: %.3f / %.3f\n", voltajePv, max(0.0f, l.iPv) * voltajePv, gananciaPv, gananciaInv);
}

void ayudaSerie() {
    Serial.println("Comandos del monitor serie (115200, fin de línea \\n):");
    Serial.println("  cal              activa o desactiva la vista de calibración (cada 1 s)");
    Serial.println("  cero             recalibra el cero de los ACS712 (SIN corriente)");
    Serial.println("  vbat 12.73       ajusta el factor del divisor con el voltaje del multímetro");
    Serial.println("  vpv 58.4         voltaje del panel que muestra el MPPT (para la potencia estimada)");
    Serial.println("  ipv 6.2          ajusta la ganancia del ACS712-20A con la corriente de una pinza DC (> 1 A)");
    Serial.println("  iinv 12.5        ajusta la ganancia del ACS712-30A con la corriente de una pinza DC (> 1 A)");
    Serial.println("  soc 66           fija el SoC a mano (0-100) y lo guarda en la flash");
    Serial.println("  recal            estado de la recalibración guiada; recal iniciar|confirmar|cancelar");
    Serial.println("  fabrica          vuelve a los valores de sensores_config.h");
    Serial.println("  estado           una lectura de todo y la lista de dispositivos Tuya");
    Serial.println("  red              dispositivos Tuya anunciados en la red (ID abreviado, IP, versión)");
    Serial.println("  autotest         prueba el protocolo Tuya 3.3/3.4/3.5 con los vectores de tinytuya (sin red)");
    Serial.println("  probar toma1 on  manda on|off a ese id_fisico, verifica e imprime el resultado y el tiempo");
}

void atenderSerie() {
    static char linea[48];
    static int n = 0;
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\r') continue;
        if (c != '\n') { if (n < (int)sizeof(linea) - 1) linea[n++] = c; continue; }
        linea[n] = '\0'; n = 0;
        ejecutarComandoSerie(linea);
    }
    if (modoCalibracion && millis() - ultimaVista >= 1000) { ultimaVista = millis(); mostrarCalibracion(); }
}

void ejecutarComandoSerie(char* linea) {
    while (*linea == ' ') linea++;
    if (*linea == '\0') return;
    if (comandoSerieTuya(linea)) return;                 // autotest y probar (domo_nodo.ino)
    if (!strncmp(linea, "recal", 5) && (linea[5] == '\0' || linea[5] == ' ')) {
        const char* arg = linea + 5;
        while (*arg == ' ') arg++;
        if      (!*arg)                       mostrarRecal();
        else if (!strcmp(arg, "estado"))      mostrarRecal();
        else if (!strcmp(arg, "iniciar"))     ejecutarAccionRecal(RECAL_INICIAR, "serie");
        else if (!strcmp(arg, "confirmar"))   ejecutarAccionRecal(RECAL_CONFIRMAR, "serie");
        else if (!strcmp(arg, "cancelar"))    ejecutarAccionRecal(RECAL_CANCELAR, "serie");
        else Serial.println("Uso: recal [estado|iniciar|confirmar|cancelar]");
        return;
    }
    float valor = NAN;
    char* espacio = strchr(linea, ' ');
    if (espacio) { *espacio = '\0'; valor = atof(espacio + 1); }

    if (!strcmp(linea, "cal")) {
        modoCalibracion = !modoCalibracion;
        Serial.println(modoCalibracion ? "Vista de calibración activada." : "Vista de calibración desactivada.");
    } else if (!strcmp(linea, "cero")) {
        pedirCero = true;
    } else if (!strcmp(linea, "vbat")) {
        Lecturas l = tomarLecturas(false);
        if (!(valor > 5 && valor < 20) || l.mvVbat < 100) { Serial.println("Uso: vbat <voltios medidos con el multímetro, 5-20>"); return; }
        factorVbat = valor / (l.mvVbat / 1000.0f);
        prefs.putFloat("fVbat", factorVbat);
        Serial.printf("Factor de la batería: %.4f (nominal %.2f). Guardado.\n", factorVbat, VBAT_FACTOR_DIVISOR);
    } else if (!strcmp(linea, "vpv")) {
        if (!(valor > 5 && valor < 200)) { Serial.println("Uso: vpv <voltios del panel en el MPPT, 5-200>"); return; }
        voltajePv = valor;
        prefs.putFloat("vPv", voltajePv);
        Serial.printf("Voltaje del panel para la estimación: %.1f V. Guardado.\n", voltajePv);
    } else if (!strcmp(linea, "ipv") || !strcmp(linea, "iinv")) {
        bool pv = !strcmp(linea, "ipv");
        Lecturas l = tomarLecturas(false);
        float medida = fabs(pv ? l.iPv : l.iInv);
        if (!(valor > 1 && valor < 40) || medida < 1.0f) {
            Serial.println("Uso: ipv|iinv <amperios de la pinza, > 1 A>, con al menos 1 A circulando");
            return;
        }
        float nueva = (pv ? gananciaPv : gananciaInv) * valor / medida;
        if (pv) gananciaPv = nueva; else gananciaInv = nueva;
        prefs.putFloat(pv ? "gPv" : "gInv", nueva);
        Serial.printf("Ganancia del %s: %.4f (medía %.2f A, pinza %.2f A). Guardada.\n", pv ? "ACS712-20A" : "ACS712-30A", nueva, medida, valor);
    } else if (!strcmp(linea, "soc")) {
        char* fin = nullptr;
        float nuevo = espacio ? strtof(espacio + 1, &fin) : NAN;
        if (!espacio || fin == espacio + 1 || !(nuevo >= 0 && nuevo <= 100)) {
            Serial.println("Uso: soc <0-100>   fija el SoC a mano y lo guarda en la flash");
            mostrarSoc();
            return;
        }
        fijarSoc(nuevo, soc::Inicio::Manual);
        mostrarSoc();
    } else if (!strcmp(linea, "fabrica")) {
        prefs.clear();
        ceroPvMv = ACS_CERO_NOMINAL_MV; ceroInvMv = ACS_CERO_NOMINAL_MV;
        factorVbat = VBAT_FACTOR_DIVISOR; voltajePv = VOLTAJE_PV_NOMINAL;
        gananciaPv = 1.0f; gananciaInv = 1.0f;
        Serial.println("Calibración borrada: valores de sensores_config.h. Usa \"cero\" sin corriente.");
    } else if (!strcmp(linea, "red")) {
        mostrarRed();
    } else if (!strcmp(linea, "estado")) {
        mostrarCalibracion();
        listarDispositivos();
    } else {
        ayudaSerie();
    }
}
