#pragma once
// ============================================================================
//  Estimador del SoC de la batería. C++ sin Arduino: lo usa Sensores.ino y lo
//  prueba en el PC firmware/pruebas_soc/prueba_soc.cpp (ver firmware/README.md).
//
//  - CONTEO DE ENERGÍA siempre: SoC += (Wh_carga × η_carga − Wh_descarga × η_descarga)
//    / capacidad_Wh × 100, con la potencia neta ESTIMADA de la batería que integra la
//    tarea de sensores. Límites 0-100.
//  - CORRECCIÓN POR VOLTAJE solo en REPOSO REAL: corriente del panel < reposoIpvA (de
//    noche o casi sin luz) Y |I_bat| < reposoIbatA, de forma continua durante reposoS, Y
//    SIN CARGA RECIENTE: al menos sinCargaS sin carga antes de empezar a contar el reposo
//    (tras cargar, la carga superficial infla el voltaje durante horas aunque descargue).
//    "Carga" = iPv >= reposoIpvA o potencia neta media > +pEstableW. Al arrancar el
//    contador sin carga empieza en 0 (conservador).
//    Entonces el SoC se acerca al de la tabla (voltaje compensado V − I·R) con un filtro
//    de constante de tiempo tauCorreccionS. Si la condición se rompe, el contador vuelve
//    a 0. Con sol el voltaje lo sostienen los paneles y la tabla no sirve.
//  - ESTADO con la potencia neta MEDIA (filtro de constante tauEstadoS), no la instantánea.
//  - CARGA COMPLETA: V compensado >= vCargaCompleta con corriente de cola (0 <= I_bat <
//    iColaMaxA) de forma continua durante cargaCompletaS -> SoC = 100 %. Es lo único que
//    lleva el SoC a 100 % aparte del conteo.
//  - ARRANQUE (decidirArranque): un SoC guardado válido se restaura sin límite de edad;
//    solo sin guardado se usa la tabla de voltaje.
// ============================================================================

#include <math.h>
#include <stdint.h>

namespace soc {

// De dónde salió el último cambio del SoC.
enum class Fuente : uint8_t { Ninguna, Conteo, CorreccionVoltaje, CargaCompleta };
// Cómo se fijó el SoC por última vez (arranque, comando "soc" o recalibración de la app).
enum class Inicio : uint8_t { Ninguno, Restaurado, InicialVoltaje, Manual, Recalibrado };
// Los valores que acepta el backend (schemas.EstadoBateriaValor); Ninguno = null.
enum class Estado : uint8_t { Ninguno, Cargando, Descargando, Cargada, EnReposo };

struct Config {
    float capacidadWh;
    float eficienciaCarga;       // fracción de la energía de carga que queda en la batería
    float eficienciaDescarga;
    float reposoIpvA;
    float reposoIbatA;
    float reposoS;               // reposo real continuo antes de corregir por voltaje
    float dtMaxReposoS;          // un intervalo más largo (p. ej. sin broker) reinicia el reposo
                                 // y no cuenta como tiempo sin carga
    float sinCargaS;             // sin carga al menos esto antes de que el reposo pueda contar
    float tauCorreccionS;
    float tauEstadoS;
    float pEstableW;             // |P media| por debajo: se conserva el último cargando/descargando
    float rCompensacionOhm;
    float vFlotacionMin;         // "cargada": V >= esto ...
    float socCargadaMin;         // ... y SoC >= esto
    const float* tablaV;         // voltaje en reposo -> SoC, ascendente
    const float* tablaPct;
    int   tablaPuntos;
    // Al final: el inicializador de Sensores.ino es posicional.
    float vCargaCompleta;        // carga completa: V compensado >= esto ...
    float iColaMaxA;             // ... con 0 <= I_bat < esto (corriente de cola) ...
    float cargaCompletaS;        // ... de forma continua durante esto
};

struct Estimador {
    float  soc = NAN;                    // NAN hasta iniciarlo
    Fuente fuente = Fuente::Ninguna;
    Inicio inicio = Inicio::Ninguno;
    float  segundosReposo = 0;           // reposo real continuo acumulado
    float  segundosSinCarga = 0;         // desde la última carga (0 al arrancar)
    bool   cargaAhora = false;           // en la última actualización hubo carga
    float  pMedia = NAN;                 // W, + cargando
    Estado ultimoSentido = Estado::Ninguno;   // último Cargando o Descargando
    float  ultimoVComp = NAN;            // el voltaje compensado y su valor de tabla
    float  ultimaTabla = NAN;            // de la última actualización
    float  segundosCargaCompleta = 0;    // carga completa continua acumulada
    long   edadRestauradoS = -1;         // edad del SoC restaurado al arrancar; -1 desconocida
};

// Qué hacer al arrancar con lo que hay en la flash.
enum class Arranque : uint8_t { Restaurar, EsperarHora, Tabla };
struct DecisionArranque {
    Arranque accion;
    float    soc;                        // el guardado, si accion == Restaurar
    long     edadS;                      // su edad en s; -1 si no se sabe (sin fecha o sin hora)
};

float socDeVoltaje(const Config& c, float v);
// Voltaje de reposo aproximado: V − I_bat × R (I_bat + cargando).
float vCompensado(const Config& c, float v, float iBat);

bool iniciado(const Estimador& e);
void iniciar(Estimador& e, float soc, Inicio inicio);     // también el comando "soc"

// Suma la energía del intervalo (Wh >= 0 cada una). Sin iniciar, no hace nada.
void contar(Estimador& e, const Config& c, double whCarga, double whDescarga);

// Lleva los contadores sin carga y de reposo real y, si se cumple, corrige por voltaje.
// La carga mira iPv y la potencia media de la actualización anterior (actualizarEstado
// va después). iPv o iBat NAN (sensor ausente o saturado): no hay reposo. Devuelve true si
// corrigió.
bool reposoYCorreccion(Estimador& e, const Config& c, float iPv, float iBat, float v, float dt);
bool enReposoReal(const Estimador& e, const Config& c);

// Carga completa: lleva el contador y, al cumplirse cargaCompletaS, pone el SoC en 100 %.
// Devuelve true solo en la actualización que sincroniza (SoC < 100 antes). iBat NAN: no cumple.
bool sincronizarCargaCompleta(Estimador& e, const Config& c, float v, float iBat, float dt);

// Arranque. `hayGuardado`/`guardado`: el SoC de la flash; `cuando`: su hora Unix (0 = sin
// fecha); `ahora`: la hora actual (<= horaMinimaValida = sin NTP); `esperaVencida`: ya se
// esperó lo suficiente a la hora NTP. Un guardado finito en 0-100 se restaura SIEMPRE, sin
// importar la edad; sin guardado, la tabla, pero antes se espera la hora (comportamiento
// anterior) salvo que la espera haya vencido.
DecisionArranque decidirArranque(bool hayGuardado, float guardado, uint32_t cuando, uint32_t ahora,
                                 uint32_t horaMinimaValida, bool esperaVencida);

// Actualiza la potencia media con la del intervalo y devuelve el estado a publicar.
Estado actualizarEstado(Estimador& e, const Config& c, float pNeta, float v, float dt);

const char* textoEstado(Estado s);     // el valor de la API, o nullptr
const char* textoFuente(Fuente f);
const char* textoInicio(Inicio i);

}  // namespace soc
