import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { csvDeReporte, errorDeRango, fechaBogota, nombreDeCsv, rangoDeAtajo, sumarDias } from './reportes.js'

describe('fechas de Bogotá', () => {
  it('a las 04:59 UTC todavía es el día anterior', () => {
    assert.equal(fechaBogota(new Date('2026-03-11T04:59:00Z')), '2026-03-10')
    assert.equal(fechaBogota(new Date('2026-03-11T05:00:00Z')), '2026-03-11')
  })
  it('suma días cruzando meses', () => {
    assert.equal(sumarDias('2026-03-01', -1), '2026-02-28')
    assert.equal(sumarDias('2026-12-31', 1), '2027-01-01')
  })
})

describe('rangoDeAtajo', () => {
  const instante = new Date('2026-03-11T15:00:00Z')
  it('incluye hoy', () => {
    assert.deepEqual(rangoDeAtajo('hoy', instante), { desde: '2026-03-11', hasta: '2026-03-11' })
    assert.deepEqual(rangoDeAtajo('7d', instante), { desde: '2026-03-05', hasta: '2026-03-11' })
    assert.deepEqual(rangoDeAtajo('30d', instante), { desde: '2026-02-10', hasta: '2026-03-11' })
  })
})

describe('errorDeRango', () => {
  it('valida el orden y el máximo', () => {
    assert.equal(errorDeRango('2026-03-01', '2026-03-01'), null)
    assert.match(errorDeRango('', '2026-03-01'), /Elige/)
    assert.match(errorDeRango('2026-03-02', '2026-03-01'), /posterior/)
    assert.equal(errorDeRango('2026-01-01', '2027-01-01'), null)
    assert.match(errorDeRango('2026-01-01', '2027-01-02'), /366/)
  })
})

describe('csvDeReporte', () => {
  const reporte = {
    domo_id: 1,
    desde: '2026-03-10',
    hasta: '2026-03-11',
    dias: [
      { fecha: '2026-03-10', kwh_generados: 1.2346, kwh_consumidos: 0.5, soc_minimo: 40.26, soc_maximo: 80, soc_promedio: 60 },
      { fecha: '2026-03-11', kwh_generados: null, kwh_consumidos: null, soc_minimo: null, soc_maximo: null, soc_promedio: null },
    ],
    cortes_por_toma: [{ nombre: 'Toma; "A"', categoria: 'confort', cortes: 2, horas_apagada: 1.5 }],
  }
  const lineas = csvDeReporte(reporte).split('\r\n')

  it('empieza con BOM y usa formato de Excel en español', () => {
    assert.ok(csvDeReporte(reporte).startsWith('﻿Fecha;kWh generados'))
    assert.equal(lineas[1], '2026-03-10;1,235;0,500;0,735;40,3;80,0;60,0')
  })
  it('sin datos deja las celdas vacías', () => {
    assert.equal(lineas[2], '2026-03-11;;;;;;')
  })
  it('protege los textos con separadores o comillas', () => {
    assert.equal(lineas[5], '"Toma; ""A""";confort;2;1,50')
  })
  it('nombra el archivo con el domo y el rango', () => {
    assert.equal(nombreDeCsv(reporte), 'vatia_reporte_domo1_2026-03-10_2026-03-11.csv')
  })
})
