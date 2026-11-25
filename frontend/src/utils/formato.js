// Formatos de presentación en español (Colombia).

const unDecimal = new Intl.NumberFormat('es-CO', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
const hora = new Intl.DateTimeFormat('es-CO', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
const fechaHora = new Intl.DateTimeFormat('es-CO', { dateStyle: 'short', timeStyle: 'medium', hour12: false })
const diaHora = new Intl.DateTimeFormat('es-CO', {
  day: '2-digit',
  month: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})
const UN_DIA_MS = 24 * 60 * 60 * 1000

export const SIN_DATOS = 'Sin datos'

export function esNumero(valor) {
  return typeof valor === 'number' && Number.isFinite(valor)
}

export function formatoW(valor) {
  return esNumero(valor) ? `${unDecimal.format(valor)} W` : SIN_DATOS
}

export function formatoPct(valor) {
  return esNumero(valor) ? `${unDecimal.format(valor)} %` : SIN_DATOS
}

export function formatoNumero(valor) {
  return esNumero(valor) ? unDecimal.format(valor) : SIN_DATOS
}

// El backend devuelve fechas ISO con zona horaria; si alguna llegara sin
// ella, se interpreta como UTC (así las guarda Postgres).
export function aFecha(iso) {
  if (!iso) return null
  const conZona = /([zZ]|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`
  const fecha = new Date(conZona)
  return Number.isNaN(fecha.getTime()) ? null : fecha
}

export function formatoHora(fecha) {
  return fecha ? hora.format(fecha) : ''
}

export function formatoFechaHora(fecha) {
  return fecha ? fechaHora.format(fecha) : SIN_DATOS
}

// Energía en Wh (unidad que usa el simulador para energia_generada/
// energia_consumida, ver backend/README.md); desde 1000 Wh, en kWh.
export function formatoEnergia(valorWh) {
  if (!esNumero(valorWh)) return SIN_DATOS
  return Math.abs(valorWh) >= 1000 ? `${unDecimal.format(valorWh / 1000)} kWh` : `${unDecimal.format(valorWh)} Wh`
}

// Etiquetas del eje de tiempo: HH:MM:SS si el rango cabe en un día, y
// día/mes + hora si es más largo.
export function formateadorEjeTiempo(duracionMs) {
  return duracionMs <= UN_DIA_MS ? (ms) => hora.format(new Date(ms)) : (ms) => diaHora.format(new Date(ms))
}
