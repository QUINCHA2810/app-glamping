#pragma once
// Tipos compartidos entre los .ino. Van en un .h porque el IDE de Arduino
// genera los prototipos de las funciones antes de cualquier tipo declarado en
// un .ino, y una función que use uno de esos tipos no compilaría.

#include <Arduino.h>

// Una orden pendiente para un dispositivo Tuya. -1 = ese campo no viene.
struct Orden {
    int  disp;          // índice en DISPOSITIVOS (una entrada = un id_fisico)
    int  accion;        // 1 = encender, 0 = apagar, -1 = sin cambio
    int  brillo;        // 0-100 (solo bombillos), -1 = sin cambio
    bool desdeSerie;    // la pidió "probar" en el monitor serie: se imprime el resultado
    unsigned long recibida;   // millis() al anotarla
    // Solo bombillos. modo -1 = sin modo: se comporta como antes (accion y brillo).
    int  modo;          // MODO_BLANCO, MODO_COLOR, MODO_ESCENA o -1
    int  temperatura;   // 0-100 (0 = más cálido), -1 = sin cambio
    int  h;             // 0-360, -1 = sin cambio
    int  s;             // 0-100, -1 = sin cambio
    int  escena;        // índice en ESCENAS_BOMBILLO (dispositivos_config.h), -1 = ninguna
};

#define MODO_BLANCO  0
#define MODO_COLOR   1
#define MODO_ESCENA  2
#define MODO_OTRO    3      // el bombillo informó otro modo (p. ej. "music")

// Una orden vacía (todos los campos sin cambio). Los campos se ponen aparte.
inline Orden nuevaOrden(int disp, bool desdeSerie) {
    Orden o = { disp, -1, -1, desdeSerie, millis(), -1, -1, -1, -1, -1 };
    return o;
}

// Qué falta ver en las respuestas del dispositivo para verificar una orden (máscara).
#define FALTA_SWITCH  1
#define FALTA_BRILLO  2
#define FALTA_MODO    4
#define FALTA_TEMP    8
#define FALTA_COLOR  16

// Lo que mide la tarea de sensores. La escribe esa tarea y la copia el loop,
// siempre dentro de muxLecturas.
struct Lecturas {
    // ADC: mV en el pin (filtrados) y una lectura cruda de 12 bits
    float    mvPv, mvInv, mvVbat;
    int      crudoPv, crudoInv, crudoVbat;
    bool     hayPv, hayInv, hayVbat;
    // Convertidos
    float    iPv;      // A del panel (lado PV), >= 0 generando
    float    iInv;     // A de la batería hacia el inversor, >= 0 consumiendo
    bool     invSaturado;          // |iInv| >= ACS_INV_SATURACION_A en algún bloque del intervalo
    float    vBat;     // V de la batería
    // PZEM-004T (salida AC del inversor)
    bool     pzemOk;
    float    acV, acI, acP, acKwh, acHz, acFp;
    uint32_t pzemFallos;
    // Integrados desde la última publicación. whCarga/whDescarga: potencia neta ESTIMADA
    // de la batería (P_FV·η − V·I_inv − propio) por bloque de 100 ms, para el conteo del SoC.
    double   whGen, whCons, whCarga, whDescarga;
    uint32_t msGen, msCons, msTotal;
    uint32_t bloques;
};

// Un dispositivo Tuya que se anunció por UDP (Descubrimiento.ino).
struct Descubierto {
    char     gwId[40];     // ID de Tuya ("gwId" del anuncio)
    uint8_t  ip[4];
    int      version;      // 31, 33, 34, 35... (0 = no lo dijo)
    uint16_t puerto;       // 6666, 6667 o 7000
    uint32_t visto;        // millis() del último anuncio
};
