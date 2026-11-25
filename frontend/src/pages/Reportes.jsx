import { lazy, Suspense, useCallback, useState } from 'react'
import { apiFetch } from '../api/client.js'
import { INFO_CATEGORIA } from '../components/dispositivos/categorias.js'
import PaginaDomo from '../components/PaginaDomo.jsx'
import { AvisoError, BOTON_PRINCIPAL, BOTON_SECUNDARIO, Campo, CLASE_CAMPO, CLASE_TARJETA, Vacio } from '../components/ui.jsx'
import { useConsulta } from '../hooks/useConsulta.js'
import { formatoNumero, formatoPct } from '../utils/formato.js'
import { ATAJOS_RANGO, csvDeReporte, errorDeRango, nombreDeCsv, rangoDeAtajo } from '../utils/reportes.js'

// Recharts es pesado: las gráficas se cargan en un chunk aparte.
const GraficasReportes = lazy(() => import('../components/graficas/GraficasReportes.jsx'))

const TIPOS_ALERTA = {
  bateria_baja: 'Batería baja',
  consumo_elevado: 'Consumo elevado',
  generacion_nula: 'Generación nula',
  dispositivo_desconectado: 'Dispositivo desconectado',
  sobrecarga: 'Sobrecarga',
}
const NIVELES_ALERTA = { critico: 'Crítico', advertencia: 'Advertencia', informativo: 'Informativo' }
const ROLES = { sistema: 'Sistema', 'dueño': 'Dueño', administrador: 'Administrador', cliente: 'Cliente', desconocido: 'Sin registrar' }

function kwh(valor) {
  return `${formatoNumero(valor)} kWh`
}

function SelectorRango({ seleccion, rango, onAtajo, onPersonalizado }) {
  const [desde, setDesde] = useState(rango.desde)
  const [hasta, setHasta] = useState(rango.hasta)
  const [error, setError] = useState(null)

  function aplicar(evento) {
    evento.preventDefault()
    const problema = errorDeRango(desde, hasta)
    setError(problema)
    if (!problema) onPersonalizado(desde, hasta)
  }

  return (
    <section className={`${CLASE_TARJETA} space-y-3 p-4`}>
      <div className="flex flex-wrap gap-2" role="group" aria-label="Rango del reporte">
        {ATAJOS_RANGO.map((atajo) => (
          <button
            key={atajo.id}
            type="button"
            aria-pressed={seleccion === atajo.id}
            onClick={() => onAtajo(atajo.id)}
            className={seleccion === atajo.id ? BOTON_PRINCIPAL : BOTON_SECUNDARIO}
          >
            {atajo.nombre}
          </button>
        ))}
      </div>
      <form onSubmit={aplicar} noValidate className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <Campo etiqueta="Desde">
          <input type="date" value={desde} onChange={(e) => setDesde(e.target.value)} className={CLASE_CAMPO} />
        </Campo>
        <Campo etiqueta="Hasta">
          <input type="date" value={hasta} onChange={(e) => setHasta(e.target.value)} className={CLASE_CAMPO} />
        </Campo>
        <button type="submit" aria-pressed={seleccion === 'personalizado'} className={seleccion === 'personalizado' ? BOTON_PRINCIPAL : BOTON_SECUNDARIO}>
          Ver este rango
        </button>
      </form>
      {error && <AvisoError>{error}</AvisoError>}
    </section>
  )
}

function Total({ titulo, valor, detalle }) {
  return (
    <section className={`${CLASE_TARJETA} min-w-0 p-4`}>
      <h2 className="text-sm font-medium text-texto-suave">{titulo}</h2>
      <p className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">{valor}</p>
      {detalle && <p className="text-sm text-texto-suave">{detalle}</p>}
    </section>
  )
}

function Totales({ reporte }) {
  const { acciones } = reporte
  const totalAcciones = acciones.automaticas + acciones.manuales
  const cortes = reporte.cortes_por_categoria.reduce((suma, c) => suma + c.cortes, 0)
  const balance = reporte.balance_kwh

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      <Total titulo="Energía generada" valor={kwh(reporte.kwh_generados)} detalle="Los huecos de más de 10 min no cuentan" />
      <Total titulo="Energía consumida" valor={kwh(reporte.kwh_consumidos)} detalle="Los huecos de más de 10 min no cuentan" />
      <Total
        titulo="Balance"
        valor={`${balance > 0 ? '+' : ''}${kwh(balance)}`}
        detalle={balance > 0 ? 'Se generó más de lo que se consumió' : balance < 0 ? 'Se consumió más de lo que se generó' : 'Generación y consumo iguales'}
      />
      <Total titulo="Horas bajo la reserva" valor={`${formatoNumero(reporte.horas_bajo_reserva)} h`} detalle={`Con la batería bajo ${formatoPct(reporte.reserva_soc)}`} />
      <Total titulo="Cortes del sistema" valor={String(cortes)} detalle={`${reporte.cortes_por_toma.length} ${reporte.cortes_por_toma.length === 1 ? 'toma afectada' : 'tomas afectadas'}`} />
      <Total
        titulo="Acciones"
        valor={String(totalAcciones)}
        detalle={`${acciones.automaticas} automáticas · ${acciones.manuales} manuales`}
      />
    </div>
  )
}

function TablaCortes({ reporte }) {
  const tomas = reporte.cortes_por_toma
  return (
    <section className={`${CLASE_TARJETA} min-w-0 p-4 sm:p-5`}>
      <h2 className="text-sm font-medium">Cortes por toma</h2>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-texto-suave">
        {reporte.cortes_por_categoria.map((c) => (
          <li key={c.categoria} className="flex items-center gap-1.5">
            <span aria-hidden="true" className={`size-2 rounded-full ${INFO_CATEGORIA[c.categoria]?.punto ?? 'bg-borde-fuerte'}`} />
            {INFO_CATEGORIA[c.categoria]?.nombre ?? c.categoria}: <strong className="font-semibold text-texto tabular-nums">{c.cortes}</strong>
          </li>
        ))}
      </ul>
      {tomas.length === 0 ? (
        <p className="mt-3 text-sm text-texto-suave">El sistema no apagó ninguna toma en este rango.</p>
      ) : (
        <div className="mt-3 overflow-x-auto" tabIndex={0} role="region" aria-label="Tabla de cortes por toma">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-texto-suave">
              <tr className="border-b border-borde">
                <th scope="col" className="py-1.5 pr-2 sm:pr-3 font-medium">
                  Toma
                </th>
                <th scope="col" className="px-2 py-1.5 sm:px-3 font-medium">
                  Categoría
                </th>
                <th scope="col" className="px-2 py-1.5 sm:px-3 text-right font-medium">
                  Cortes
                </th>
                <th scope="col" className="py-1.5 pl-2 sm:pl-3 text-right font-medium">
                  Horas apagada
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-borde tabular-nums">
              {tomas.map((t) => (
                <tr key={t.dispositivo_id}>
                  <th scope="row" className="max-w-48 py-1.5 pr-2 sm:pr-3 font-normal break-words">
                    {t.nombre}
                    {!t.activo && (
                      <>
                        {' '}
                        <span className="mt-0.5 block w-fit rounded-full bg-superficie-suave px-2 py-0.5 text-xs text-texto-suave">
                          Desactivada
                        </span>
                      </>
                    )}
                  </th>
                  <td className="px-2 py-1.5 sm:px-3">{INFO_CATEGORIA[t.categoria]?.nombre ?? t.categoria}</td>
                  <td className="px-2 py-1.5 sm:px-3 text-right">{t.cortes}</td>
                  <td className="py-1.5 pl-2 sm:pl-3 text-right">{formatoNumero(t.horas_apagada)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-2 text-xs text-texto-suave">
        La categoría es la actual de cada toma. En una toma desactivada, las horas apagada terminan cuando se desactivó.
      </p>
    </section>
  )
}

function ResumenAlertas({ alertas, acciones }) {
  const minutos = alertas.minutos_medios_hasta_atendida
  return (
    <section className={`${CLASE_TARJETA} min-w-0 space-y-3 p-4 sm:p-5`}>
      <div>
        <h2 className="text-sm font-medium">Alertas</h2>
        {alertas.total === 0 ? (
          <p className="mt-2 text-sm text-texto-suave">No hubo alertas en este rango.</p>
        ) : (
          <ul className="mt-2 divide-y divide-borde text-sm">
            {alertas.por_tipo.map((a) => (
              <li key={`${a.tipo_alerta}-${a.nivel_prioridad}`} className="flex items-baseline justify-between gap-3 py-1.5">
                <span className="min-w-0">
                  {TIPOS_ALERTA[a.tipo_alerta] ?? a.tipo_alerta ?? 'Sin tipo'}
                  {a.nivel_prioridad && <span className="text-texto-suave"> · {NIVELES_ALERTA[a.nivel_prioridad] ?? a.nivel_prioridad}</span>}
                </span>
                <strong className="font-semibold tabular-nums">{a.total}</strong>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-sm text-texto-suave">
          {minutos == null
            ? 'Tiempo medio hasta atenderlas: sin datos.'
            : `Tiempo medio hasta atenderlas: ${formatoNumero(minutos)} min (${alertas.alertas_con_tiempo} ${alertas.alertas_con_tiempo === 1 ? 'alerta' : 'alertas'}).`}
        </p>
      </div>
      <div className="border-t border-borde pt-3">
        <h2 className="text-sm font-medium">Acciones por origen</h2>
        {acciones.por_rol.length === 0 ? (
          <p className="mt-2 text-sm text-texto-suave">No hubo acciones en este rango.</p>
        ) : (
          <ul className="mt-2 divide-y divide-borde text-sm">
            {acciones.por_rol.map((r) => (
              <li key={r.rol} className="flex items-baseline justify-between gap-3 py-1.5">
                <span>{ROLES[r.rol] ?? r.rol}</span>
                <strong className="font-semibold tabular-nums">{r.total}</strong>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

function TablaPorDia({ dias }) {
  return (
    <details className={`${CLASE_TARJETA} p-4 sm:p-5`}>
      <summary className="flex min-h-11 cursor-pointer items-center text-sm font-medium">Ver los datos por día</summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-right text-sm tabular-nums">
          <thead className="text-xs text-texto-suave">
            <tr className="border-b border-borde">
              <th scope="col" className="py-1.5 pr-3 text-left font-medium">
                Fecha
              </th>
              <th scope="col" className="px-2 py-1.5 font-medium">
                kWh gen.
              </th>
              <th scope="col" className="px-2 py-1.5 font-medium">
                kWh cons.
              </th>
              <th scope="col" className="px-2 py-1.5 font-medium">
                SoC mín.
              </th>
              <th scope="col" className="px-2 py-1.5 font-medium">
                SoC máx.
              </th>
              <th scope="col" className="py-1.5 pl-2 font-medium">
                SoC prom.
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-borde">
            {dias.map((d) => (
              <tr key={d.fecha}>
                <th scope="row" className="py-1.5 pr-3 text-left font-normal whitespace-nowrap">
                  {d.fecha}
                </th>
                <td className="px-2 py-1.5">{d.kwh_generados == null ? '—' : formatoNumero(d.kwh_generados)}</td>
                <td className="px-2 py-1.5">{d.kwh_consumidos == null ? '—' : formatoNumero(d.kwh_consumidos)}</td>
                <td className="px-2 py-1.5">{d.soc_minimo == null ? '—' : formatoNumero(d.soc_minimo)}</td>
                <td className="px-2 py-1.5">{d.soc_maximo == null ? '—' : formatoNumero(d.soc_maximo)}</td>
                <td className="py-1.5 pl-2">{d.soc_promedio == null ? '—' : formatoNumero(d.soc_promedio)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

function descargarCsv(reporte) {
  const url = URL.createObjectURL(new Blob([csvDeReporte(reporte)], { type: 'text/csv;charset=utf-8' }))
  const enlace = document.createElement('a')
  enlace.href = url
  enlace.download = nombreDeCsv(reporte)
  document.body.append(enlace)
  enlace.click()
  enlace.remove()
  URL.revokeObjectURL(url)
}

function ContenidoReportes({ domoId }) {
  const [consulta, setConsulta] = useState(() => ({ seleccion: '7d', ...rangoDeAtajo('7d') }))

  const consultar = useCallback(() => {
    const q = new URLSearchParams({ desde: consulta.desde, hasta: consulta.hasta })
    return apiFetch(`/domos/${domoId}/reportes/resumen?${q}`)
  }, [domoId, consulta])
  const { datos, error, cargando } = useConsulta(consultar)

  return (
    <div className="space-y-4">
      <SelectorRango
        key={`${consulta.desde}-${consulta.hasta}`}
        seleccion={consulta.seleccion}
        rango={consulta}
        onAtajo={(id) => setConsulta({ seleccion: id, ...rangoDeAtajo(id) })}
        onPersonalizado={(desde, hasta) => setConsulta({ seleccion: 'personalizado', desde, hasta })}
      />
      {error && <AvisoError>No se pudo cargar el reporte: {error}</AvisoError>}
      {!datos ? (
        !error && <div className={`${CLASE_TARJETA} h-64 animate-pulse`} aria-label="Cargando reporte" />
      ) : (
        <div className={`space-y-4 transition-opacity ${cargando ? 'opacity-60' : ''}`}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-texto-suave">
              Del {datos.desde} al {datos.hasta}, en hora de Colombia.
            </p>
            <button type="button" onClick={() => descargarCsv(datos)} className={BOTON_SECUNDARIO}>
              Descargar CSV
            </button>
          </div>
          <Totales reporte={datos} />
          <Suspense fallback={<div className={`${CLASE_TARJETA} h-72 animate-pulse`} />}>
            <GraficasReportes dias={datos.dias} perfilHorario={datos.perfil_horario} reserva={datos.reserva_soc} />
          </Suspense>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <TablaCortes reporte={datos} />
            <ResumenAlertas alertas={datos.alertas} acciones={datos.acciones} />
          </div>
          {datos.dias.length > 0 ? <TablaPorDia dias={datos.dias} /> : <Vacio>Sin datos.</Vacio>}
        </div>
      )}
    </div>
  )
}

export default function Reportes() {
  return <PaginaDomo seccion="Reportes">{(domo) => <ContenidoReportes domoId={domo.id} />}</PaginaDomo>
}
