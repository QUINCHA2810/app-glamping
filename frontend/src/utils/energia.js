// Cálculos del Panel a partir de lo que el sistema mide. Sin dependencias de React,
// para probarlos con `npm test`.
import { aFecha, esNumero, formatoW } from './formato.js'

const MINUTO_MS = 60_000
export const VENTANA_RECIENTE_MS = 120 * MINUTO_MS
const DATO_VIEJO_MS = 2 * MINUTO_MS
// Por debajo de esto, una potencia es ruido del sensor.
const POTENCIA_MINIMA_W = 1
const MARGEN_AHORRO_SOC = 15
// Más separadas que esto, dos lecturas no son el mismo tramo continuo.
const HUECO_TENDENCIA_MS = 10 * MINUTO_MS
// Aviso "revisa las lecturas" del flujo: solo si el balance generación − consumo − batería
// no cuadra por más de max(20 W, 15 % del consumo), ya con los promedios de 60 s. Algo de
// desajuste es normal: la generación y la batería son DC y el consumo es AC (las pérdidas
// del inversor son ≈ 15 %), y la batería también alimenta el consumo propio del ESP32.
const MARGEN_INCOHERENCIA_W = 20
const MARGEN_INCOHERENCIA_FRACCION = 0.15

// Nivel de la batería respecto a su reserva: bajo ella, en los 15 puntos sobre
// ella, o arriba. Sin datos, null.
export function nivelBateria(soc, reserva) {
  if (!esNumero(soc) || !esNumero(reserva)) return null
  if (soc < reserva) return 'supervivencia'
  if (soc <= reserva + MARGEN_AHORRO_SOC) return 'ahorro'
  return 'normal'
}

// Si una lectura tiene menos de 2 minutos: lo que sea más viejo no se presenta como actual.
export function esReciente(iso, ahora = Date.now()) {
  const fecha = aFecha(iso)
  return Boolean(fecha) && ahora - fecha.getTime() <= DATO_VIEJO_MS
}

export function formatoDuracion(horas) {
  const minutos = Math.round(horas * 60)
  if (minutos < 60) return `${minutos} min`
  const h = Math.floor(minutos / 60)
  const m = minutos % 60
  return m === 0 || h >= 10 ? `${h} h` : `${h} h ${m} min`
}

const SIN_AUTONOMIA = {
  reserva: 'No queda energía utilizable sobre la reserva.',
  sin_capacidad: 'Falta la capacidad de la batería en Configuración.',
  pocos_datos: 'Hacen falta al menos 2 minutos de lecturas de batería.',
  sin_datos: 'Faltan lecturas de batería.',
}

// Lo que muestra la tarjeta de Autonomía con GET /domos/{id}/energia-reciente (el cálculo
// es del backend, servicios/energia_reciente.py). { valor, grande, detalle, sinSol, leyenda }.
export function textoAutonomia(energia, reserva) {
  if (!energia) return { valor: 'No disponible', detalle: 'Sin datos de energía.' }
  if (!energia.vigente) return { valor: 'No disponible', detalle: 'Las lecturas tienen más de 2 minutos.' }
  const a = energia.autonomia
  const minutos = Math.round(a.minutos)
  const leyenda =
    a.minutos >= a.ventana_min
      ? `Promedio de los últimos ${a.ventana_min} min`
      : `Promedio de ${minutos} min con datos (de los últimos ${a.ventana_min})`
  const hastaReserva = esNumero(reserva) ? `Hasta llegar a la reserva (${reserva} %)` : 'Hasta llegar a la reserva'

  if (a.tipo === 'horas') {
    return {
      valor: `≈ ${formatoDuracion(a.horas)}`,
      grande: true,
      detalle: `${hastaReserva}, si la batería sigue entregando ${formatoW(-a.potencia_media_w)} en promedio.`,
      leyenda,
    }
  }
  if (a.tipo === 'mas_del_tope') {
    // Con sol, "más de 72 h" no informa nada: aparte, cuánto duraría sin sol con la carga
    // DC media. De noche esa carga es la misma descarga y también pasa del tope: no se repite.
    const sinSol = esNumero(a.horas_sin_sol) ? `Sin sol: ≈ ${formatoDuracion(a.horas_sin_sol)}` : null
    return { valor: `Más de ${a.tope_horas} h`, grande: true, detalle: 'La batería casi no se descarga en promedio.', sinSol, leyenda }
  }
  if (a.tipo === 'cargando') {
    const sinSol = esNumero(a.horas_sin_sol)
      ? `Sin sol: ≈ ${formatoDuracion(a.horas_sin_sol)}`
      : a.sin_sol_supera_tope
        ? `Sin sol: más de ${a.tope_horas} h`
        : null
    return {
      valor: 'Cargando',
      detalle: `La batería recibe ${formatoW(a.potencia_media_w)} en promedio.`,
      sinSol,
      leyenda,
    }
  }
  return { valor: a.tipo === 'reserva' ? 'Batería en reserva' : 'No disponible', detalle: SIN_AUTONOMIA[a.tipo] ?? SIN_AUTONOMIA.sin_datos }
}

// La potencia de la batería es positiva al cargar y negativa al descargar.
export function sentidoBateria(potencia) {
  if (!esNumero(potencia)) return null
  if (potencia >= POTENCIA_MINIMA_W) return 'cargando'
  if (potencia <= -POTENCIA_MINIMA_W) return 'descargando'
  return 'reposo'
}

// Último tramo sin huecos de una serie ordenada [{ t, v }], con al menos dos puntos
// para poder dibujarlo. Vacío si no hay ninguno.
export function tramoReciente(puntos, huecoMs = HUECO_TENDENCIA_MS) {
  let fin = puntos.length
  for (let i = puntos.length - 1; i >= 1; i--) {
    if (puntos[i].t - puntos[i - 1].t <= huecoMs) continue
    if (fin - i >= 2) return puntos.slice(i, fin)
    fin = i
  }
  return fin >= 2 ? puntos.slice(0, fin) : []
}

// Reparto de la energía: la generación alimenta primero las cargas, y la batería solo
// recibe el excedente o entrega el déficit. Lo directo se calcula; lo de la batería es
// lo que ella mide. `caso` dice qué pasa, también si las lecturas no cuadran entre sí.
export function flujoEnergia({ generada, consumida, potenciaBateria }) {
  const sentido = sentidoBateria(potenciaBateria)
  const aBateria = sentido === 'cargando' ? potenciaBateria : 0
  const desdeBateria = sentido === 'descargando' ? -potenciaBateria : 0
  if (!esNumero(generada) || !esNumero(consumida) || sentido === null) {
    return { sentido, directo: esNumero(generada) && esNumero(consumida) ? Math.min(generada, consumida) : 0, aBateria, desdeBateria, excedente: 0, caso: 'sin_datos' }
  }

  const directo = Math.min(generada, consumida)
  const excedente = Math.max(0, generada - consumida)
  const base = { sentido, directo, aBateria, desdeBateria, excedente }

  if (generada < POTENCIA_MINIMA_W && consumida < POTENCIA_MINIMA_W && sentido === 'reposo') return { ...base, caso: 'sin_actividad' }
  // Las lecturas no cuadran (ver MARGEN_INCOHERENCIA_*): dentro del margen manda el signo
  // de la batería; fuera, se avisa.
  const dentroDelMargen =
    Math.abs(generada - consumida - potenciaBateria) <= Math.max(MARGEN_INCOHERENCIA_W, MARGEN_INCOHERENCIA_FRACCION * consumida)
  const porLaBateria = () => {
    if (sentido === 'cargando') return 'excedente_carga'
    if (sentido === 'reposo') return 'equilibrio'
    return generada < POTENCIA_MINIMA_W ? 'solo_bateria' : 'deficit_bateria'
  }

  if (generada >= consumida - POTENCIA_MINIMA_W) {
    if (sentido === 'cargando') return { ...base, caso: 'excedente_carga' }
    if (sentido === 'descargando') return { ...base, caso: dentroDelMargen ? porLaBateria() : 'descarga_con_excedente' }
    return { ...base, caso: excedente >= POTENCIA_MINIMA_W ? 'excedente_sin_usar' : 'equilibrio' }
  }
  if (sentido !== 'descargando') return { ...base, caso: dentroDelMargen ? porLaBateria() : 'deficit_sin_descarga' }
  return { ...base, caso: generada < POTENCIA_MINIMA_W ? 'solo_bateria' : 'deficit_bateria' }
}

// Pérdidas del inversor que calcula el backend (servicios/energia_reciente.perdidas_inversor):
// paneles y batería están en DC y las cargas en AC. Sin pérdidas calculadas, null.
export function textoInversor({ perdidas, eficiencia }) {
  if (!esNumero(perdidas)) return null
  const texto = `Inversor · pérdidas ≈ ${formatoW(perdidas)}`
  return esNumero(eficiencia) ? `${texto} (eficiencia ${Math.round(eficiencia)} %)` : texto
}

// Cuántos dispositivos estaban encendidos en cada momento de la ventana,
// reconstruido hacia atrás desde el estado actual con el historial de acciones.
// `acciones` viene del historial, de la más reciente a la más antigua.
export function serieCargasActivas(dispositivos, acciones, desde, ahora = Date.now()) {
  const ids = new Set(dispositivos.map((d) => d.id))
  const total = dispositivos.length
  let encendidos = dispositivos.filter((d) => d.estado === 'encendido').length
  const puntos = [{ t: ahora, v: encendidos }]

  for (const accion of acciones) {
    const t = aFecha(accion.fecha_hora)?.getTime()
    if (!t || t < desde || !ids.has(accion.dispositivo_id)) continue
    if (accion.valor_anterior === accion.valor_nuevo) continue
    if (accion.accion_realizada === 'encender') encendidos -= 1
    else if (accion.accion_realizada === 'apagar') encendidos += 1
    else continue
    encendidos = Math.min(total, Math.max(0, encendidos))
    puntos.push({ t, v: encendidos })
  }
  puntos.push({ t: desde, v: encendidos })
  return puntos.reverse()
}
