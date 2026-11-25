import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { formatoPct } from './formato.js'
import { textoAlEncenderBajoUmbral, textoPrimaciaHumana } from './primacia.js'

describe('texto de la primacía humana', () => {
  it('sin primacía no hay texto', () => {
    assert.equal(textoPrimaciaHumana(null, 50), null)
    assert.equal(textoPrimaciaHumana(undefined, 50), null)
  })
  it('dice hasta cuándo la respeta y la reserva', () => {
    assert.equal(
      textoPrimaciaHumana(75, 50),
      `Encendida a mano: el sistema la respeta hasta que la batería llegue a ${formatoPct(75)} (la corta si baja de la reserva, ${formatoPct(50)}).`,
    )
  })
  it('sin reserva conocida, sin el paréntesis', () => {
    assert.equal(textoPrimaciaHumana(75, null), `Encendida a mano: el sistema la respeta hasta que la batería llegue a ${formatoPct(75)}.`)
  })
})

describe('aviso al encender bajo el umbral', () => {
  it('sobre la reserva: la respetará hasta la reactivación', () => {
    const texto = textoAlEncenderBajoUmbral(64, 75, 50)
    assert.match(texto, /la respetará hasta que la batería llegue a 75,0/)
    assert.match(texto, /salvo que baje de la reserva \(50,0/)
  })
  it('bajo la reserva: la volverá a apagar', () => {
    assert.match(textoAlEncenderBajoUmbral(45, 75, 50), /volverá a apagar en la siguiente evaluación/)
  })
  it('justo en la reserva todavía la respeta', () => {
    assert.match(textoAlEncenderBajoUmbral(50, 75, 50), /la respetará/)
  })
})
