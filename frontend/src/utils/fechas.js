// <input type="datetime-local"> trabaja con texto "AAAA-MM-DDTHH:MM" en hora
// local; el backend recibe ISO 8601 con zona.
export function aValorLocal(fecha) {
  const desfase = fecha.getTimezoneOffset() * 60000
  return new Date(fecha.getTime() - desfase).toISOString().slice(0, 16)
}

export function valorLocalAIso(valor) {
  if (!valor) return null
  const fecha = new Date(valor)
  return Number.isNaN(fecha.getTime()) ? null : fecha.toISOString()
}
