// Recalibración guiada del SoC (ver soc_recalibracion.h). Sin Arduino: compila también en el PC.
#include "soc_recalibracion.h"

namespace soc {
namespace recal {

static bool pasaron(uint32_t desdeMs, uint32_t ahoraMs, float segundos) {
    return (float)(uint32_t)(ahoraMs - desdeMs) >= segundos * 1000.0f;   // resta sin signo: soporta el desborde de millis()
}

Motivo condicion(const Config& c, float iPv, float iInv, bool invSaturado, float iBat) {
    if (isnan(iPv)) return Motivo::SinSensorPv;
    if (iPv >= c.iPvMaxA) return Motivo::HaySol;
    if (invSaturado || isnan(iInv)) return Motivo::InversorSinLectura;
    if (fabsf(iInv) >= c.iInvMaxA) return Motivo::ConsumoInversor;
    if (!(fabsf(iBat) < c.iBatMaxA)) return Motivo::CorrienteBateria;   // NAN tampoco cumple
    return Motivo::EnReposo;
}

bool activa(const Recal& r) { return r.estado == Estado::Esperando || r.estado == Estado::Lista; }

void iniciar(Recal& r, uint32_t ahoraMs) {
    r = Recal();
    r.estado = Estado::Esperando;
    r.inicioMs = ahoraMs;
}

bool revisarVencimiento(Recal& r, const Config& c, uint32_t ahoraMs) {
    if (r.estado == Estado::Esperando && pasaron(r.inicioMs, ahoraMs, c.maxS)) {
        r.estado = Estado::Vencida; r.motivo = Motivo::SesionVencida; r.segReposo = 0;
        return true;
    }
    if (r.estado == Estado::Lista && pasaron(r.propuestaMs, ahoraMs, c.propuestaS)) {
        r.estado = Estado::Vencida; r.motivo = Motivo::PropuestaVencida;
        return true;
    }
    return false;
}

bool muestra(Recal& r, const Config& c, const soc::Config& cs, float iPv, float iInv, bool invSaturado,
             float iBat, float v, float socActual, float dt, uint32_t ahoraMs) {
    if (!activa(r)) return false;
    r.socActual = socActual;
    if (revisarVencimiento(r, c, ahoraMs)) return true;
    if (r.estado != Estado::Esperando) return false;          // Lista: solo espera la confirmación

    Motivo m = condicion(c, iPv, iInv, invSaturado, iBat);
    if (m == Motivo::EnReposo && (isnan(v) || v < 5.0f)) m = Motivo::SinBateria;
    if (m == Motivo::EnReposo && dt > c.dtMaxS) m = Motivo::LecturasInterrumpidas;
    r.motivo = m;
    r.voltaje = soc::vCompensado(cs, v, iBat);
    if (m != Motivo::EnReposo) { r.segReposo = 0; return false; }
    r.segReposo += dt;
    if (r.segReposo < c.reposoS) return false;

    // Misma tabla y misma compensación V − I·R que el estimador.
    r.socPropuesto = soc::socDeVoltaje(cs, r.voltaje);
    r.estado = Estado::Lista;
    r.propuestaMs = ahoraMs;
    return true;
}

bool confirmar(Recal& r, const Config& c, uint32_t ahoraMs, float& valor) {
    revisarVencimiento(r, c, ahoraMs);
    if (r.estado != Estado::Lista || isnan(r.socPropuesto)) {
        r.motivo = r.estado == Estado::Vencida ? Motivo::PropuestaVencida : Motivo::SinPropuesta;
        r.estado = Estado::Rechazada;
        return false;
    }
    valor = r.socPropuesto;
    r.estado = Estado::Aplicada;
    r.motivo = Motivo::Aplicada;
    return true;
}

void cancelar(Recal& r) {
    r.estado = Estado::Cancelada;
    r.motivo = Motivo::Cancelada;
    r.segReposo = 0;
}

float minutosOk(const Recal& r) { return r.segReposo / 60.0f; }

const char* textoEstado(Estado e) {
    switch (e) {
        case Estado::Esperando: return "esperando";
        case Estado::Lista:     return "lista";
        case Estado::Aplicada:  return "aplicada";
        case Estado::Cancelada: return "cancelada";
        case Estado::Vencida:   return "vencida";
        case Estado::Rechazada: return "rechazada";
        default:                return "inactiva";
    }
}

const char* textoMotivo(Motivo m) {
    switch (m) {
        case Motivo::EnReposo:              return "en reposo";
        case Motivo::HaySol:                return "hay sol: los paneles entregan corriente";
        case Motivo::SinSensorPv:           return "sin lectura de la corriente de los paneles";
        case Motivo::ConsumoInversor:       return "hay consumo en el inversor";
        case Motivo::InversorSinLectura:    return "sin lectura válida del inversor (saturado o ausente)";
        case Motivo::CorrienteBateria:      return "la batería tiene corriente";
        case Motivo::SinBateria:            return "sin lectura del voltaje de la batería";
        case Motivo::LecturasInterrumpidas: return "se interrumpieron las lecturas";
        case Motivo::SesionVencida:         return "no hubo el reposo continuo necesario dentro del tiempo máximo";
        case Motivo::PropuestaVencida:      return "la propuesta no se confirmó a tiempo";
        case Motivo::SinPropuesta:          return "no hay una propuesta lista para confirmar";
        case Motivo::Cancelada:             return "cancelada por el dueño";
        case Motivo::Aplicada:              return "SoC aplicado y guardado";
        default:                            return "";
    }
}

}  // namespace recal
}  // namespace soc
