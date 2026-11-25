#pragma once
// ============================================================================
//  Recalibración GUIADA del SoC (la pide el dueño desde la app). C++ sin Arduino:
//  la usa Sensores.ino y la prueba firmware/pruebas_soc/prueba_soc.cpp.
//
//  El dueño NO escribe un valor: solo pide que el sistema mida en condiciones válidas.
//  1. iniciar: empieza una sesión (máx. maxS). En cada muestra se comprueba el reposo:
//     corriente FV < iPvMaxA, corriente del inversor < iInvMaxA (saturado o sin lectura
//     NO cumple) y |I_bat estimada| < iBatMaxA. Hacen falta reposoS CONTINUOS; una
//     muestra que no cumple (o un intervalo largo) devuelve el contador a 0 con el motivo.
//  2. Al cumplirse: el SoC de la tabla con el voltaje compensado (soc::socDeVoltaje y
//     soc::vCompensado, las de siempre) queda como PROPUESTA (estado Lista). No se aplica.
//  3. confirmar: devuelve la propuesta para aplicarla, solo si sigue en Lista y tiene
//     menos de propuestaS; si no, Rechazada. Sin confirmar en propuestaS: Vencida.
//  4. cancelar: termina sin cambios.
//  El valor libre queda SOLO en el comando serie "soc" (técnico).
//  Los tiempos se miden con un reloj en ms (millis() en la placa): la edad de la propuesta
//  no depende de que lleguen muestras.
// ============================================================================

#include <stdint.h>
#include "soc_estimador.h"

namespace soc {
namespace recal {

// Los valores de "estado" en domo/{id}/estado/soc (backend: schemas.EstadoRecalibracionSoc).
enum class Estado : uint8_t { Inactiva, Esperando, Lista, Aplicada, Cancelada, Vencida, Rechazada };

enum class Motivo : uint8_t {
    Ninguno, EnReposo, HaySol, SinSensorPv, ConsumoInversor, InversorSinLectura, CorrienteBateria,
    SinBateria, LecturasInterrumpidas, SesionVencida, PropuestaVencida, SinPropuesta, Cancelada, Aplicada,
};

struct Config {
    float iPvMaxA;
    float iInvMaxA;
    float iBatMaxA;
    float reposoS;          // reposo continuo necesario
    float maxS;             // duración máxima de la espera
    float propuestaS;       // vigencia de la propuesta para confirmarla
    float dtMaxS;           // un intervalo entre muestras más largo reinicia el reposo
};

struct Recal {
    Estado   estado = Estado::Inactiva;
    Motivo   motivo = Motivo::Ninguno;
    uint32_t inicioMs = 0;           // inicio de la sesión
    uint32_t propuestaMs = 0;        // cuándo quedó Lista
    float    segReposo = 0;          // reposo continuo acumulado
    float    socActual = NAN;        // el SoC del estimador en la última muestra
    float    socPropuesto = NAN;     // al quedar Lista
    float    voltaje = NAN;          // V compensado de la propuesta (o de la última muestra)
};

// Condición de reposo de UNA muestra: EnReposo o el motivo por el que no se cumple.
// iPv, iInv o iBat NAN (sin sensor) no cumplen; invSaturado tampoco.
Motivo condicion(const Config& c, float iPv, float iInv, bool invSaturado, float iBat);

bool activa(const Recal& r);                         // Esperando o Lista
void iniciar(Recal& r, uint32_t ahoraMs);
// Una muestra (una por publicación). v: voltaje medido; socActual: el del estimador.
// Devuelve true si cambió el estado.
bool muestra(Recal& r, const Config& c, const soc::Config& cs, float iPv, float iInv, bool invSaturado,
             float iBat, float v, float socActual, float dt, uint32_t ahoraMs);
// Vence la espera (maxS) o la propuesta (propuestaS). true si cambió el estado.
bool revisarVencimiento(Recal& r, const Config& c, uint32_t ahoraMs);
// true: aplicar `valor` (estado Aplicada). false: Rechazada (sin propuesta vigente).
bool confirmar(Recal& r, const Config& c, uint32_t ahoraMs, float& valor);
void cancelar(Recal& r);

float minutosOk(const Recal& r);
const char* textoEstado(Estado e);
const char* textoMotivo(Motivo m);    // "" para Ninguno

}  // namespace recal
}  // namespace soc
