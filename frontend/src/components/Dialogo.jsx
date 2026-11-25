import { useEffect, useRef } from 'react'
import { BOTON_PELIGRO, BOTON_PRINCIPAL, BOTON_SECUNDARIO } from './ui.jsx'

// <dialog> nativo: atrapa el foco, cierra con Esc y queda sobre todo lo demás,
// incluso sobre otro diálogo. En el celular es una hoja que sube desde abajo.
export default function Dialogo({ titulo, onCerrar, children }) {
  const ref = useRef(null)

  useEffect(() => {
    const dialogo = ref.current
    if (!dialogo.open) dialogo.showModal()
  }, [])

  return (
    <dialog
      ref={ref}
      aria-label={titulo}
      onClose={onCerrar}
      // Solo un clic en el fondo tiene como destino el propio <dialog>.
      onClick={(e) => e.target === ref.current && onCerrar()}
      className="m-0 mt-auto max-h-[92svh] w-full max-w-none rounded-t-2xl bg-superficie text-texto shadow-xl backdrop:bg-stone-900/40 sm:m-auto sm:max-w-lg sm:rounded-2xl"
    >
      <div className="space-y-4 p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] sm:p-6">
        <div className="flex items-start justify-between gap-3">
          <h2 className="min-w-0 text-lg font-semibold tracking-tight break-words">{titulo}</h2>
          <button
            type="button"
            onClick={onCerrar}
            aria-label="Cerrar"
            className="-m-1 flex size-11 shrink-0 items-center justify-center rounded-lg text-texto-suave hover:bg-superficie-suave"
          >
            ✕
          </button>
        </div>
        {children}
      </div>
    </dialog>
  )
}

export function DialogoConfirmacion({ titulo, textoConfirmar, peligro, ocupado, onConfirmar, onCancelar, children }) {
  return (
    <Dialogo titulo={titulo} onCerrar={onCancelar}>
      <div className="space-y-2 text-sm text-texto-suave">{children}</div>
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button type="button" onClick={onCancelar} className={BOTON_SECUNDARIO}>
          Cancelar
        </button>
        <button type="button" disabled={ocupado} onClick={onConfirmar} className={peligro ? BOTON_PELIGRO : BOTON_PRINCIPAL}>
          {textoConfirmar}
        </button>
      </div>
    </Dialogo>
  )
}
