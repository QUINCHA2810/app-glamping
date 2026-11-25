import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { esDemostracion, nombreEnSelector, TEXTO_DEMOSTRACION } from './demostracion.js'
import { csvDeReporte, nombreDeCsv } from './reportes.js'

const reporte = (es_demostracion) => ({
  domo_id: 7,
  desde: '2026-10-07',
  hasta: '2026-11-05',
  es_demostracion,
  dias: [{ fecha: '2026-10-07', kwh_generados: 1.5, kwh_consumidos: 1.2, soc_minimo: 60, soc_maximo: 100, soc_promedio: 80 }],
  cortes_por_toma: [],
})

describe('domo de demostración', () => {
  it('el texto es siempre el mismo', () => {
    assert.equal(TEXTO_DEMOSTRACION, 'Demostración · datos sintéticos')
  })
  it('solo cuenta un true explícito', () => {
    assert.equal(esDemostracion({ es_demostracion: true }), true)
    for (const domo of [{ es_demostracion: false }, {}, null, undefined, { es_demostracion: 'true' }]) assert.equal(esDemostracion(domo), false)
  })
  it('el selector lo dice en la opción', () => {
    assert.equal(nombreEnSelector({ nombre: 'Domo demostración', es_demostracion: true }), 'Domo demostración (Demostración · datos sintéticos)')
    assert.equal(nombreEnSelector({ nombre: 'Domo Experimental', es_demostracion: false }), 'Domo Experimental')
  })
  it('el CSV empieza con el aviso y el nombre lo marca', () => {
    const csv = csvDeReporte(reporte(true))
    const filas = csv.slice(1).split('\r\n')
    assert.equal(filas[0], 'Aviso;Demostración · datos sintéticos: no son mediciones de un domo real')
    assert.equal(filas[1], '')
    assert.ok(filas[2].startsWith('Fecha;kWh generados'))
    assert.equal(nombreDeCsv(reporte(true)), 'vatia_reporte_domo7_demostracion_2026-10-07_2026-11-05.csv')
  })
  it('un domo del proyecto no lleva el aviso', () => {
    const csv = csvDeReporte(reporte(false))
    assert.ok(csv.slice(1).startsWith('Fecha;kWh generados'))
    assert.ok(!csv.includes('sintéticos'))
    assert.equal(nombreDeCsv(reporte(false)), 'vatia_reporte_domo7_2026-10-07_2026-11-05.csv')
  })
})
