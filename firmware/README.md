# Firmware del domo (ESP32)

`domo_nodo/` es el programa del ESP32 del domo. Hace tres cosas:
- **Mide** la corriente del panel, la corriente de la batería hacia el inversor, el voltaje de la
  batería y la salida AC del inversor.
- **Publica** esas mediciones por MQTT, cada 10 s, en los temas que guarda `backend/mqtt_listener.py`.
- **Ejecuta** las órdenes de la app sobre las tomas y la lámpara Tuya, por la red local y sin la nube.

Los otros sketches son pruebas sueltas:
- `EspTuya/`: el original de FrBerger83.
- `mqtt_test/`: prueba de MQTT.
- `wifi_test/`: prueba de WiFi.

> **Regla: nunca corras el simulador sobre un domo cuyo ESP32 publica telemetría real.**
> `backend/tools/simulador_telemetria.py` publica en los mismos temas que el ESP32: con los dos
> encendidos sobre el domo 1, el listener guarda las dos fuentes mezcladas en las mismas tablas,
> y R-13 corta o reactiva las tomas reales con un SoC inventado.
>
> Mientras el ESP32 esté conectado, el simulador solo se usa sobre otro domo
> (`--domo 2`). Si ya se mezclaron, anota la hora: no hay forma de separarlas después.
>
> El simulador ayuda a cumplir la regla:
> - `--domo` es obligatorio.
> - Si el domo recibió telemetría en los últimos 10 minutos (por ejemplo, del ESP32), se niega
>   a correr.
> - También se niega si no puede consultar la base.
>
> `--forzar` salta esa comprobación: no lo uses sobre el domo del ESP32.

## Archivos

| Archivo | Qué tiene |
|---|---|
| `domo_nodo.ino` | WiFi, MQTT, cola de órdenes, verificación de cada carga, validación de `secrets.h`, comandos `autotest` y `probar` |
| `TuyaLocal.ino` | Conexión TCP con un dispositivo Tuya: conectar, negociar la clave, mandar la orden o la consulta, leer las respuestas |
| `tuya_protocolo.h/.cpp` | **Protocolo Tuya local 3.3 / 3.4 / 3.5**: armar y abrir tramas, negociación de la clave de sesión, JSON de cada orden. C++ sin Arduino |
| `tuya_cripto.h`, `tuya_cripto_mbedtls.cpp` | AES-128-ECB, AES-128-GCM y HMAC-SHA256 con el mbedtls del core ESP32 |
| `tuya_autotest.h/.cpp`, `tuya_vectores.h` | Prueba del protocolo contra vectores generados con tinytuya (comando `autotest`) |
| `Descubrimiento.ino` | Escucha los anuncios UDP de Tuya: tabla ID → IP y versión |
| `Sensores.ino` | Tarea de muestreo, PZEM, SoC, publicación de la telemetría y comandos del monitor serie |
| `soc_estimador.h/.cpp` | **Estimador del SoC**: conteo de energía, corrección por voltaje en reposo real, carga completa, arranque y estado de la batería. C++ sin Arduino |
| `soc_recalibracion.h/.cpp` | **Recalibración guiada del SoC** desde la app: condiciones de reposo, sesión, propuesta y confirmación. C++ sin Arduino |
| `sensores_config.h` | **Todo el hardware de medición**: pines, divisores, sensibilidades, interruptores por sensor, tabla de SoC |
| `dispositivos_config.h` | Estructura de cada carga Tuya, DPS por defecto, tiempos y reintentos |
| `tipos_domo.h` | Tipos compartidos |
| `secrets.h` | WiFi, broker y lista de cargas Tuya. **No se versiona**; plantilla en `secrets.h.example` |

`pruebas_tuya/` (fuera del sketch, el IDE no lo compila) tiene el generador de los vectores y la
prueba del protocolo en el PC; ver "Protocolo Tuya" más abajo. `pruebas_soc/` tiene la prueba del
estimador de SoC y de la recalibración en el PC; ver "SoC".

El código Tuya empezó como EspTuya (FrBerger83), que solo hablaba 3.4 y 3.5. En la ronda F2 se
reescribió sobre la implementación de referencia de tinytuya y ya no usa la librería Crypto.

## Librerías

Todas se instalan desde el Gestor de librerías del IDE de Arduino:

| Librería | Versión probada | Para qué |
|---|---|---|
| ArduinoJson (Benoît Blanchon) | 7.4.3 | JSON de MQTT y de Tuya |
| PubSubClient (Nick O'Leary) | 2.8 | MQTT |
| **PZEM004Tv30 (Jakub Mandula)** | 1.2.1 | PZEM-004T v3 |

**Por qué PZEM004Tv30:**
- Es la librería de referencia para el PZEM-004T **v3.0**, que habla Modbus-RTU.
- En ESP32 acepta un `HardwareSerial` con los pines RX y TX, y aquí usa UART2 con los GPIO 16 y 17.
- Cuando el PZEM no responde devuelve `NAN` después de un timeout corto, sin colgarse. El firmware
  lo convierte en `null` y avisa en el monitor serie.
- Lee todos los registros en una sola consulta y la reutiliza durante 200 ms.

El cifrado de Tuya usa el **mbedtls que trae el núcleo ESP32** (no se instala nada). La librería
Crypto de Rhys Weatherley ya no hace falta.

Placa: **ESP32 Dev Module**, núcleo `esp32` de Espressif 3.x. Se compiló con 3.3.8: 77 % de la
flash (1 017 226 bytes) y 17 % de la RAM (55 804 bytes en variables globales).

## Pines

| Señal | Sensor | Conexión | GPIO | Notas |
|---|---|---|---|---|
| Corriente del panel (lado PV) | ACS712-20A, 100 mV/A | OUT → divisor 10k/20k | **34** | Factor 1,5. Positiva al generar |
| Corriente batería → inversor (DC) | ACS712-30A, 66 mV/A | OUT → divisor 10k/20k | **35** | Factor 1,5. Positiva al consumir |
| Voltaje de la batería | Divisor 47k/10k + 100 nF | punto medio | **32** | Factor 5,7, ajustable con `vbat` |
| Voltaje del panel | — | — | 33 libre | **No se mide**: la potencia del panel es estimada |
| PZEM-004T TX → ESP32 | PZEM-004T v3 | por un divisor 1k/2k (solo hardware) | **16** (RX2) | |
| ESP32 → PZEM-004T RX | PZEM-004T v3 | directo | **17** (TX2) | |
| Alimentación | LM2596 | 5 V | VIN / 5V | ACS712 y PZEM a 5 V. **GND común** con la batería |

Los tres analógicos están en el ADC1. El ADC2 no funciona con el WiFi encendido.

Cualquier cambio de hardware se hace solo en `sensores_config.h`. Cada sensor tiene su interruptor
(`USAR_ACS_PV`, `USAR_ACS_INVERSOR`, `USAR_VBAT`, `USAR_PZEM`), y con 0 sus campos salen como `null`.

## Qué publica

Cada 10 s (`PERIODO_TELEMETRIA_MS`), sin retener. Son 8640 filas por día en cada tabla.

| Tema | Campo | De dónde sale |
|---|---|---|
| `domo/1/telemetria/fotovoltaica` | `voltaje` | Siempre `null`: no se mide |
| | `corriente` | ACS712-20A, A |
| | `potencia_generada` | **Estimada**: corriente × `vpv` (60 V por defecto), W |
| | `energia_generada` | Integral de la potencia estimada en el intervalo, Wh |
| | `estado_generacion` | `generando` sobre 5 W, si no `sin_generacion` |
| `domo/1/telemetria/bateria` | `voltaje` | Divisor del GPIO32, V |
| | `corriente` | **Estimada**, + cargando: (potencia del panel × 0,95 − V_bat × I_inversor − 3 W) / V_bat |
| | `potencia` | **Estimada**, el mismo balance en W |
| | `estado_carga_soc` | Ver "SoC" |
| | `estado_bateria` | `cargando`, `descargando`, `en_reposo` o `cargada`, con la potencia neta media (ver "SoC") |
| | `temperatura` | No se envía: no hay sensor |
| `domo/1/telemetria/consumo` | `voltaje`, `corriente`, `potencia_consumida` | PZEM, salida AC del inversor |
| | `energia_consumida` | Integral de la potencia del PZEM en el intervalo, Wh |
| `domo/1/telemetria/sensores` | `frecuencia` (Hz) y `factor_potencia` | Cada 60 s, `tipo_sensor: "pzem004t"`, una fila por variable |

Detalles:
- **Sin dato:** si un sensor no responde, sus campos van como `null`, y `energia_*` también es
  `null` si faltó más de la mitad del intervalo. El listener guarda NULL.
- **Energía:** es la del intervalo, no el contador acumulado del PZEM. El contador tiene una
  resolución de 1 Wh, demasiado gruesa para intervalos de 10 s.
- **Voltaje del panel:** el log dice "ESTIMADA". En la app, el voltaje del panel aparece vacío.
- **Frecuencia y factor de potencia:** no tienen columna en `lecturas_consumo` y van a
  `lecturas_sensores`, que ya existe, así que el backend no cambia. Si algún día se quieren como
  columnas, la forma de no romper nada es una migración que agregue `lecturas_consumo.frecuencia` y
  `lecturas_consumo.factor_potencia`, ambas opcionales, sumarlas a `MAPEO_TIPOS` y a
  `LecturaConsumoCreate`, y moverlas del tema `sensores` al de `consumo`. Un firmware viejo seguiría
  funcionando porque los campos serían opcionales.

## Comandos del monitor serie (115200 baudios, fin de línea "Nueva línea")

| Comando | Qué hace |
|---|---|
| `cal` | Activa o desactiva la vista de calibración, cada 1 s: ADC crudo, mV en el pin, mV del sensor, cero y valor convertido de cada sensor, más el PZEM |
| `cero` | Recalibra el cero de los dos ACS712. **Sin corriente** |
| `vbat 12.73` | Ajusta el factor del divisor de la batería con lo que marca el multímetro |
| `vpv 58.4` | Voltaje del panel para la potencia estimada (el que muestra el MPPT) |
| `ipv 6.2` / `iinv 12.5` | Ajusta la ganancia de cada ACS712 con lo que marca una pinza DC (más de 1 A circulando) |
| `soc 66` | Fija el SoC a mano (0-100), lo guarda en la flash y lo imprime. Es el **único** comando con un valor libre (técnico). Sin hora NTP se guarda sin fecha; al reiniciar se restaura con edad desconocida |
| `recal` | Estado de la recalibración guiada (líneas `[RECAL]`). `recal iniciar`, `recal confirmar` y `recal cancelar` hacen lo mismo que los botones de la app, para probar sin ella. Ninguno acepta un valor |
| `fabrica` | Borra la calibración (y el SoC guardado) y vuelve a `sensores_config.h` |
| `estado` | Una lectura de todo y la lista de cargas Tuya: id_fisico, DPS, IP, versión, estado (sin local keys ni IDs completos) |
| `red` | Dispositivos Tuya que se anunciaron en la red: ID abreviado, IP, versión, hace cuánto, y a cuál de `secrets.h` corresponde |
| `autotest` | Prueba el protocolo 3.3, 3.4 y 3.5 con los vectores de tinytuya: imprime `Tuya 3.x: OK` o `FALLO` por versión. Sin red; funciona aun sin WiFi |
| `probar <id_fisico> on\|off` | Manda la orden a esa carga sin backend, la verifica e imprime `probar <id>: VERIFICADO, estado on, <ms> ms (intento 1)` o `NO VERIFICADO ...`. Si hay broker, también publica el estado, como cualquier orden |

La calibración queda en la flash (Preferences) y sobrevive a reinicios y a cargar un firmware nuevo.

## Cómo calibrar cada sensor

Abre el monitor serie y escribe `cal`.

### ACS712, cero

Un ACS712 da Vcc/2 (unos 2500 mV) sin corriente, pero ese valor cambia con cada sensor y con los
5 V reales del LM2596.
1. Abre el breaker del panel y apaga el inversor, para que no circule corriente por ninguno de los dos sensores.
2. Escribe `cero`. En la vista `cal`, los dos deben marcar 0,00 A, o casi.
3. Al arrancar, el ESP32 vuelve a medir el cero. Si el valor nuevo queda a más de 60 mV del
   guardado, que equivale a más o menos 0,6 A, supone que hay corriente, lo rechaza y lo avisa.
   Por eso conviene arrancar con el panel y el inversor desconectados la primera vez.

### ACS712: ¿está sano?

Antes de calibrarlo, con la vista `cal` y **sin corriente**:

1. Mide con el multímetro el Vcc del ACS712 (sus 5 V, entre VCC y GND del módulo).
2. En la línea del sensor, la columna **"sensor"** debe estar cerca de **Vcc/2**: con 5,00 V,
   alrededor de 2500 mV.
3. La columna **"pin"** debe estar cerca de **Vcc/2 ÷ 1,5**: con 5,00 V, alrededor de **1667 mV**
   (1,67 V). La vista `cal` muestra esas dos referencias.
4. Una diferencia de pocas decenas de mV es normal: para eso está `cero`.
5. Si "sensor" queda a más de unos 150 mV de Vcc/2, el problema puede ser el divisor, el GND
   común o el sensor mismo. También lo es si se pega cerca de 0 o de Vcc (en el pin, cerca de
   0 o de 3,3 V), o si no cambia al pasar corriente.
6. Comprueba el divisor midiendo directamente en el pin. Para descartar el sensor, prueba
   cambiándolo por otro.
7. Con corriente, el cambio en "sensor" debe ser de unos 100 mV por amperio en el de 20 A y de
   66 mV/A en el de 30 A. En "pin" son unos 67 y 44 mV/A. Si casi no se mueve, el sensor está
   dañado o la corriente no pasa por él.

### ACS712, ganancia

Necesitas una pinza amperimétrica DC, o un multímetro en serie si la corriente cabe en su rango.
1. Con al menos 2-3 A circulando, compara la pinza con el valor de la vista `cal`.
2. Si difieren, escribe `ipv <A de la pinza>` o `iinv <A de la pinza>`.
3. Repite con otra corriente. Si el error cambia mucho entre las dos, revisa el cableado antes de
   ajustar más.
4. Si una corriente sale negativa cuando debería ser positiva, cambia `ACS_PV_SIGNO` o
   `ACS_INV_SIGNO` a -1 en `sensores_config.h`. Mover el sensor no hace falta.

Límite del ACS712-30A: satura cerca de 30 A, que a 12 V son unos 360 W de entrada al inversor.
Desde 29 A (`ACS_INV_SATURACION_A`), la lectura se marca como **saturada**:
- El log avisa como máximo una vez por minuto, y la vista `cal` dice "SATURADO".
- La corriente y la potencia de la batería se publican como `null`, porque serían falsas, y
  `estado_bateria` como `descargando`.
- El SoC sigue contando la descarga con la lectura saturada, que es una cota inferior (la real
  es mayor). El log lo avisa una vez por episodio. Mientras dure, no hay reposo real.

### Voltaje de la batería

1. Mide con un multímetro en los bornes de la batería, con la batería en reposo: sin carga del
   panel ni consumo grande.
2. Escribe `vbat <voltios>`, por ejemplo `vbat 12.74`. El factor nuevo aparece en la vista `cal`;
   debe quedar cerca de 5,7.
3. Comprueba en otro momento, con otro voltaje. Debe coincidir con el multímetro dentro de ±0,05 V.

El ADC del ESP32 es más exacto por debajo de unos 2,45 V en el pin. Eso equivale a 14 V en la
batería con el factor 5,7. Calibra cerca de 12,8 V, y ten en cuenta que en carga (14,4-15 V) la
lectura es un poco menos precisa.

### Potencia del panel (estimada) y su error

El voltaje del panel no se mide, así que potencia = corriente medida × voltaje fijado con `vpv`.
1. Mira en la pantalla del MPPT el voltaje del panel (PV) mientras genera, y escribe `vpv <ese voltaje>`.
2. Para medir el error, anota a la misma hora, en varios momentos del día (mañana, mediodía,
   nublado, tarde):

   | Hora | V del panel (MPPT) | W del panel (MPPT) | W estimados (log "Telemetría") | Error % |
   |---|---|---|---|---|

   Error % = (estimado − MPPT) / MPPT × 100. Si tu MPPT no muestra la potencia del panel, usa su
   voltaje × la corriente medida.
3. El error crece cuando el voltaje real del panel se aleja del fijado, porque el punto de máxima
   potencia cambia con la temperatura y la luz. Si se mueve mucho a lo largo del día, ese error
   queda en los reportes. La solución definitiva es medir el voltaje del panel con un divisor en el
   GPIO33, que quedó libre para eso.

### PZEM-004T

1. Compara su voltaje con un multímetro en la salida AC del inversor. Compara su corriente con una
   pinza AC sobre la fase, con una carga conocida (un bombillo de potencia conocida, un cargador).
2. El PZEM viene calibrado y no se ajusta desde el firmware. Si no coincide, revisa que la fase
   pase por dentro del transformador de corriente y que el divisor de su TX esté bien.
3. Si en el log aparece "el PZEM-004T no responde", revisa:
   - que su lado AC tenga tensión, porque sin ella no responde;
   - el GND común;
   - el cruce de TX y RX;
   - el divisor 1k/2k.

### Compensación del voltaje bajo carga (R_COMPENSACION_OHM)

1. Con la batería en reposo, anota V1.
2. Enciende una carga DC conocida, por ejemplo 10 A en el inversor, espera 10 s y anota V2 y la corriente I.
3. R = (V1 − V2) / I. Ponla en `sensores_config.h`. Incluye la resistencia interna, de unos 3 mΩ
   según la ficha CSB, más la de cables y bornes.

### SoC

El estimador está en `soc_estimador.h/.cpp` y sus constantes en `sensores_config.h` (sección
"SoC: estimador").

- **Conteo de energía, siempre.** La tarea de sensores integra cada 100 ms la potencia neta
  ESTIMADA de la batería (la misma que se publica: P_FV × 0,95 − V × I_inversor − 3 W). En cada
  publicación: SoC += (Wh de carga × 0,90 − Wh de descarga) / 1200 Wh × 100, entre 0 y 100.
  Sin broker la energía se sigue acumulando y se aplica al reconectar.
- **Corrección por voltaje, solo en reposo real:** corriente del panel < 0,1 A (de noche) y
  |I_bat| < 1,5 A (≈ C/67; deja pasar el inversor con el bombillo encendido, ~1,2 A), sin
  interrupción durante 30 min, y **sin carga reciente**: el reposo solo empieza a contar tras 2 h
  sin carga (`SIN_CARGA_S`). "Carga" es corriente del panel ≥ 0,1 A o potencia neta media
  > +6 W. Entonces el SoC se acerca al de la tabla (con V − I·R) con un filtro lento: 63 % de la
  diferencia en 30 min. Si la condición se rompe, el contador de reposo vuelve a 0; si hay carga,
  también el de sin carga. Un intervalo de más de 30 s (sin broker) no suma tiempo sin carga.
  - **Por qué no con sol:** los paneles alimentan las cargas y sostienen el voltaje (13,2 V)
    aunque la batería casi no tenga corriente. La tabla daba 100 %, el SoC subía solo y R-13
    reactivaba tomas.
  - **Por qué 2 h sin carga:** tras cargar, la carga superficial infla el voltaje durante horas
    aunque la batería descargue (23-nov: 12,65 V a −1,2 A daban 80 % en la tabla con ~65 % real).
    Sin esta condición, una nube de 30 min de día habría llevado el SoC a ese valor falso.
  - **Al arrancar** el contador sin carga empieza en 0: tras un reinicio la primera corrección
    llega, como pronto, a las 2 h 30 min. Mientras tanto el SoC sigue por conteo.
- **Tabla:** voltaje en reposo → SoC, a 25 °C.
  - **En uso:** una tabla genérica de AGM, más conservadora, que eligió el equipo: 12,85 V = 100 %,
    12,35 V = 50 %, 11,65 V = 0 %. No viene de una ficha concreta.
  - **Alternativa:** la que Trojan publica para AGM (12,84 V = 100 %, 12,24 V = 50 %,
    11,64 V = 0 %). Está comentada en el mismo archivo.
  - Lo mejor es reemplazarlas por la curva "Open circuit voltage vs. residual capacity" de la ficha
    de la CSB HRL12390W.
- **`estado_bateria`**, con la potencia neta media de ~1 min (no la instantánea):
  - `en_reposo`: solo en reposo real (los 30 min de arriba);
  - `cargada`: 13,4 V o más, SoC ≥ 95 % y sin descarga. Es **solo un estado**: no lleva el SoC a 100 %;
  - `cargando` por encima de +6 W y `descargando` por debajo de −6 W;
  - entre −6 y +6 W ("estable", que el backend no tiene) se conserva el último de los dos.
- **Al arrancar** (`soc::decidirArranque`): si hay un SoC guardado válido (0-100) en la flash, se
  restaura **siempre, tenga la edad que tenga**, y el log, `cal` y `estado` dicen "restaurado de
  flash (aproximado), hace X h" (o "edad desconocida" sin hora NTP o si se guardó sin fecha).
  Antes solo se restauraba si tenía menos de 6 h y si no, salía de la tabla con la batería cargando
  (24-nov: tras más de 6 h apagada, 13,75 V cargando dieron **100 %** con ~58 % real). Un SoC viejo
  es mejor punto de partida que la tabla bajo carga; si la ESP32 estuvo apagada mucho tiempo, o con
  sol mientras estaba apagada, recalíbralo desde la app (ver "Recalibración guiada").
  Solo si **no hay** uno guardado se usa la tabla, como antes: se espera la hora NTP hasta 2 min (el
  SoC va `null`) y luego sale de la tabla con el voltaje compensado ("SoC inicial por voltaje
  (aproximado)").
- **En flash:** cada 5 min, si cambió 0,5 puntos o más (`soc` y `socT`, con hora NTP).
- **Vista `cal` y `estado`:** una línea con el SoC, la fuente del último cambio (conteo,
  corrección por voltaje o carga completa) y cómo se inició (con la edad si se restauró), "sin carga
  hace X/120 min" (o "cargando ahora"), los minutos de reposo real y de carga completa, el valor de
  la tabla con el voltaje compensado, la potencia media y el estado. Si hay una recalibración, su
  línea `[RECAL]`.
- **Límites:** el conteo hereda el error de la potencia estimada (el voltaje del panel no se mide)
  y deriva con los días; lo corrigen un reposo real de noche y la carga completa.
- **Carga completa: sincronizar a 100 %** (`soc::sincronizarCargaCompleta`, constantes
  `V_CARGA_COMPLETA`, `I_COLA_MAX_A` y `CARGA_COMPLETA_S`). Si el voltaje compensado es
  **≥ 14,3 V** de forma **continua ≥ 30 min** y la corriente de carga estimada es **≥ 0 y < 1,0 A**
  (corriente de cola), el SoC pasa a 100 % y el log dice `[SoC] carga completa detectada: X % →
  100 %`. Cualquier muestra que no cumpla (o un intervalo de más de 30 s sin broker) reinicia el
  contador. Es lo único, aparte del conteo, que lleva el SoC a 100 %.
  - **Por qué 14,3 V y no la flotación:** 14,3 V solo se alcanzan en absorción (CSB: 14,4-15,0 V).
    En flotación (13,5-13,8 V) o con sol a media carga (13,2-13,8 V con 0,2-2,5 A, como el 24-nov
    con ~58 %) **no** sincroniza. En bulk (14,6 V con 3 A) tampoco: falta la corriente de cola.
  - **Riesgo 1, flotación por tiempo:** si el MPPT pasa a flotación por tiempo de absorción y no
    por corriente de cola, puede que nunca haya 30 min seguidos ≥ 14,3 V con < 1 A, y entonces no
    sincroniza (el SoC sigue por conteo; no es peligroso, solo no corrige).
  - **Riesgo 2, I_bat ESTIMADA:** la corriente de la batería no se mide: es (P_FV con 60 V
    nominales × 0,95 − V × I_inversor − 3 W) / V. Si se **subestima** (p. ej. el voltaje real del
    panel es mayor que `vpv`, o la ganancia del ACS712-20A es baja), la condición de cola < 1 A
    podría cumplirse antes de tiempo, con la batería todavía cargando fuerte. Lo mitiga exigir
    ≥ 14,3 V **sostenidos** 30 min, que la batería no aguanta lejos de llena.
  - **Validar en un día soleado:** con `cal` o `estado`, mira "carga completa X/30 min", el
    voltaje y la corriente estimada durante la absorción; contrasta con lo que muestra el MPPT
    (etapa Absorption/Float y corriente) y, si puedes, con una pinza DC en la batería. Si se
    sincroniza muy pronto, baja `I_COLA_MAX_A` o sube `CARGA_COMPLETA_S`; si nunca, revisa el
    voltaje de absorción del MPPT y `vbat`.
- **Prueba en el PC** (sin placa), con el gcc de MSYS2 (`C:\msys64\ucrt64\bin`), desde `firmware/`:
  ```
  g++ -std=c++17 -Wall -static -o prueba_soc.exe pruebas_soc/prueba_soc.cpp domo_nodo/soc_estimador.cpp domo_nodo/soc_recalibracion.cpp
  ./prueba_soc.exe
  ```
  Usa la tabla y las constantes de `sensores_config.h`. Reproduce los casos del 23/11 (11:40,
  +20 W con sol; 12:00, "en_reposo" descargando a 13,2 V): el SoC ya no sube. Y el de las 13:27:
  recién terminada la carga, descargando a 1,2 A sin sol, no corrige; tras 2 h sin carga + 30 min,
  sí; una nube de 40 min tras cargar, no; un reinicio exige las 2 h. También el arranque (guardado
  de hace 21 h → se restaura; sin guardado → tabla), la carga completa (casos a-d de arriba) y la
  recalibración guiada (sol → no; inversor con 2 A → no; 15 min en reposo → lista; interrupción a
  los 10 min → vuelve a 0; confirmar con la propuesta vencida → rechazada).

### Recalibración guiada del SoC (desde la app)

En el domo no hay un computador conectado a la ESP32: el dueño corrige el SoC desde la app
(Configuración → "Recalibrar el estado de carga"). **El dueño no escribe un valor**: solo pide que el
sistema mida en condiciones válidas; el valor lo calcula la ESP32 con la tabla y el voltaje
compensado (las mismas funciones que la corrección en reposo) y el dueño solo lo confirma. Así nadie
deja el SoC en un valor inventado con el que R-13 decidiría mal. El valor libre queda solo en el
comando serie `soc` (técnico).

- **Pedido** (`domo/1/comando/soc`, QoS 1, nunca retenido; lo publica el backend):
  `{"accion":"iniciar"|"confirmar"|"cancelar"}`. Cualquier otro campo, p. ej. un SoC, se ignora.
- **iniciar:** empieza una sesión de hasta 45 min (`RECAL_MAX_MIN`). En cada muestra (10 s):
  corriente del panel < 0,10 A (sin sol), corriente del inversor < 0,5 A (saturado o sin lectura
  **no** cumple) y |I_bat estimada| < 1,5 A. Hacen falta **15 min seguidos** (`RECAL_REPOSO_MIN`);
  una muestra que no cumpla, o un intervalo de más de 30 s, vuelve el contador a 0 y publica el
  motivo ("hay sol…", "hay consumo en el inversor"…).
- **lista:** a los 15 min calcula el SoC de la tabla con el voltaje compensado y lo publica como
  propuesta (`soc_actual`, `soc_propuesto`, `voltaje`). **No lo aplica todavía.**
- **confirmar:** aplica la propuesta con `fijarSoc`, la misma función del comando `soc`, y la
  guarda en la flash (inicio "recalibrado desde la app"). Solo si sigue en `lista` y la propuesta
  tiene menos de 5 min (`RECAL_PROPUESTA_MIN`); si no, `rechazada`. Una propuesta sin confirmar en
  5 min queda `vencida`; también una espera de más de 45 min. Un `iniciar` nuevo descarta la
  propuesta anterior.
- **cancelar:** termina sin cambios (`cancelada`).
- **Estado** (`domo/1/estado/soc`, **retenido**: es estado, no comando):
  `{"estado":"inactiva|esperando|lista|aplicada|cancelada|vencida|rechazada","minutos_ok":x,"requeridos":15,"motivo":"…","soc_actual":…,"soc_propuesto":…,"voltaje":…,"t":<hora Unix>}`.
  Se publica al cambiar y cada 30 s mientras la sesión está activa. **Al arrancar** (y al
  reconectar al broker) se publica el estado actual, `inactiva` tras un reinicio: reemplaza un
  `esperando`/`lista` retenido de antes, que ya no existe. El backend marca `interrumpida` una
  sesión activa sin noticias por más de 90 s.
- **Cuándo usarla:** la ESP32 estuvo apagada o el SoC parece incorrecto. Mejor de noche y con el
  inversor apagado. **Ojo:** tras cargar, el voltaje queda algo alto unas horas (carga superficial)
  y el valor medido saldría alto: espera unas horas después de la carga.

## Dispositivos Tuya

### Equipos del prototipo

| id_fisico | Equipo | IP | Protocolo | DPS que controla | Otros DPS del equipo |
|---|---|---|---|---|---|
| `bombillo1` | Bombillo RGB + blanco | 192.168.0.160 | 3.5 | `20` encendido | `21` modo (`white`/`colour`/`scene`), `22` brillo 10–1000, `23` temperatura 0–1000, `24` color |
| `toma2` | Toma sencilla | 192.168.0.161 | 3.4 | `1` | — |
| `toma1` | Tomacorriente de pared, salida 1 (en la app: "Toma cargadores") | 192.168.0.162 | 3.3 | `1` | — |
| `toma1_s2` | El mismo tomacorriente, salida 2 | 192.168.0.162 | 3.3 | `2` | — |
| `toma1_usb` | El mismo tomacorriente, puertos USB | 192.168.0.162 | 3.3 | `3` | — |

- **Una salida = una carga:** cada salida del tomacorriente de pared es un dispositivo aparte en la
  app, con su `id_fisico` y su categoría, para que R-13 corte primero la menos importante. En
  `secrets.h` son tres líneas con el mismo ID de Tuya, IP, local key y versión, y distinto DPS.
  En la app hay que crear (o editar) `toma1_s2` y `toma1_usb` como tomas reales con ese
  `id_fisico`; `toma1` ya existe.
- **IPs del prototipo** (confirmadas con el router y tinytuya, reservadas en el router):
  `bombillo1` 192.168.0.160, `toma2` 192.168.0.161 y las tres salidas del tomacorriente de pared
  192.168.0.162. El broker está en 192.168.0.150. El comando `red` muestra las anunciadas.
- Las local keys no van en este README ni en el repositorio: solo en `secrets.h`.

### Formato de `secrets.h`

```c
// { id_fisico, tipo, ID de Tuya, IP, local key, versión, DPS }
#define DISPOSITIVOS_TUYA {                                                                                  \
    { "toma1",     TIPO_TOMA,     "ID_TUYA_TOMA1",     { 192, 168, 0, 162 }, "LOCAL_KEY_TOMA_1", 33, 1  },  \
    { "toma1_s2",  TIPO_TOMA,     "ID_TUYA_TOMA1",     { 192, 168, 0, 162 }, "LOCAL_KEY_TOMA_1", 33, 2  },  \
    { "toma1_usb", TIPO_TOMA,     "ID_TUYA_TOMA1",     { 192, 168, 0, 162 }, "LOCAL_KEY_TOMA_1", 33, 3  },  \
    { "toma2",     TIPO_TOMA,     "ID_TUYA_TOMA2",     { 192, 168, 0, 161 }, "LOCAL_KEY_TOMA_2", 34, 1  },  \
    { "bombillo1", TIPO_BOMBILLO, "ID_TUYA_BOMBILLO1", { 192, 168, 0, 160 }, "LOCAL_KEY_BOMB_1", 35, 20 },  \
}
```

| Campo | Qué va |
|---|---|
| `id_fisico` | **Igual** al `id_fisico` del dispositivo en la app. Letras, números, `_` y `-`, hasta 32 caracteres (va en el tema MQTT) |
| `tipo` | `TIPO_TOMA` o `TIPO_BOMBILLO` |
| ID de Tuya | El "device id". **Obligatorio**: va en las órdenes 3.3, reconoce el anuncio UDP y agrupa las salidas de un mismo equipo |
| IP | `{0, 0, 0, 0}` = la que el dispositivo anuncia en la red (ver "Descubrimiento"). Si la escribes, manda la escrita |
| local key | 16 caracteres, tal cual la entrega `python -m tinytuya wizard` (ver la advertencia de abajo) |
| versión | `33`, `34` o `35`; `0` = la anunciada |
| DPS | El DPS de encendido de **esa** carga. `0` o sin poner: `1` en tomas, `20` en bombillos. En un bombillo, el modo y el brillo son DPS+1 y DPS+2 |

Al arrancar se valida cada línea. Una línea que no cumple queda **desactivada** y el monitor dice
por qué: id_fisico repetido o con caracteres raros, local key que no tiene 16 caracteres, versión
distinta de 0/33/34/35, falta el ID, o dos líneas del mismo equipo con distinta IP, clave o
versión, o con el mismo DPS.

**Ojo al actualizar:** el formato cambió respecto a la ronda F1 (el ID va antes que la IP, la
versión es 33/34/35 en vez de 4/5, y hay un campo DPS). Un `secrets.h` del formato viejo no
compila o deja las líneas desactivadas: pásalo al nuevo copiando de `secrets.h.example`.

> **`python -m tinytuya wizard`, `scan` y `snapshot` imprimen las local keys** en la consola y
> las guardan en archivos. Córrelos **solo en `$HOME\tuya_local`** (fuera del repositorio y de
> OneDrive) y **nunca pegues su salida** en ningún lado: ni en un chat, ni en un issue, ni en un
> commit. Para comparar con el comando `red`, imprime solo nombre, IP y versión desde esa carpeta:
>
> ```
> python -c "import json; [print(d.get('name'), d.get('ip'), d.get('ver')) for d in json.load(open('snapshot.json'))['devices']]"
> ```
>
> **Corre tinytuya FUERA del repositorio y de OneDrive**: en `$HOME\tuya_local`.
> `wizard` y `scan` escriben `devices.json`, `tinytuya.json`, `tuya-raw.json` y `snapshot.json` en
> la carpeta donde se ejecutan, y ahí quedan las local keys y la clave de tu cuenta de Tuya IoT.
> - **Dentro del repo**, un `git add .` los subiría. Por eso están en el `.gitignore` de la raíz,
>   en cualquier carpeta, pero el `.gitignore` es solo una red de seguridad.
> - **Dentro de OneDrive**, se sincronizan a la nube aunque git los ignore.
>
> Copia a `secrets.h` solo los datos que necesitas, y guarda esos JSON donde guardas otras claves.

### Órdenes y verificación

- **Bombillo:** encender y apagar van por el DPS 20. Al cambiar el brillo, la misma orden lleva
  `{"21":"white","22":<valor>}`: la app manda 0–100 y el firmware lo lleva a 10–1000. En modo
  color, el DPS 22 no cambia lo que se ve; por eso siempre se pide el modo blanco.
- **Verificación:** `verificado: true` solo si el dispositivo informa **el DPS de esa línea** con el
  valor pedido, en la respuesta a la orden o en una consulta de estado en la misma conexión. Si en
  la respuesta vienen otras salidas del mismo equipo, también se anota su estado.
- **Reintentos:** cada orden se intenta 3 veces (conectar, negociar, mandar, verificar). Si las 3
  fallan, se publica el último estado confirmado, no el pedido.
- **En serie por equipo:** el ESP32 abre una sola conexión a la vez y ejecuta las órdenes de a una,
  en el orden en que llegaron. Dos órdenes seguidas de R-13 a `toma1_s2` y `toma1_usb` quedan como
  dos órdenes en la cola y se aplican las dos, con 400 ms entre la conexión de una y la de la otra
  (`PAUSA_MISMO_FISICO_MS`): algunos equipos aceptan una sola conexión local y tardan en liberarla.
  Solo se reemplaza una orden pendiente cuando llega otra para **la misma** línea.
- El estado se publica retenido en `domo/1/estado/{toma|bombillo}/<id_fisico>`; ver el contrato en
  `docs/arquitectura_sistema.md`. Si la IP o la versión van en 0 y el dispositivo todavía no se ha anunciado, la
  orden no se intenta y el estado lleva `"error": "ip_desconocida"`.
- **La app Tuya/Smart Life abierta** en un celular de la misma red puede ocupar la conexión local de
  algunos equipos: si `probar` dice "sin conexión TCP", ciérrala y repite.

### Protocolo Tuya

`tuya_protocolo.h/.cpp` implementa el protocolo local siguiendo tinytuya (`core/message_helper.py`,
`core/header.py`, `core/XenonDevice.py`; licencia MIT), citado en los comentarios:

| Versión | Trama | Cifrado | Integridad | Sesión |
|---|---|---|---|---|
| 3.3 | `55AA` | AES-128-ECB con la local key; el encabezado `3.3` + 12 ceros va en claro delante | CRC32 | No |
| 3.4 | `55AA` | AES-128-ECB con la clave de sesión; el encabezado `3.4` va dentro de lo cifrado | HMAC-SHA256 (32 bytes) | Sí |
| 3.5 | `6699` | AES-128-GCM con la clave de sesión, IV de 12 bytes al azar por trama | Tag GCM de 16 bytes (autentica también el encabezado) | Sí |

- **Negociación (3.4 y 3.5):** el ESP32 manda un nonce al azar (`SESS_KEY_NEG_START`); el
  dispositivo responde su nonce y el HMAC-SHA256 del nuestro con la local key
  (`SESS_KEY_NEG_RESP`). Si ese HMAC no coincide, el dispositivo no conoce la clave y se aborta.
  Si coincide, el ESP32 manda el HMAC del nonce del dispositivo (`SESS_KEY_NEG_FINISH`) y ambos
  calculan la clave de sesión: AES de (nonce local XOR nonce remoto) con la local key.
- **Órdenes:** 3.3 usa `CONTROL` (7) con `{"devId":..,"uid":..,"t":"..","dps":{..}}`; 3.4 y 3.5
  usan `CONTROL_NEW` (13) con `{"protocol":5,"t":..,"data":{"dps":{..}}}`. Consultas: `DP_QUERY`
  (10) en 3.3 y `DP_QUERY_NEW` (16) en 3.4/3.5. Los 3.3 que contestan "data unvalid" se consultan
  como "device22" (CONTROL_NEW con los DPS en null), igual que tinytuya.
- **Respuestas:** toda trama recibida se verifica (CRC, HMAC o tag) antes de usarla; si no
  coincide se descarta y se avisa en el monitor (suele ser local key o versión equivocadas).
- **Pruebas:** `pruebas_tuya/generar_vectores.py` arma con las funciones de tinytuya, y con claves,
  nonces, IV e ID **ficticios**, las tramas de cada versión: negociación (también una respuesta de
  un impostor con otra clave), orden, consulta, acuse y STATUS. Las guarda en
  `domo_nodo/tuya_vectores.h`. El comando `autotest` las reproduce en la ESP32 byte a byte, y
  además comprueba que una trama alterada se rechace.
  - Regenerar: `backend/.venv/Scripts/python firmware/pruebas_tuya/generar_vectores.py`. La prueba
    `backend/tests/test_salidas_tuya.py` avisa si el archivo quedó desactualizado.
  - En el PC (sin placa), con el gcc de MSYS2 (`C:\msys64\ucrt64\bin`), desde `firmware/`:
    ```
    g++ -std=c++17 -Wall -o prueba_tuya.exe pruebas_tuya/prueba_pc.cpp pruebas_tuya/cripto_windows.cpp domo_nodo/tuya_protocolo.cpp domo_nodo/tuya_autotest.cpp -lbcrypt
    ./prueba_tuya.exe
    ```
    En el PC el cifrado lo hace la API de Windows (bcrypt), no mbedtls: prueba el protocolo,
    no las llamadas a mbedtls. Esas las prueba `autotest` en la placa.

### Descubrimiento

`Descubrimiento.ino` escucha los anuncios UDP que los dispositivos Tuya envían a toda la red:

| Puerto | Versión | Formato |
|---|---|---|
| 6666 | 3.1 | JSON sin cifrar en un paquete 55AA |
| 6667 | 3.3 y 3.4 | Paquete 55AA; payload cifrado con AES-128-ECB y relleno PKCS#7. Se anuncian solos, cada pocos segundos |
| 7000 | 3.5 | Paquete 6699 cifrado con AES-128-GCM. **Solo responden si se les pide**: cada 30 s el ESP32 envía a `255.255.255.255:7000` un 6699 con el comando `REQ_DEVINFO` (0x25) y `{"from":"app","ip":"<IP del ESP32>"}` |

- **Clave:** la clave de los anuncios es fija e igual para todos los dispositivos:
  `md5("yGAdlopoPVldABfn")` = `6c1ec8e2bb9bb59ab50b0daf649b410a`, la `UDP_key` del firmware.
  Fuente: tinytuya, `core/udp_helper.py` (`udpkey` y `decrypt_udp`), `core/const.py` (puertos) y
  `scanner.py` (petición a 7000).
- **Por qué no es secreto:** como esa clave es pública, cualquiera en la red puede leer el
  anuncio. Pero el anuncio no trae la local key, que es la que protege las órdenes.
- **Formato 6699:** en el encabezado van prefijo, 2 bytes, secuencia, comando y largo. Luego van
  un IV de 12 bytes, el texto cifrado, un tag de 16 bytes y el sufijo. Los datos autenticados son
  los 14 bytes que siguen al prefijo.
- **Qué se guarda:** del JSON descifrado, `gwId` (el ID de Tuya), `ip` y `version`, en la tabla.
  Si un dispositivo aparece con otra IP, se actualiza y se avisa en el log.
- **No bloquea:** en cada vuelta lee como máximo 4 paquetes por puerto, y usa sus propios buffers.
  Corre también durante una orden a un dispositivo, sin tocar la sesión Tuya en curso.
- **Para verlo:** el comando `red` muestra la tabla sin IDs completos ni local keys. `estado` dice
  de dónde salió la IP de cada dispositivo: escrita, descubierta o sin IP.
- **Restricción de red:** el ESP32 y los dispositivos deben estar en la misma red y la misma
  subred. Un router con "aislamiento de clientes" (AP isolation) bloquea los broadcasts y el
  control local.
Mientras el ESP32 habla con un dispositivo (`probar` imprime cuánto tarda), sigue atendiendo MQTT y
publicando telemetría, y los sensores se siguen midiendo en su propia tarea.

## Plan de prueba

Abre el monitor serie a 115200. En cada paso, anota lo que ves.

1. **Sin sensores**: pon los cuatro `USAR_*` en 0 y carga el firmware.
   - Debe conectar al WiFi y a Mosquitto, y listar los dispositivos Tuya.
   - Mira la telemetría con `mosquitto_sub -h <broker> -t 'domo/1/#' -v`: todos los campos de
     sensores deben llegar en `null`.
2. **Voltaje de la batería** (`USAR_VBAT 1`):
   - En la vista `cal`, compara con el multímetro y ajusta con `vbat`.
   - Comprueba que `estado_carga_soc` sea razonable para ese voltaje.
3. **ACS712-30A del inversor**:
   - Con el inversor apagado, escribe `cero`.
   - Enciende el inversor sin carga y anota su consumo en vacío.
   - Conecta una carga conocida, compara con la pinza DC y ajusta con `iinv`.
   - Verifica que el signo sea positivo.
4. **ACS712-20A del panel**:
   - Con el breaker del panel abierto, escribe `cero`. Ciérralo de día.
   - Compara con la pinza y ajusta con `ipv`.
   - Escribe `vpv` con el voltaje del MPPT y llena la tabla de error.
5. **PZEM**:
   - Con una carga conocida en el inversor, compara voltaje, corriente y potencia.
   - Desconecta el TX del PZEM 30 s: debe aparecer el error, publicarse `null`, y el ESP32 debe
     seguir publicando lo demás. Al reconectarlo, debe decir "responde de nuevo".
6. **Protocolo, sin red** (`autotest`): con la placa recién cargada, escribe `autotest`. Deben salir
   `Tuya 3.3: OK`, `Tuya 3.4: OK`, `Tuya 3.5: OK` y `autotest: OK (0 fallos)`. Si alguna versión
   dice FALLO, no sigas: copia las líneas `FALLO: ...` (no contienen claves).
7. **Descubrimiento**: con los dispositivos encendidos, escribe `red`. En menos de un minuto deben
   aparecer los tres equipos: .160 en 3.5, .161 en 3.4 y .162 en 3.3. Compáralos con el
   one-liner de `snapshot.json` de la sección "Formato de `secrets.h`", corrido en
   `$HOME\tuya_local` (no con la salida de `scan`, que imprime las local keys). Escribe `estado`: las cinco líneas deben
   decir `ok`, con su DPS.
8. **Cada carga desde el monitor, sin backend** (`probar`). Con el broker apagado o no, da igual:
   ```
   probar toma1 on        probar toma1 off
   probar toma1_s2 on     probar toma1_s2 off
   probar toma1_usb on    probar toma1_usb off
   probar toma2 on        probar toma2 off
   probar bombillo1 on    probar bombillo1 off
   ```
   - Cada una debe terminar en `probar <id>: VERIFICADO ...` y la salida correcta debe cambiar a la
     vista (en `toma1_s2` y `toma1_usb`, **solo** esa salida). Anota el tiempo de cada una.
   - **En serie:** escribe seguidas, sin esperar, `probar toma1_s2 off` y `probar toma1_usb off`.
     Deben salir los dos `VERIFICADO` y apagarse las dos salidas.
   - Si sale "sin conexión TCP", cierra la app Tuya/Smart Life en el celular y repite. Si sale
     "no prueba que el dispositivo conozca la local key" o "trama descartada", revisa la local
     key y la versión de esa línea en `secrets.h`.
9. **Órdenes desde la app** (listener y API corriendo):
   - Crea en la app `toma1_s2` y `toma1_usb` como tomas reales con ese `id_fisico` y su
     categoría (por ejemplo, USB prescindible y salida 2 de confort).
   - Enciende y apaga cada toma desde la app. En `domo/1/estado/toma/<id>` debe aparecer
     `verificado: true`, y la telemetría no debe dejar de llegar mientras se ejecuta.
   - Bombillo: encender, apagar y brillo 10, 50 y 100. Comprueba
     `domo/1/estado/bombillo/bombillo1` y que el brillo se vea en blanco aunque estuviera en color.
10. **R-13**: baja los umbrales de `toma1_usb` y `toma1_s2` (nunca con la nevera real) para que
    el SoC actual quede por debajo de los dos. En la siguiente evaluación deben apagarse primero
    la USB y luego la salida 2, cada una con su `verificado: true`, y `toma1` debe seguir
    encendida. Vuelve a subir los umbrales y comprueba que se reactiven.
11. **Todo junto**:
   - Una hora con todo conectado y el simulador APAGADO.
   - Revisa en la app que las lecturas lleguen cada 10 s, y que el SoC y los estados de la batería
     tengan sentido con lo que hace el MPPT.
   - Prueba R-13 bajando los umbrales de una toma de prueba, nunca con la nevera real.
