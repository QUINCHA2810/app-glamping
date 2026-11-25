#pragma once
// ============================================================================
//  Configuración de los sensores del domo. TODO el hardware de medición está
//  aquí: si cambia un pin, un divisor o un sensor, solo se toca este archivo.
//  Ver firmware/README.md (tabla de pines y calibración).
//
//  Unidades que se publican (las de backend/schemas.py y mqtt_listener.py):
//    voltaje V, corriente A, potencia W, energía Wh DEL INTERVALO, SoC 0-100 %.
//  Convención de signo de la batería (docs/Requisitos/Variables.md):
//    corriente y potencia positivas = cargando, negativas = descargando.
// ============================================================================

// ---------------------------------------------------------------- Interruptores
// 0 = sensor desactivado: no se lee, y sus campos se publican como null.
#define USAR_ACS_PV        1   // ACS712-20A, corriente del panel (lado PV)
#define USAR_ACS_INVERSOR  1   // ACS712-30A, corriente DC batería -> inversor
#define USAR_VBAT          1   // divisor 47k/10k del voltaje de la batería
#define USAR_PZEM          1   // PZEM-004T v3 en la salida AC del inversor

// ---------------------------------------------------------------- Pines
// Solo pines del ADC1 (32-39): el ADC2 no funciona con WiFi encendido.
#define PIN_ACS_PV         34
#define PIN_ACS_INVERSOR   35
#define PIN_VBAT           32
// GPIO33 libre (el voltaje del panel NO se mide).
#define PIN_PZEM_RX        16  // RX del ESP32 <- TX del PZEM (por el divisor 1k/2k)
#define PIN_PZEM_TX        17  // TX del ESP32 -> RX del PZEM

// ---------------------------------------------------------------- ACS712
// Salida: Vcc/2 en cero y SENSIBILIDAD mV/A alrededor. Va al pin por un divisor
// 10k/20k, así que el voltaje del sensor es el del pin × FACTOR_DIVISOR.
#define ACS_PV_SENSIBILIDAD_MV_A     100.0f  // ACS712-20A
#define ACS_INV_SENSIBILIDAD_MV_A     66.0f  // ACS712-30A
#define ACS_FACTOR_DIVISOR             1.5f  // (10k + 20k) / 20k
#define ACS_CERO_NOMINAL_MV         2500.0f  // Vcc/2 con 5 V, en el lado del sensor
// +1 o -1: cambia el signo si el sensor quedó montado al revés. La corriente del
// panel debe dar positiva al generar, y la del inversor positiva al consumir.
#define ACS_PV_SIGNO                   1
#define ACS_INV_SIGNO                  1
// Por debajo de esto la corriente se publica como 0: es ruido del sensor.
#define ACS_PV_BANDA_MUERTA_A          0.15f
#define ACS_INV_BANDA_MUERTA_A         0.20f
// El ACS712-30A satura cerca de 30 A (~360 W a 12 V): desde aquí la lectura se
// marca como saturada, no se usa para el SoC y la corriente de la batería va null.
#define ACS_INV_SATURACION_A          29.0f
#define AVISO_SATURACION_MS        60000     // un aviso por minuto como máximo

// Cero de cada ACS712. Se mide al arrancar y con el comando serie "cero".
// Arranca SIN corriente: breaker del panel abierto e inversor apagado, o el cero
// absorbe una corriente real. Para protegerlo, al arrancar solo se acepta un cero
// que quede a menos de CERO_TOLERANCIA_MV del guardado (o del nominal la primera
// vez); si no, se usa el guardado y se avisa. El comando "cero" lo fuerza.
#define CALIBRAR_CERO_AL_ARRANCAR      1
#define CERO_TOLERANCIA_MV            60.0f  // ~0,6 A en el de 20 A, ~0,9 A en el de 30 A
#define CERO_MUESTRAS                2000

// ---------------------------------------------------------------- Batería
// Divisor 47k/10k (con 100 nF a tierra): V_bat = V_pin × FACTOR. El comando
// "vbat <voltios del multímetro>" recalcula el factor y lo guarda en la flash.
#define VBAT_FACTOR_DIVISOR            5.7f  // (47k + 10k) / 10k
#define BATERIA_CAPACIDAD_AH         100.0f  // CSB HRL12390W: 100 Ah a 20 h (ficha CSB)

// ---------------------------------------------------------------- Panel
// El voltaje del panel NO se mide: la potencia generada es ESTIMADA como
// corriente del ACS712-20A × VOLTAJE_PV_NOMINAL. Ajústalo con el voltaje que
// muestra el MPPT (comando "vpv <voltios>", queda en la flash). Ver README.
#define VOLTAJE_PV_NOMINAL            60.0f
// Fracción de la potencia del panel que llega a la batería (pérdidas del MPPT y
// del cableado). Solo se usa para ESTIMAR la corriente neta de la batería.
#define EFICIENCIA_MPPT                0.95f
// Consumo propio en DC que no pasa por el ACS del inversor (ESP32, LM2596, MPPT).
#define CONSUMO_PROPIO_W               3.0f
// Sobre esta potencia estimada, estado_generacion = "generando".
#define UMBRAL_GENERANDO_W             5.0f

// ---------------------------------------------------------------- Muestreo
// Una tarea de FreeRTOS lee los sensores aparte del loop: las órdenes a las
// tomas (4-6 s) y MQTT no se bloquean entre sí.
#define MUESTRAS_POR_BLOQUE           64     // por canal y por bloque
#define PERIODO_BLOQUE_MS            100     // un bloque cada 100 ms
#define ALFA_FILTRO                    0.15f // filtro exponencial por bloque (0-1; más bajo = más suave)
#define PERIODO_PZEM_MS             1000     // la librería no refresca más rápido de 200 ms
#define PZEM_REINTENTO_LOG_MS      60000     // un aviso de PZEM caído como máximo por minuto

// ---------------------------------------------------------------- Publicación
#define PERIODO_TELEMETRIA_MS      10000     // cada 10 s: 8640 filas por día y por tabla
// Frecuencia y factor de potencia del PZEM van a lecturas_sensores (no tienen
// columna en lecturas_consumo). 0 = no se publican.
#define PERIODO_SENSORES_PZEM_MS   60000

// ---------------------------------------------------------------- SoC (AGM 12 V)
// Voltaje en circuito abierto (en reposo, 25 °C) -> SoC, en orden ascendente.
//
// En uso: tabla genérica de AGM de 12 V a 25 °C que eligió el equipo (ronda F1c)
// por ser más conservadora: para un mismo voltaje da menos SoC que la de Trojan
// en la mitad baja (12,25 V = 40 % contra 50 %). No viene de una ficha concreta.
#define SOC_TABLA_PUNTOS 11
static const float SOC_TABLA_V[SOC_TABLA_PUNTOS]   = { 11.65f, 11.80f, 11.95f, 12.10f, 12.25f, 12.35f, 12.45f, 12.55f, 12.65f, 12.75f, 12.85f };
static const float SOC_TABLA_PCT[SOC_TABLA_PUNTOS] = {   0.0f,  10.0f,  20.0f,  30.0f,  40.0f,  50.0f,  60.0f,  70.0f,  80.0f,  90.0f, 100.0f };
//
// Alternativa: la que Trojan Battery publica para su familia AGM (12,84 V = 100 %,
// 12,54 V = 75 %, 12,24 V = 50 %, 11,94 V = 25 %, 11,64 V = 0 %), interpolada cada
// 10 %. Para usarla, comenta las dos líneas de arriba y descomenta estas:
// static const float SOC_TABLA_V[SOC_TABLA_PUNTOS]   = { 11.64f, 11.76f, 11.88f, 12.00f, 12.12f, 12.24f, 12.36f, 12.48f, 12.60f, 12.72f, 12.84f };
// static const float SOC_TABLA_PCT[SOC_TABLA_PUNTOS] = {   0.0f,  10.0f,  20.0f,  30.0f,  40.0f,  50.0f,  60.0f,  70.0f,  80.0f,  90.0f, 100.0f };
//
// Ninguna de las dos es de la CSB HRL12390W: la mejor es la curva "Open circuit
// voltage vs. residual capacity" de su ficha, cuando la tengas.

// Compensación del voltaje bajo carga: V_reposo ≈ V_medido − I_bat × R. La ficha
// CSB da ~3 mΩ de resistencia interna; se suma una estimación de cables y bornes.
// Mídela con el procedimiento del README.
#define R_COMPENSACION_OHM             0.010f

// ---------------------------------------------------------------- SoC: estimador
// (soc_estimador.h/.cpp). CONTEO DE ENERGÍA siempre, con la potencia neta estimada
// de la batería integrada cada 100 ms; la tabla de voltaje solo corrige en REPOSO REAL.
#define BATERIA_VOLTAJE_NOMINAL       12.0f
#define BATERIA_CAPACIDAD_WH  (BATERIA_CAPACIDAD_AH * BATERIA_VOLTAJE_NOMINAL)   // 1200 Wh
// Fracción de la energía de carga que queda en la batería (AGM: pérdidas de carga).
#define EFICIENCIA_CARGA               0.90f
#define EFICIENCIA_DESCARGA            1.0f
// Reposo real: panel casi sin corriente (de noche) Y |I_bat| baja, sin interrupción
// durante REPOSO_REAL_S, y solo tras SIN_CARGA_S sin carga. 1,5 A ≈ C/67 para 100 Ah:
// deja pasar el inversor con el bombillo encendido (~1,15-1,21 A medidos el 23-nov); la
// compensación I·R corrige el voltaje a esa corriente. Con sol NO hay reposo real: los
// paneles sostienen el voltaje aunque la corriente de la batería sea ~0.
#define REPOSO_REAL_IPV_A              0.10f
#define REPOSO_REAL_IBAT_A             1.5f
#define REPOSO_REAL_S               1800      // 30 min continuos
// Sin carga reciente: tras cargar, la carga superficial infla el voltaje durante horas
// (23-nov: 12,65 V descargando a 1,2 A = 80 % en la tabla con ~65 % real). El reposo solo
// empieza a contar tras esto sin carga (iPv >= REPOSO_REAL_IPV_A o P media > +P_ESTABLE_W).
// Al arrancar se empieza en 0: tras un reinicio hacen falta 2 h sin carga + 30 min.
#define SIN_CARGA_S                 7200      // 2 h
#define REPOSO_DT_MAX_S               30      // un intervalo más largo (sin broker) lo reinicia
// En reposo real el SoC se acerca a la tabla con esta constante de tiempo (63 % en 30 min).
#define TAU_CORRECCION_S            1800.0f
// Estado publicado: con la potencia neta MEDIA (filtro de TAU_ESTADO_S). Entre
// −P_ESTABLE_W y +P_ESTABLE_W se conserva el último cargando/descargando.
#define TAU_ESTADO_S                  60.0f
#define P_ESTABLE_W                    6.0f   // ≈ 0,5 A a 12 V
// "cargada" (solo estado, el SoC no se lleva a 100 %): V de flotación (ficha CSB:
// 13,5-13,8 V), SoC alto y potencia media no negativa.
#define V_FLOTACION_MIN               13.40f
#define SOC_CARGADA_MIN               95.0f
// Al arrancar, un SoC guardado en la flash se restaura SIEMPRE, tenga la edad que tenga
// (marcado "aproximado"). Solo si no hay uno guardado se espera la hora NTP hasta esto
// (SoC publicado null) y luego se inicia por voltaje.
#define SOC_ESPERA_NTP_MS         120000UL

// Carga completa (soc::sincronizarCargaCompleta): V compensado >= V_CARGA_COMPLETA con
// corriente de cola (0 <= I_bat estimada < I_COLA_MAX_A) durante CARGA_COMPLETA_S
// continuos -> SoC = 100 %. Absorción de la CSB: 14,4-15,0 V; flotación 13,5-13,8 V
// (por eso la flotación sola NO sincroniza). Validar en un día soleado: ver README.
#define V_CARGA_COMPLETA              14.3f
#define I_COLA_MAX_A                   1.0f
#define CARGA_COMPLETA_S            1800      // 30 min continuos

// ---------------------------------------------------------------- SoC: recalibración guiada
// (soc_recalibracion.h/.cpp). La pide el dueño desde la app (domo/{id}/comando/soc); el
// valor lo mide la ESP32 en reposo y el dueño solo lo confirma.
#define RECAL_REPOSO_MIN              15      // minutos continuos en reposo
#define RECAL_MAX_MIN                 45      // duración máxima de la espera
#define RECAL_PROPUESTA_MIN            5      // vigencia de la propuesta para confirmarla
#define RECAL_IPV_MAX_A                0.10f  // sin sol
#define RECAL_IINV_MAX_A               0.5f   // inversor apagado o sin consumo
#define RECAL_IBAT_MAX_A               1.5f
#define RECAL_PUBLICAR_MS          30000      // con la sesión activa, el estado cada 30 s (y en cada cambio)
