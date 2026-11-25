import EtiquetaDemostracion from '../EtiquetaDemostracion.jsx'
import { Link } from 'react-router-dom'
import { IconoCampana } from '../Iconos.jsx'
import { estadoEnergiaDe, textoPorCortar } from './estadosEnergia.js'

export default function SaludoEstado({ nombre, domo, control, alertasActivas }) {
  const estado = estadoEnergiaDe(control)
  const porCortar = textoPorCortar(control)

  return (
    <header className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight break-words">Hola, {nombre}</h1>
        <p className="text-sm text-texto-suave">
          {domo.nombre}
          {domo.ubicacion ? ` · ${domo.ubicacion}` : ''}
        </p>
        <EtiquetaDemostracion domo={domo} className="mt-1.5" />
      </div>
      <div className="flex min-w-0 flex-col gap-2 lg:items-end">
        <div className="flex flex-wrap items-center gap-2">
          <span
            aria-label={`Estado energético: ${estado.nombre}`}
            className={`inline-flex min-h-11 items-center gap-2 rounded-full px-4 text-base font-semibold ${estado.etiqueta}`}
          >
            <span aria-hidden="true">{estado.simbolo}</span>
            {estado.nombre}
          </span>
          {alertasActivas > 0 && (
            <Link
              to="/alertas"
              className="inline-flex min-h-11 items-center gap-2 rounded-full border border-supervivencia/30 bg-supervivencia-suave px-4 text-sm font-semibold text-supervivencia"
            >
              <IconoCampana />
              {alertasActivas === 1 ? '1 alerta activa' : `${alertasActivas} alertas activas`}
            </Link>
          )}
        </div>
        <p className="text-sm text-texto-suave lg:text-right">{porCortar ?? estado.detalle}</p>
      </div>
    </header>
  )
}
