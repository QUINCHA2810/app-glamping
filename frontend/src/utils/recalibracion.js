// Recalibración guiada del SoC (Configuración, solo el dueño). Lo que se muestra con
// GET /domos/{id}/soc-recalibracion. El dueño nunca escribe un valor: la ESP32 mide en
// reposo y el dueño solo confirma. Sin dependencias de React, para probarlo con `npm test`.
import { esNumero, formatoPct } from './formato.js'

export const ESTADOS_ACTIVOS = ['esperando', 'lista']

export function esActiva(estado) {
  return ESTADOS_ACTIVOS.includes(estado)
}

// "en reposo" es el motivo cuando la muestra SÍ cumple.
function motivoQueFalla(motivo) {
  return motivo && motivo !== 'en reposo' ? motivo : null
}

function conMotivo(motivo, resto) {
  if (!motivo) return resto
  const frase = `${motivo.charAt(0).toUpperCase()}${motivo.slice(1)}`
  return `${/[.!?]$/.test(frase) ? frase : `${frase}.`} ${resto}`.trim()
}

// { titulo, detalle, tono ('neutro'|'verde'|'ambar'|'rojo'), progreso, propuesta,
//   puedeIniciar, puedeConfirmar, puedeCancelar }
export function describirRecalibracion(r) {
  const base = {
    titulo: 'No hay una recalibración en curso',
    detalle: null,
    tono: 'neutro',
    progreso: null,
    propuesta: null,
    puedeIniciar: true,
    puedeConfirmar: false,
    puedeCancelar: false,
  }
  if (!r) return base
  const sinCambio = 'El estado de carga no cambió.'

  switch (r.estado) {
    case 'esperando': {
      const requeridos = esNumero(r.requeridos) ? r.requeridos : 15
      const ok = esNumero(r.minutos_ok) ? Math.min(r.minutos_ok, requeridos) : 0
      const falla = motivoQueFalla(r.motivo)
      return {
        ...base,
        titulo: 'Midiendo en reposo',
        detalle: falla
          ? `No se cumple: ${falla}. El conteo vuelve a 0 hasta que se cumpla.`
          : 'En reposo: mantén el inversor apagado hasta que termine.',
        tono: falla ? 'ambar' : 'neutro',
        progreso: { ok, requeridos, fraccion: requeridos > 0 ? ok / requeridos : 0 },
        puedeIniciar: false,
        puedeCancelar: true,
      }
    }
    case 'lista':
      return {
        ...base,
        titulo: 'Medición lista',
        detalle: 'Revisa el valor medido y confírmalo en los próximos 5 minutos.',
        tono: 'verde',
        propuesta: { actual: r.soc_actual, medido: r.soc_propuesto, voltaje: r.voltaje },
        puedeIniciar: false,
        puedeConfirmar: esNumero(r.soc_propuesto),
        puedeCancelar: true,
      }
    case 'aplicada':
      return {
        ...base,
        titulo: 'Estado de carga recalibrado',
        detalle: `Quedó en ${formatoPct(r.soc_propuesto)}, medido en reposo.`,
        tono: 'verde',
      }
    case 'cancelada':
      return { ...base, titulo: 'Recalibración cancelada', detalle: sinCambio }
    case 'vencida':
      return { ...base, titulo: 'La recalibración venció', detalle: conMotivo(r.motivo, sinCambio), tono: 'ambar' }
    case 'rechazada':
      return { ...base, titulo: 'Confirmación rechazada', detalle: conMotivo(r.motivo, sinCambio), tono: 'ambar' }
    case 'interrumpida':
      return { ...base, titulo: 'Recalibración interrumpida', detalle: conMotivo(r.motivo, ''), tono: 'ambar' }
    default:
      return base // sin_sesion, inactiva
  }
}
