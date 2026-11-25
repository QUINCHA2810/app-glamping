import { useDomo } from '../domos/DomoContext.jsx'
import { CLASE_TARJETA, EncabezadoPagina } from './ui.jsx'

// Marco común de las páginas que dependen del domo seleccionado: resuelve
// "cargando domos" y "sin domos", pone el encabezado y monta el contenido
// con key={domo.id} para que al cambiar de domo se reinicie su estado.
export default function PaginaDomo({ seccion, children }) {
  const { domo, cargando } = useDomo()

  if (cargando) return <p className="text-sm text-texto-suave">Cargando…</p>
  if (!domo) {
    return (
      <div className={`${CLASE_TARJETA} p-6 text-sm text-texto-suave`}>
        No tienes domos disponibles. Pide al dueño que te asigne uno.
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <EncabezadoPagina seccion={seccion} titulo={domo.nombre} domo={domo} />
      <div key={domo.id}>{children(domo)}</div>
    </div>
  )
}
