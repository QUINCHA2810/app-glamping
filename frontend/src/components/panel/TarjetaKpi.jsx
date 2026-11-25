import { CLASE_TARJETA } from '../ui.jsx'

// Tarjeta de indicador: ícono y título, valor grande, un detalle y su tendencia.
export default function TarjetaKpi({ Icono, titulo, valor, claseValor = 'text-texto', detalle, children }) {
  return (
    <section className={`${CLASE_TARJETA} flex min-w-0 flex-col gap-2 p-4`}>
      <h2 className="flex items-center gap-2 text-sm font-medium text-texto-suave">
        <Icono className="size-5 shrink-0" />
        {titulo}
      </h2>
      <p className={`text-3xl font-semibold tracking-tight tabular-nums ${claseValor}`}>{valor}</p>
      {detalle && <div className="text-sm text-texto-suave">{detalle}</div>}
      {children}
    </section>
  )
}
