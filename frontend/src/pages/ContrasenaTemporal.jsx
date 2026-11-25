import { useAuth } from '../auth/AuthContext.jsx'
import { IconoSalir } from '../components/Iconos.jsx'
import Logo from '../components/Logo.jsx'
import { BOTON_SECUNDARIO } from '../components/ui.jsx'
import { NOMBRE_APP } from '../config/marca.js'
import MiCuenta from './MiCuenta.jsx'

// Con una contraseña temporal el backend solo deja ver el perfil y cambiarla: no hay
// menú ni domos que cargar, solo Mi cuenta. Al cambiarla se vuelve a la app normal.
export default function ContrasenaTemporal() {
  const { logout } = useAuth()
  return (
    <div className="min-h-svh bg-fondo">
      <header className="flex items-center gap-3 border-b border-borde bg-superficie px-4 py-3 pt-[max(0.75rem,env(safe-area-inset-top))]">
        <Logo className="size-8 shrink-0" />
        <span className="font-semibold tracking-tight">{NOMBRE_APP}</span>
        <button type="button" onClick={logout} className={`${BOTON_SECUNDARIO} ml-auto gap-2`}>
          <IconoSalir />
          Cerrar sesión
        </button>
      </header>
      <main className="mx-auto max-w-xl space-y-4 px-4 py-6">
        <div className="rounded-2xl border border-ahorro/40 bg-ahorro-suave p-4 text-sm text-texto">
          <p className="font-semibold">Cambia tu contraseña para continuar</p>
          <p className="mt-1">
            Entraste con una contraseña temporal que te dio el dueño. Elige una nueva que solo tú conozcas: después
            podrás usar {NOMBRE_APP} con normalidad.
          </p>
        </div>
        <MiCuenta />
      </main>
    </div>
  )
}
