import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { describirRecalibracion, esActiva } from './recalibracion.js'

describe('describirRecalibracion', () => {
  it('sin sesión o inactiva: se puede iniciar', () => {
    for (const r of [null, { estado: 'sin_sesion' }, { estado: 'inactiva' }]) {
      const d = describirRecalibracion(r)
      assert.equal(d.puedeIniciar, true)
      assert.equal(d.puedeConfirmar, false)
      assert.equal(d.progreso, null)
    }
  })

  it('esperando: progreso y motivo si no se cumple', () => {
    const d = describirRecalibracion({ estado: 'esperando', minutos_ok: 0, requeridos: 15, motivo: 'hay consumo en el inversor' })
    assert.deepEqual(d.progreso, { ok: 0, requeridos: 15, fraccion: 0 })
    assert.match(d.detalle, /hay consumo en el inversor/)
    assert.equal(d.tono, 'ambar')
    assert.equal(d.puedeIniciar, false)
    assert.equal(d.puedeCancelar, true)
    const enReposo = describirRecalibracion({ estado: 'esperando', minutos_ok: 7.5, requeridos: 15, motivo: 'en reposo' })
    assert.equal(enReposo.progreso.fraccion, 0.5)
    assert.doesNotMatch(enReposo.detalle, /No se cumple/)
  })

  it('lista: actual -> medido, confirmar o cancelar', () => {
    const d = describirRecalibracion({ estado: 'lista', soc_actual: 100, soc_propuesto: 58, voltaje: 12.41 })
    assert.deepEqual(d.propuesta, { actual: 100, medido: 58, voltaje: 12.41 })
    assert.equal(d.puedeConfirmar, true)
    assert.equal(d.puedeCancelar, true)
    assert.equal(describirRecalibracion({ estado: 'lista', soc_propuesto: null }).puedeConfirmar, false)
  })

  it('resultados finales: se puede volver a iniciar', () => {
    assert.match(describirRecalibracion({ estado: 'aplicada', soc_propuesto: 58 }).detalle, /58,0 %/)
    for (const estado of ['aplicada', 'cancelada', 'vencida', 'rechazada', 'interrumpida']) {
      const d = describirRecalibracion({ estado, motivo: 'la propuesta no se confirmó a tiempo' })
      assert.equal(d.puedeIniciar, true, estado)
      assert.equal(d.puedeConfirmar, false, estado)
    }
    assert.match(describirRecalibracion({ estado: 'vencida', motivo: 'la propuesta no se confirmó a tiempo' }).detalle, /^La propuesta/)
  })

  it('activa solo esperando y lista', () => {
    assert.equal(esActiva('esperando'), true)
    assert.equal(esActiva('lista'), true)
    assert.equal(esActiva('aplicada'), false)
    assert.equal(esActiva(undefined), false)
  })
})
