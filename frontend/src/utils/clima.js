// Clima de referencia (backend/servicios/clima.py): estimaciones de Open-Meteo para las
// coordenadas del domo, NO mediciones en el domo. Aquí solo se arma lo que se muestra.

export const AVISO_ESTIMACION =
  'Estimación del modelo meteorológico para las coordenadas del domo; no es una medición en el domo.'
export const ATRIBUCION = 'Datos del clima:'
export const URL_OPEN_METEO = 'https://open-meteo.com/'

// El backend consulta cada 15 min: después de 3 ciclos sin datos nuevos, se avisa.
export const ANTIGUEDAD_VIEJA_S = 45 * 60
// GET /domos/{id}/clima acepta rangos de hasta 31 días.
export const RANGO_MAXIMO_MS = 31 * 24 * 60 * 60 * 1000

// Código WMO de Open-Meteo (weather_code) → texto e ícono. Los íconos: sol, luna,
// parcial (sol o luna con nube), nube, niebla, lluvia, nieve, tormenta.
const CODIGOS = [
  [[0], 'Despejado', 'despejado'],
  [[1], 'Mayormente despejado', 'despejado'],
  [[2], 'Parcialmente nublado', 'parcial'],
  [[3], 'Nublado', 'nube'],
  [[45, 48], 'Niebla', 'niebla'],
  [[51, 53, 55], 'Llovizna', 'lluvia'],
  [[56, 57], 'Llovizna helada', 'lluvia'],
  [[61], 'Lluvia ligera', 'lluvia'],
  [[63], 'Lluvia', 'lluvia'],
  [[65], 'Lluvia fuerte', 'lluvia'],
  [[66, 67], 'Lluvia helada', 'lluvia'],
  [[71, 73, 75, 77], 'Nieve', 'nieve'],
  [[80, 81], 'Chubascos', 'lluvia'],
  [[82], 'Chubascos fuertes', 'lluvia'],
  [[85, 86], 'Chubascos de nieve', 'nieve'],
  [[95], 'Tormenta', 'tormenta'],
  [[96, 99], 'Tormenta con granizo', 'tormenta'],
]
const POR_CODIGO = new Map(CODIGOS.flatMap(([codigos, texto, icono]) => codigos.map((c) => [c, { texto, icono }])))

// { texto, icono }. De noche, "despejado" es luna y "parcial" lleva luna.
export function descripcionClima(codigo, esDeDia) {
  const base = POR_CODIGO.get(codigo) ?? { texto: 'Condición desconocida', icono: 'nube' }
  const noche = esDeDia === false
  if (base.icono === 'despejado') return { texto: base.texto, icono: noche ? 'luna' : 'sol' }
  if (base.icono === 'parcial') return { texto: base.texto, icono: noche ? 'parcial-noche' : 'parcial' }
  return base
}

// "Actualizado hace X min" con la antigüedad en segundos que manda el backend.
export function textoAntiguedad(segundos) {
  if (typeof segundos !== 'number' || !Number.isFinite(segundos) || segundos < 0) return 'Sin fecha'
  if (segundos < 60) return 'Actualizado hace menos de 1 min'
  const minutos = Math.floor(segundos / 60)
  if (minutos < 60) return `Actualizado hace ${minutos} min`
  const horas = Math.floor(minutos / 60)
  if (horas < 24) {
    const resto = minutos % 60
    return resto ? `Actualizado hace ${horas} h ${resto} min` : `Actualizado hace ${horas} h`
  }
  const dias = Math.floor(horas / 24)
  return `Actualizado hace ${dias} ${dias === 1 ? 'día' : 'días'}`
}

export function esViejo(segundos) {
  return typeof segundos === 'number' && segundos > ANTIGUEDAD_VIEJA_S
}

// Lo que dice la tarjeta cuando no hay clima (GET /clima/actual con disponible=false).
export function textoSinClima(motivo) {
  if (motivo === 'sin_coordenadas') return 'Este domo no tiene coordenadas: sin ellas no hay clima de referencia.'
  if (motivo === 'sin_datos') return 'Todavía no hay datos del clima. Se consultan cada 15 minutos.'
  return 'Clima no disponible.'
}

// ¿Se puede pedir la serie de clima para este rango (ISO)?
export function rangoClimaValido(desdeIso, hastaIso) {
  const desde = Date.parse(desdeIso)
  const hasta = Date.parse(hastaIso)
  return Number.isFinite(desde) && Number.isFinite(hasta) && desde <= hasta && hasta - desde <= RANGO_MAXIMO_MS
}
