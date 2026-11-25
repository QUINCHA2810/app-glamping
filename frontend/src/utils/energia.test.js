import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import {
  esReciente,
  flujoEnergia,
  formatoDuracion,
  nivelBateria,
  sentidoBateria,
  serieCargasActivas,
  textoAutonomia,
  textoInversor,
  tramoReciente,
} from './energia.js'
import { combinarSeries } from './series.js'

const AHORA = Date.parse('2026-11-05T12:00:00Z')
const hace = (minutos) => new Date(AHORA - minutos * 60_000).toISOString()

describe('nivelBateria', () => {
  it('compara con la reserva', () => {
    assert.equal(nivelBateria(49.9, 50), 'supervivencia')
    assert.equal(nivelBateria(50, 50), 'ahorro')
    assert.equal(nivelBateria(65, 50), 'ahorro')
    assert.equal(nivelBateria(65.1, 50), 'normal')
    assert.equal(nivelBateria(19, 20), 'supervivencia')
  })
  it('sin datos no opina', () => {
    assert.equal(nivelBateria(null, 50), null)
    assert.equal(nivelBateria(70, undefined), null)
  })
})

// Como GET /domos/{id}/energia-reciente (backend/servicios/energia_reciente.py).
const autonomia = (campos) => ({
  vigente: true,
  autonomia: {
    tipo: 'horas',
    horas: 3,
    horas_sin_sol: null,
    sin_sol_supera_tope: false,
    potencia_media_w: -120,
    carga_dc_media_w: 120,
    minutos: 15,
    ventana_min: 15,
    tope_horas: 72,
    ...campos,
  },
})

describe('textoAutonomia', () => {
  it('horas con la descarga media y la leyenda de 15 min', () => {
    assert.deepEqual(textoAutonomia(autonomia(), 50), {
      valor: '≈ 3 h',
      grande: true,
      detalle: 'Hasta llegar a la reserva (50 %), si la batería sigue entregando 120,0 W en promedio.',
      leyenda: 'Promedio de los últimos 15 min',
    })
  })
  it('con menos de 15 min de datos, los minutos reales', () => {
    assert.equal(textoAutonomia(autonomia({ minutos: 6.2 }), 50).leyenda, 'Promedio de 6 min con datos (de los últimos 15)')
    assert.equal(textoAutonomia(autonomia({ minutos: 2 }), 50).leyenda, 'Promedio de 2 min con datos (de los últimos 15)')
  })
  it('casi sin descarga: más del tope', () => {
    const t = textoAutonomia(autonomia({ tipo: 'mas_del_tope', horas: null }), 50)
    assert.equal(t.valor, 'Más de 72 h')
    assert.equal(t.leyenda, 'Promedio de los últimos 15 min')
    assert.equal(t.sinSol, null) // de noche: sin sol también pasa del tope, no se repite
  })
  it('casi sin descarga con sol: también "sin sol" con la carga DC media', () => {
    const t = textoAutonomia(autonomia({ tipo: 'mas_del_tope', horas: null, horas_sin_sol: 7.5 }), 50)
    assert.deepEqual([t.valor, t.sinSol], ['Más de 72 h', 'Sin sol: ≈ 7 h 30 min'])
  })
  it('cargando: sin horas, y aparte "sin sol"', () => {
    const t = textoAutonomia(autonomia({ tipo: 'cargando', horas: null, potencia_media_w: 250, horas_sin_sol: 2.5 }), 50)
    assert.deepEqual([t.valor, t.grande, t.sinSol], ['Cargando', undefined, 'Sin sol: ≈ 2 h 30 min'])
    assert.equal(t.detalle, 'La batería recibe 250,0 W en promedio.')
    const tope = textoAutonomia(autonomia({ tipo: 'cargando', potencia_media_w: 8, sin_sol_supera_tope: true }), 50)
    assert.equal(tope.sinSol, 'Sin sol: más de 72 h')
  })
  it('sin datos vigentes, pocos datos, reserva o sin capacidad: no da horas', () => {
    assert.equal(textoAutonomia({ vigente: false, autonomia: null }, 50).detalle, 'Las lecturas tienen más de 2 minutos.')
    assert.equal(textoAutonomia(null, 50).valor, 'No disponible')
    const pocos = textoAutonomia(autonomia({ tipo: 'pocos_datos', horas: null, minutos: 1.5 }), 50)
    assert.deepEqual([pocos.valor, pocos.detalle, pocos.leyenda], ['No disponible', 'Hacen falta al menos 2 minutos de lecturas de batería.', undefined])
    assert.equal(textoAutonomia(autonomia({ tipo: 'reserva' }), 50).valor, 'Batería en reserva')
    assert.match(textoAutonomia(autonomia({ tipo: 'sin_capacidad' }), 50).detalle, /capacidad/)
  })
})

describe('esReciente', () => {
  it('hasta 2 minutos', () => {
    assert.equal(esReciente(hace(1.9), AHORA), true)
    assert.equal(esReciente(hace(2.1), AHORA), false)
    assert.equal(esReciente(null, AHORA), false)
  })
})

describe('formatoDuracion', () => {
  it('escribe horas y minutos', () => {
    assert.equal(formatoDuracion(0.5), '30 min')
    assert.equal(formatoDuracion(4), '4 h')
    assert.equal(formatoDuracion(3.5), '3 h 30 min')
    assert.equal(formatoDuracion(12.4), '12 h')
  })
})

describe('sentidoBateria', () => {
  it('usa el signo de la potencia', () => {
    assert.equal(sentidoBateria(35), 'cargando')
    assert.equal(sentidoBateria(-35), 'descargando')
    assert.equal(sentidoBateria(0.4), 'reposo')
    assert.equal(sentidoBateria(null), null)
  })
})

describe('tramoReciente', () => {
  const p = (minutos, v = 1) => ({ t: AHORA - minutos * 60_000, v })

  it('sin huecos devuelve todo', () => {
    const serie = [p(30), p(20), p(10), p(0)]
    assert.deepEqual(tramoReciente(serie), serie)
  })
  it('con huecos devuelve solo el último tramo continuo', () => {
    assert.deepEqual(tramoReciente([p(100), p(90), p(40), p(30), p(20)]), [p(40), p(30), p(20)])
  })
  it('una lectura suelta tras un hueco no alcanza para dibujar: usa el tramo anterior', () => {
    assert.deepEqual(tramoReciente([p(60), p(50), p(0)]), [p(60), p(50)])
  })
  it('sin dos puntos seguidos queda vacío', () => {
    assert.deepEqual(tramoReciente([p(60), p(30), p(0)]), [])
    assert.deepEqual(tramoReciente([p(0)]), [])
    assert.deepEqual(tramoReciente([]), [])
  })
  it('justo en el límite sigue siendo continuo', () => {
    assert.equal(tramoReciente([p(20), p(10), p(0)]).length, 3)
  })
})

describe('flujoEnergia', () => {
  const flujo = (generada, consumida, potenciaBateria) => flujoEnergia({ generada, consumida, potenciaBateria })

  it('excedente: cubre el consumo y carga la batería', () => {
    const f = flujo(180, 90, 85)
    assert.deepEqual([f.caso, f.directo, f.aBateria, f.desdeBateria], ['excedente_carga', 90, 85, 0])
  })
  it('déficit: los paneles aportan lo suyo y la batería el resto', () => {
    const f = flujo(50, 90, -40)
    assert.deepEqual([f.caso, f.directo, f.aBateria, f.desdeBateria], ['deficit_bateria', 50, 0, 40])
  })
  it('de noche: solo la batería', () => {
    const f = flujo(0, 90, -90)
    assert.deepEqual([f.caso, f.directo, f.desdeBateria], ['solo_bateria', 0, 90])
  })
  it('batería llena con excedente: no lo recibe', () => {
    const f = flujo(180, 90, 0)
    assert.deepEqual([f.caso, f.excedente, f.aBateria], ['excedente_sin_usar', 90, 0])
  })
  it('equilibrio', () => {
    assert.equal(flujo(90, 90, 0).caso, 'equilibrio')
  })
  it('sin generación ni consumo', () => {
    assert.equal(flujo(0, 0, 0).caso, 'sin_actividad')
  })
  it('lecturas que no cuadran se dicen, no se disimulan', () => {
    assert.equal(flujo(50, 90, 35).caso, 'deficit_sin_descarga')
    assert.equal(flujo(50, 90, 0).caso, 'deficit_sin_descarga')
    assert.equal(flujo(180, 90, -40).caso, 'descarga_con_excedente')
  })
  it('un desajuste dentro del margen (DC frente a AC, pérdidas del inversor) no se avisa', () => {
    // Paneles 100 W, consumo AC 95 W, batería −15 W: desajuste 20 W <= max(20, 14,25).
    assert.equal(flujo(100, 95, -15).caso, 'deficit_bateria')
    // Consumo 200 W: el margen es el 15 % (30 W). Desajuste 28 W: no se avisa; 35 W: sí.
    assert.equal(flujo(200, 200, -28).caso, 'deficit_bateria')
    assert.equal(flujo(200, 200, -35).caso, 'descarga_con_excedente')
    // Déficit pequeño sin descarga: manda el signo de la batería.
    assert.equal(flujo(90, 100, 0).caso, 'equilibrio')
    assert.equal(flujo(90, 100, 3).caso, 'excedente_carga')
  })
  it('sin datos', () => {
    assert.equal(flujo(null, 90, -40).caso, 'sin_datos')
    assert.equal(flujo(50, null, -40).caso, 'sin_datos')
    assert.equal(flujo(50, 90, null).caso, 'sin_datos')
  })
})

describe('serieCargasActivas', () => {
  const disp = [
    { id: 1, estado: 'encendido' },
    { id: 2, estado: 'apagado' },
    { id: 3, estado: 'encendido' },
  ]
  const desde = AHORA - 120 * 60_000

  it('sin acciones es plana', () => {
    assert.deepEqual(serieCargasActivas(disp, [], desde, AHORA), [
      { t: desde, v: 2 },
      { t: AHORA, v: 2 },
    ])
  })
  it('reconstruye hacia atrás', () => {
    const acciones = [
      { dispositivo_id: 2, accion_realizada: 'apagar', valor_anterior: 'encendido', valor_nuevo: 'apagado', fecha_hora: hace(10) },
      { dispositivo_id: 3, accion_realizada: 'encender', valor_anterior: 'apagado', valor_nuevo: 'encendido', fecha_hora: hace(30) },
    ]
    assert.deepEqual(serieCargasActivas(disp, acciones, desde, AHORA).map((p) => p.v), [2, 2, 3, 2])
  })
  it('ignora dispositivos ajenos, acciones sin cambio y lo anterior a la ventana', () => {
    const acciones = [
      { dispositivo_id: 9, accion_realizada: 'apagar', valor_anterior: 'encendido', valor_nuevo: 'apagado', fecha_hora: hace(5) },
      { dispositivo_id: 1, accion_realizada: 'encender', valor_anterior: 'encendido', valor_nuevo: 'encendido', fecha_hora: hace(6) },
      { dispositivo_id: 1, accion_realizada: 'encender', valor_anterior: 'apagado', valor_nuevo: 'encendido', fecha_hora: hace(500) },
    ]
    assert.deepEqual(serieCargasActivas(disp, acciones, desde, AHORA).map((p) => p.v), [2, 2])
  })
})

describe('combinarSeries', () => {
  const punto = (minutos, v) => ({ t: AHORA - minutos * 60_000, v })

  it('corta la línea en un hueco por encima del máximo indicado', () => {
    // Con lecturas muy separadas, el hueco típico no alcanza a cortar: el máximo sí.
    const serie = [punto(300, 1), punto(200, 2), punto(100, 3), punto(0, 4)]
    assert.ok(combinarSeries({ a: serie }).every((f) => f.a !== null))
    const filas = combinarSeries({ a: serie }, 10 * 60_000)
    assert.ok(filas.some((f) => f.a === null))
    assert.equal(filas.filter((f) => f.a !== null).length, 4)
  })
})

describe('textoInversor', () => {
  it('pérdidas y eficiencia del inversor (caso del 24-nov 16:41)', () => {
    assert.equal(textoInversor({ perdidas: 28.5, eficiencia: 80.52 }), 'Inversor · pérdidas ≈ 28,5 W (eficiencia 81 %)')
  })
  it('sin eficiencia (entrada < 20 W) solo las pérdidas', () => {
    assert.equal(textoInversor({ perdidas: 3, eficiencia: null }), 'Inversor · pérdidas ≈ 3,0 W')
  })
  it('sin pérdidas calculadas, nada', () => {
    assert.equal(textoInversor({ perdidas: null, eficiencia: 80 }), null)
    assert.equal(textoInversor({}), null)
  })
})
