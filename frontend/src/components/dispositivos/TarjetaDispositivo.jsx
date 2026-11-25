import { useState } from 'react'
import { apiFetch } from '../../api/client.js'
import { textoLuz } from '../../utils/iluminacion.js'
import { AvisoError, BOTON_SECUNDARIO, CLASE_TARJETA } from '../ui.jsx'
import ControlEncendido from './ControlEncendido.jsx'
import EtiquetaCategoria from './EtiquetaCategoria.jsx'
import { umbralApagadoEfectivo, umbralReactivacionEfectivo } from './categorias.js'

function Marca({ children }) {
  return <span className="rounded-full bg-superficie-suave px-2 py-0.5 text-xs font-medium text-texto-suave">{children}</span>
}

// Tocar la parte superior abre el panel de configuración. El botón de encender
// queda fuera de esa zona: un control no puede ir dentro de otro.
export default function TarjetaDispositivo({
  domoId,
  dispositivo,
  configuracion,
  iluminacion,
  soc,
  reserva,
  esDueno,
  onAbrir,
  onActualizado,
}) {
  const [reactivando, setReactivando] = useState(false)
  const [error, setError] = useState(null)
  const esBombillo = dispositivo.tipo === 'bombillo'
  // Modo y preset o escena activos ("Blanco · Lectura", "Escena · Vela"); en una escena
  // el brillo no se muestra: lo pone la escena.
  const luz = esBombillo ? textoLuz(dispositivo, iluminacion) : null
  const muestraBrillo = esBombillo && dispositivo.brillo != null && dispositivo.modo_luz !== 'escena'

  async function reactivar() {
    setReactivando(true)
    setError(null)
    try {
      await apiFetch(`/domos/${domoId}/dispositivos/${dispositivo.id}`, { method: 'PATCH', body: { activo: true } })
      onActualizado()
    } catch (e) {
      setError(e.message)
    } finally {
      setReactivando(false)
    }
  }

  return (
    <article
      className={`${CLASE_TARJETA} flex min-w-0 flex-col gap-3 p-4 ${dispositivo.activo ? '' : 'border-dashed'}`}
    >
      <button
        type="button"
        onClick={onAbrir}
        className="-m-1 flex min-h-11 min-w-0 flex-col gap-1.5 rounded-lg p-1 text-left hover:bg-superficie-suave"
      >
        <span className="flex w-full items-start justify-between gap-2">
          <span className="min-w-0 truncate font-medium text-texto" title={dispositivo.nombre}>
            <span className="sr-only">Ver la configuración de </span>
            {dispositivo.nombre}
          </span>
          <span aria-hidden="true" className="shrink-0 text-texto-suave">
            ›
          </span>
        </span>
        <span className="flex flex-wrap gap-1.5">
          <EtiquetaCategoria categoria={dispositivo.categoria} />
          {dispositivo.personalizado && <Marca>Personalizado</Marca>}
          {!dispositivo.activo && <Marca>Desactivado</Marca>}
          <Marca>{dispositivo.id_fisico != null ? 'Real' : 'Simulado'}</Marca>
        </span>
        <span className="text-sm text-texto-suave">{dispositivo.ubicacion || 'Sin ubicación'}</span>
        {(luz || muestraBrillo) && (
          <span className="text-sm text-texto-suave tabular-nums">
            {[luz, muestraBrillo && `Brillo ${dispositivo.brillo} %`].filter(Boolean).join(' · ')}
          </span>
        )}
      </button>

      {dispositivo.activo ? (
        <ControlEncendido
          domoId={domoId}
          dispositivo={dispositivo}
          soc={soc}
          umbralApagado={umbralApagadoEfectivo(dispositivo, configuracion)}
          umbralReactivacion={umbralReactivacionEfectivo(dispositivo, configuracion)}
          reserva={reserva}
          onActualizado={onActualizado}
        />
      ) : (
        esDueno && (
          <div className="space-y-2">
            <button type="button" disabled={reactivando} onClick={reactivar} className={`${BOTON_SECUNDARIO} w-full`}>
              {reactivando ? 'Reactivando…' : 'Reactivar'}
            </button>
            {error && <AvisoError>{error}</AvisoError>}
          </div>
        )
      )}
    </article>
  )
}
