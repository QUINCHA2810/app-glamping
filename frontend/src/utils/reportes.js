// Rangos de fechas y CSV de la página de Reportes. Sin React, para probarlos con `npm test`.

import { TEXTO_DEMOSTRACION } from './demostracion.js'

const DIA_MS = 24 * 60 * 60 * 1000

// Fecha AAAA-MM-DD en Bogotá: es la que usa el backend para cortar los días.
export function fechaBogota(instante = new Date()) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Bogota', year: 'numeric', month: '2-digit', day: '2-digit' }).format(instante)
}

export function sumarDias(fecha, dias) {
  return new Date(Date.parse(`${fecha}T00:00:00Z`) + dias * DIA_MS).toISOString().slice(0, 10)
}

export const ATAJOS_RANGO = [
  { id: 'hoy', nombre: 'Hoy', dias: 1 },
  { id: '7d', nombre: '7 días', dias: 7 },
  { id: '30d', nombre: '30 días', dias: 30 },
]

// Ambas fechas se incluyen: "7 días" es hoy y los 6 anteriores.
export function rangoDeAtajo(id, instante = new Date()) {
  const hasta = fechaBogota(instante)
  const { dias } = ATAJOS_RANGO.find((a) => a.id === id)
  return { desde: sumarDias(hasta, 1 - dias), hasta }
}

// El mensaje a mostrar si el rango elegido no es válido, o null.
export function errorDeRango(desde, hasta, diasMaximos = 366) {
  if (!desde || !hasta) return 'Elige la fecha inicial y la final.'
  if (desde > hasta) return 'La fecha inicial no puede ser posterior a la final.'
  if ((Date.parse(hasta) - Date.parse(desde)) / DIA_MS >= diasMaximos) return `El rango no puede superar ${diasMaximos} días.`
  return null
}

function numero(valor, decimales) {
  // Excel en español espera coma decimal, y por eso el separador de columnas es ";".
  return valor == null ? '' : valor.toFixed(decimales).replace('.', ',')
}

function campo(texto) {
  const valor = String(texto ?? '')
  return /[";\n\r]/.test(valor) ? `"${valor.replaceAll('"', '""')}"` : valor
}

// Una fila por día, y después las tomas cortadas. Empieza con BOM para que Excel lea
// bien los acentos. Un domo de demostración lo dice en la primera fila.
export function csvDeReporte(reporte) {
  const filas = [
    ...(reporte.es_demostracion ? [['Aviso', `${TEXTO_DEMOSTRACION}: no son mediciones de un domo real`], []] : []),
    ['Fecha', 'kWh generados', 'kWh consumidos', 'Balance kWh', 'SoC mínimo %', 'SoC máximo %', 'SoC promedio %'],
    ...reporte.dias.map((d) => [
      d.fecha,
      numero(d.kwh_generados, 3),
      numero(d.kwh_consumidos, 3),
      d.kwh_generados == null || d.kwh_consumidos == null ? '' : numero(d.kwh_generados - d.kwh_consumidos, 3),
      numero(d.soc_minimo, 1),
      numero(d.soc_maximo, 1),
      numero(d.soc_promedio, 1),
    ]),
    [],
    ['Toma', 'Categoría', 'Cortes', 'Horas apagada por el sistema'],
    ...reporte.cortes_por_toma.map((t) => [t.nombre, t.categoria, String(t.cortes), numero(t.horas_apagada, 2)]),
  ]
  return `﻿${filas.map((fila) => fila.map(campo).join(';')).join('\r\n')}\r\n`
}

export function nombreDeCsv(reporte) {
  const marca = reporte.es_demostracion ? '_demostracion' : ''
  return `vatia_reporte_domo${reporte.domo_id}${marca}_${reporte.desde}_${reporte.hasta}.csv`
}
