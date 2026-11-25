// Estimador del SoC (ver soc_estimador.h). Sin Arduino: compila también en el PC.
#include "soc_estimador.h"

namespace soc {

static float limitar(float x, float lo, float hi) { return x < lo ? lo : (x > hi ? hi : x); }

// Fracción de un filtro exponencial de constante tau tras dt segundos.
static float alfaDe(float dt, float tau) { return tau > 0 ? 1.0f - expf(-dt / tau) : 1.0f; }

float socDeVoltaje(const Config& c, float v) {
    if (v <= c.tablaV[0]) return c.tablaPct[0];
    for (int i = 1; i < c.tablaPuntos; i++) {
        if (v <= c.tablaV[i]) {
            float t = (v - c.tablaV[i - 1]) / (c.tablaV[i] - c.tablaV[i - 1]);
            return c.tablaPct[i - 1] + t * (c.tablaPct[i] - c.tablaPct[i - 1]);
        }
    }
    return c.tablaPct[c.tablaPuntos - 1];
}

float vCompensado(const Config& c, float v, float iBat) {
    return isnan(iBat) ? v : v - iBat * c.rCompensacionOhm;
}

bool iniciado(const Estimador& e) { return !isnan(e.soc); }

void iniciar(Estimador& e, float valor, Inicio inicio) {
    e.soc = limitar(valor, 0.0f, 100.0f);
    e.inicio = inicio;
    e.fuente = Fuente::Ninguna;
    e.segundosReposo = 0;
}

void contar(Estimador& e, const Config& c, double whCarga, double whDescarga) {
    if (!iniciado(e) || c.capacidadWh <= 0) return;
    double neto = whCarga * c.eficienciaCarga - whDescarga * c.eficienciaDescarga;
    e.soc = limitar(e.soc + (float)(neto / c.capacidadWh * 100.0), 0.0f, 100.0f);
    e.fuente = Fuente::Conteo;
}

bool enReposoReal(const Estimador& e, const Config& c) { return e.segundosReposo >= c.reposoS; }

bool reposoYCorreccion(Estimador& e, const Config& c, float iPv, float iBat, float v, float dt) {
    // Sin carga reciente: un intervalo largo (sin broker) no suma, porque no se sabe qué pasó.
    e.cargaAhora = iPv >= c.reposoIpvA || (!isnan(e.pMedia) && e.pMedia > c.pEstableW);
    if (e.cargaAhora) e.segundosSinCarga = 0;
    else if (dt <= c.dtMaxReposoS) e.segundosSinCarga += dt;
    // Comparaciones con NAN dan false: sin sensor o saturado, no hay reposo.
    bool reposo = iPv < c.reposoIpvA && fabsf(iBat) < c.reposoIbatA && dt <= c.dtMaxReposoS
                  && e.segundosSinCarga >= c.sinCargaS;
    e.segundosReposo = reposo ? e.segundosReposo + dt : 0;
    e.ultimoVComp = vCompensado(c, v, iBat);
    e.ultimaTabla = socDeVoltaje(c, e.ultimoVComp);
    if (!iniciado(e) || !enReposoReal(e, c)) return false;
    e.soc = limitar(e.soc + alfaDe(dt, c.tauCorreccionS) * (e.ultimaTabla - e.soc), 0.0f, 100.0f);
    e.fuente = Fuente::CorreccionVoltaje;
    return true;
}

bool sincronizarCargaCompleta(Estimador& e, const Config& c, float v, float iBat, float dt) {
    // Comparaciones con NAN dan false: sin corriente conocida no cuenta.
    bool cumple = iBat >= 0 && iBat < c.iColaMaxA && dt <= c.dtMaxReposoS
                  && vCompensado(c, v, iBat) >= c.vCargaCompleta;
    e.segundosCargaCompleta = cumple ? e.segundosCargaCompleta + dt : 0;
    if (!iniciado(e) || e.segundosCargaCompleta < c.cargaCompletaS || e.soc >= 100.0f) return false;
    e.soc = 100.0f;
    e.fuente = Fuente::CargaCompleta;
    return true;
}

DecisionArranque decidirArranque(bool hayGuardado, float guardado, uint32_t cuando, uint32_t ahora,
                                 uint32_t horaMinimaValida, bool esperaVencida) {
    bool horaValida = ahora > horaMinimaValida;
    if (hayGuardado && !isnan(guardado) && guardado >= 0.0f && guardado <= 100.0f) {
        long edad = (horaValida && cuando > horaMinimaValida && ahora >= cuando) ? (long)(ahora - cuando) : -1;
        return { Arranque::Restaurar, guardado, edad };
    }
    if (!horaValida && !esperaVencida) return { Arranque::EsperarHora, NAN, -1 };
    return { Arranque::Tabla, NAN, -1 };
}

Estado actualizarEstado(Estimador& e, const Config& c, float pNeta, float v, float dt) {
    if (!isnan(pNeta)) e.pMedia = isnan(e.pMedia) ? pNeta : e.pMedia + alfaDe(dt, c.tauEstadoS) * (pNeta - e.pMedia);
    if (isnan(e.pMedia)) return Estado::Ninguno;
    if (enReposoReal(e, c)) return Estado::EnReposo;
    if (v >= c.vFlotacionMin && iniciado(e) && e.soc >= c.socCargadaMin && e.pMedia > -c.pEstableW) return Estado::Cargada;
    if (e.pMedia > c.pEstableW)       e.ultimoSentido = Estado::Cargando;
    else if (e.pMedia < -c.pEstableW) e.ultimoSentido = Estado::Descargando;
    // Entre los dos umbrales ("estable"): se conserva el último sentido; sin uno, el signo.
    else if (e.ultimoSentido == Estado::Ninguno) e.ultimoSentido = e.pMedia >= 0 ? Estado::Cargando : Estado::Descargando;
    return e.ultimoSentido;
}

const char* textoEstado(Estado s) {
    switch (s) {
        case Estado::Cargando:    return "cargando";
        case Estado::Descargando: return "descargando";
        case Estado::Cargada:     return "cargada";
        case Estado::EnReposo:    return "en_reposo";
        default:                  return nullptr;
    }
}

const char* textoFuente(Fuente f) {
    switch (f) {
        case Fuente::Conteo:            return "conteo";
        case Fuente::CorreccionVoltaje: return "corrección por voltaje";
        case Fuente::CargaCompleta:     return "carga completa (100 %)";
        default:                        return "-";
    }
}

const char* textoInicio(Inicio i) {
    switch (i) {
        case Inicio::Restaurado:     return "restaurado de flash (aproximado)";
        case Inicio::InicialVoltaje: return "inicial por voltaje (aproximado)";
        case Inicio::Manual:         return "fijado con el comando soc";
        case Inicio::Recalibrado:    return "recalibrado desde la app (medido en reposo)";
        default:                     return "-";
    }
}

}  // namespace soc
