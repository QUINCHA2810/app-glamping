import { useCallback, useState } from 'react'
import { apiFetch } from '../api/client.js'
import { useAuth } from '../auth/AuthContext.jsx'
import AsistenteDispositivo from '../components/dispositivos/AsistenteDispositivo.jsx'
import Interruptor from '../components/dispositivos/Interruptor.jsx'
import LineaSupervivencia from '../components/dispositivos/LineaSupervivencia.jsx'
import PanelDispositivo from '../components/dispositivos/PanelDispositivo.jsx'
import TarjetaDispositivo from '../components/dispositivos/TarjetaDispositivo.jsx'
import { INFO_CATEGORIA, textoUmbrales } from '../components/dispositivos/categorias.js'
import { AvisoError, BOTON_PRINCIPAL, CLASE_TARJETA, EncabezadoPagina } from '../components/ui.jsx'
import { useDomo } from '../domos/DomoContext.jsx'
import { useConsulta } from '../hooks/useConsulta.js'
import { usePolling } from '../hooks/usePolling.js'
import { esDuenoDe } from '../utils/roles.js'

const INTERVALO_MS = 10000

// Presets, escenas y colores del bombillo (servicios/iluminacion.py): no dependen del domo.
const cargarOpcionesIluminacion = () => apiFetch('/iluminacion/opciones')

const GRUPOS = [
  { tipo: 'toma', titulo: 'Tomacorrientes', vacio: 'Este domo no tiene tomacorrientes registrados.' },
  { tipo: 'bombillo', titulo: 'Bombillos', vacio: 'Este domo no tiene bombillos registrados.' },
]

function Esqueleto() {
  return (
    <div className="grid animate-pulse gap-4 sm:grid-cols-2 xl:grid-cols-3" aria-label="Cargando dispositivos">
      {Array.from({ length: 3 }, (_, i) => (
        <div key={i} className="h-44 rounded-2xl border border-borde bg-superficie" />
      ))}
    </div>
  )
}

function ExplicacionCategorias({ categorias }) {
  return (
    <section className={`${CLASE_TARJETA} p-4`}>
      <h2 className="text-sm font-semibold text-texto">Categorías</h2>
      <ul className="mt-2 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {categorias.categorias.map((preset) => {
          const info = INFO_CATEGORIA[preset.categoria]
          return (
            <li key={preset.categoria} className="text-sm">
              <span className="flex items-center gap-1.5 font-semibold text-texto">
                <span aria-hidden="true" className={`size-2 rounded-full ${info.punto}`} />
                {info.nombre}
              </span>
              <span className="block text-texto-suave">{info.explicacion}</span>
              <span className="block text-xs text-texto-suave tabular-nums">{textoUmbrales(preset)}</span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function ListaDispositivos({ domo }) {
  const domoId = domo.id
  const { usuario } = useAuth()
  // El rol en ESTE domo: quien es dueño de otro domo aquí puede ser solo administrador.
  const esDueno = esDuenoDe(domo, usuario)
  const [mostrarDesactivados, setMostrarDesactivados] = useState(false)
  const [abiertoId, setAbiertoId] = useState(null)
  const [anadiendo, setAnadiendo] = useState(false)

  // Siempre con los desactivados: el interruptor los muestra al instante, sin recargar.
  const consultar = useCallback(async () => {
    const [dispositivos, general] = await Promise.all([
      apiFetch(`/domos/${domoId}/dispositivos?incluir_inactivos=true`),
      apiFetch(`/domos/${domoId}/estado-general`),
    ])
    return { dispositivos, soc: general.estado_carga_soc }
  }, [domoId])
  const { datos, error, refrescar } = usePolling(consultar, INTERVALO_MS)

  // Solo cambian desde la página de configuración: basta con leerlos al entrar.
  const consultarAjustes = useCallback(async () => {
    const [categorias, configuracion] = await Promise.all([
      apiFetch(`/domos/${domoId}/categorias`),
      apiFetch(`/domos/${domoId}/configuracion`),
    ])
    return { categorias, configuracion }
  }, [domoId])
  const ajustes = useConsulta(consultarAjustes)
  const iluminacion = useConsulta(cargarOpcionesIluminacion)

  if (!datos) {
    return error ? <AvisoError>No se pudieron cargar los dispositivos: {error}</AvisoError> : <Esqueleto />
  }

  const { dispositivos, soc } = datos
  const categorias = ajustes.datos?.categorias
  const configuracion = ajustes.datos?.configuracion
  const activos = dispositivos.filter((d) => d.activo)
  const hayDesactivados = activos.length < dispositivos.length
  const visibles = mostrarDesactivados ? dispositivos : activos
  const abierto = dispositivos.find((d) => d.id === abiertoId)

  return (
    <div className="space-y-6">
      {error && <AvisoError>No se pudo actualizar la lista: {error}</AvisoError>}
      {ajustes.error && <AvisoError>No se pudieron cargar las categorías: {ajustes.error}</AvisoError>}

      {categorias && <ExplicacionCategorias categorias={categorias} />}
      <LineaSupervivencia dispositivos={activos} soc={soc} reserva={categorias?.reserva_soc} configuracion={configuracion} />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm font-medium text-texto">
          <Interruptor
            encendido={mostrarDesactivados}
            etiqueta="Mostrar desactivados"
            onCambiar={setMostrarDesactivados}
          />
          <span aria-hidden="true">
            Mostrar desactivados
            {hayDesactivados && ` (${dispositivos.length - activos.length})`}
          </span>
        </label>
        {esDueno && (
          <button type="button" onClick={() => setAnadiendo(true)} className={BOTON_PRINCIPAL}>
            Añadir dispositivo
          </button>
        )}
      </div>

      {GRUPOS.map((grupo) => {
        const lista = visibles.filter((d) => d.tipo === grupo.tipo)
        return (
          <section key={grupo.tipo} className="space-y-3">
            <h2 className="flex items-baseline gap-2 text-lg font-semibold tracking-tight">
              {grupo.titulo}
              <span className="text-sm font-normal text-texto-suave">({lista.length})</span>
            </h2>
            {lista.length === 0 ? (
              <p className="rounded-2xl border border-dashed border-borde-fuerte bg-superficie p-4 text-sm text-texto-suave">
                {grupo.vacio}
              </p>
            ) : (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {lista.map((d) => (
                  <TarjetaDispositivo
                    key={d.id}
                    domoId={domoId}
                    dispositivo={d}
                    configuracion={configuracion}
                    iluminacion={iluminacion.datos}
                    soc={soc}
                    reserva={categorias?.reserva_soc}
                    esDueno={esDueno}
                    onAbrir={() => setAbiertoId(d.id)}
                    onActualizado={refrescar}
                  />
                ))}
              </div>
            )}
          </section>
        )
      })}

      {abierto && (
        <PanelDispositivo
          key={abierto.id}
          domoId={domoId}
          dispositivo={abierto}
          categorias={categorias}
          configuracion={configuracion}
          iluminacion={iluminacion}
          soc={soc}
          reserva={categorias?.reserva_soc}
          esDueno={esDueno}
          onCerrar={() => setAbiertoId(null)}
          onActualizado={refrescar}
        />
      )}
      {anadiendo && (
        <AsistenteDispositivo
          domoId={domoId}
          categorias={categorias}
          onCerrar={() => setAnadiendo(false)}
          onCreado={refrescar}
        />
      )}
    </div>
  )
}

export default function Dispositivos() {
  const { domo, cargando } = useDomo()

  if (cargando) return <Esqueleto />
  if (!domo) {
    return (
      <div className={`${CLASE_TARJETA} p-6 text-sm text-texto-suave`}>
        No tienes domos disponibles. Pide al dueño que te asigne uno.
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <EncabezadoPagina seccion="Dispositivos" titulo={domo.nombre} domo={domo} />
      {/* key: al cambiar de domo se reinicia todo el estado de la lista */}
      <ListaDispositivos key={domo.id} domo={domo} />
    </div>
  )
}
