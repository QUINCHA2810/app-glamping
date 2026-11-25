// Preparación de series temporales para las gráficas (Panel e Históricos).
//
// Las gráficas usan un eje X numérico en milisegundos, proporcional al
// tiempo real. Para que un hueco entre lecturas se vea como hueco (y no
// como una línea recta que lo atraviesa) se intercala un punto con valor
// null en medio de cada hueco, y las líneas se dibujan sin connectNulls.

import { aFecha, esNumero } from './formato.js'

export const MAX_PUNTOS = 300
// Un salto entre lecturas cuenta como hueco si supera 3 veces el intervalo
// típico de la serie, y nunca menos de 30 s (la telemetría llega cada ~5 s).
const FACTOR_HUECO = 3
const HUECO_MINIMO_MS = 30_000

// Lecturas del backend → [{ t, v }] ordenado del más antiguo al más reciente.
export function puntosDe(lecturas, campo) {
  return (lecturas ?? [])
    .map((l) => ({ t: aFecha(l.fecha_hora)?.getTime(), v: l[campo] }))
    .filter((p) => p.t && esNumero(p.v))
    .sort((a, b) => a.t - b.t)
}

// Si hay más de `max` puntos, promedia (tiempo y valor) por intervalos de
// tiempo iguales. Solo afecta al dibujo: los totales salen del backend.
export function reducir(puntos, max = MAX_PUNTOS) {
  if (puntos.length <= max) return puntos
  const inicio = puntos[0].t
  const ancho = (puntos[puntos.length - 1].t - inicio) / max || 1
  const grupos = new Map()
  for (const p of puntos) {
    const i = Math.min(max - 1, Math.floor((p.t - inicio) / ancho))
    const g = grupos.get(i) ?? { t: 0, v: 0, n: 0 }
    g.t += p.t
    g.v += p.v
    g.n += 1
    grupos.set(i, g)
  }
  return [...grupos.keys()]
    .sort((a, b) => a - b)
    .map((i) => {
      const g = grupos.get(i)
      return { t: g.t / g.n, v: g.v / g.n }
    })
}

function mediana(valores) {
  if (valores.length === 0) return 0
  const orden = [...valores].sort((a, b) => a - b)
  return orden[Math.floor(orden.length / 2)]
}

function umbralHueco(listas, huecoMaximoMs) {
  const tipico = Math.max(
    0,
    ...listas.map((pts) => mediana(pts.slice(1).map((p, i) => p.t - pts[i].t))),
  )
  return Math.min(Math.max(FACTOR_HUECO * tipico, HUECO_MINIMO_MS), huecoMaximoMs)
}

// Une varias series ({ clave: [{t, v}] }) en filas { t, clave1, clave2 }
// para Recharts. Como cada serie tiene sus propios instantes (generación y
// consumo no llegan en el mismo milisegundo), en los instantes de una serie
// el valor de la otra se interpola linealmente entre sus dos lecturas
// vecinas — exactamente sobre la recta que se dibujaría — salvo que esas
// vecinas estén separadas por un hueco: ahí queda null y la línea se corta.
// `huecoMaximoMs` corta también cuando las lecturas son escasas: con pocas y muy
// separadas, el intervalo típico es tan grande que ningún salto contaría como hueco.
export function combinarSeries(series, huecoMaximoMs = Infinity) {
  const claves = Object.keys(series)
  const umbral = umbralHueco(claves.map((c) => series[c]), huecoMaximoMs)

  const instantes = new Set()
  for (const c of claves) {
    const pts = series[c]
    pts.forEach((p, i) => {
      instantes.add(p.t)
      // Punto intermedio de cada hueco, para que el corte se vea siempre.
      if (i > 0 && p.t - pts[i - 1].t > umbral) instantes.add(Math.round((p.t + pts[i - 1].t) / 2))
    })
  }
  const tiempos = [...instantes].sort((a, b) => a - b)

  const filas = tiempos.map((t) => ({ t }))
  for (const c of claves) {
    const pts = series[c]
    let j = 0
    filas.forEach((fila) => {
      while (j + 1 < pts.length && pts[j + 1].t <= fila.t) j++
      const a = pts[j]
      const b = pts[j + 1]
      if (!a || a.t > fila.t) {
        fila[c] = null // antes de la primera lectura de esta serie
      } else if (a.t === fila.t) {
        fila[c] = a.v
      } else if (b && b.t - a.t <= umbral) {
        fila[c] = a.v + ((b.v - a.v) * (fila.t - a.t)) / (b.t - a.t)
      } else {
        fila[c] = null // dentro de un hueco o después de la última lectura
      }
    })
  }
  return filas
}
