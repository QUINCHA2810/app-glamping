// Cómo se presenta cada categoría. Los umbrales nunca se calculan aquí: vienen de
// GET /domos/{id}/categorias, que los ajusta a la batería del domo.
import { formatoPct } from '../../utils/formato.js'

export const INFO_CATEGORIA = {
  esencial: {
    nombre: 'Esencial',
    explicacion: 'Lo que no puede faltar. El sistema nunca la apaga.',
    punto: 'bg-cat-esencial',
  },
  importante: {
    nombre: 'Importante',
    explicacion: 'Aguanta hasta llegar a la reserva de la batería.',
    punto: 'bg-cat-importante',
  },
  confort: {
    nombre: 'Confort',
    explicacion: 'Se apaga antes, para que lo importante dure más.',
    punto: 'bg-cat-confort',
  },
  prescindible: {
    nombre: 'Prescindible',
    explicacion: 'Lo primero que se apaga cuando baja la batería.',
    punto: 'bg-cat-prescindible',
  },
}

export function textoUmbrales(preset) {
  if (!preset) return ''
  if (!preset.corte_automatico) return 'Nunca se apaga sola'
  // Espacio duro: el "%" no debe quedar solo en la línea siguiente.
  const texto = `Se apaga bajo ${formatoPct(preset.umbral_apagado_soc)} · vuelve desde ${formatoPct(preset.umbral_reactivacion_soc)}`
  return texto.replaceAll(' %', '\u00a0%')
}

// Umbral de apagado que R-13 aplicará a este dispositivo, o null si nunca lo corta.
export function umbralApagadoEfectivo(dispositivo, configuracion) {
  if (dispositivo.tipo !== 'toma' || dispositivo.categoria === 'esencial') return null
  return dispositivo.umbral_apagado_soc ?? configuracion?.umbral_soc_bajo ?? null
}

export function umbralReactivacionEfectivo(dispositivo, configuracion) {
  if (dispositivo.tipo !== 'toma' || dispositivo.categoria === 'esencial') return null
  return dispositivo.umbral_reactivacion_soc ?? configuracion?.umbral_soc_recuperacion ?? null
}

export const NOTA_ID_FISICO =
  'Identifica el equipo real. Sin él, la toma es simulada. Una toma real también requiere configurar la ESP32.'

export const NOTA_ID_FISICO_BOMBILLO =
  'Identifica el bombillo real (por ejemplo, bombillo1). Sin él, el bombillo es simulado. Con él, el encendido y el brillo se envían a la ESP32.'
