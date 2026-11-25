import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext.jsx'
import { ESLOGAN, NOMBRE_APP } from '../config/marca.js'
import { useDomo } from '../domos/DomoContext.jsx'
import { nombreEnSelector } from '../utils/demostracion.js'
import Dialogo from './Dialogo.jsx'
import {
  IconoAjustes,
  IconoCampana,
  IconoCuenta,
  IconoDocumento,
  IconoEnchufe,
  IconoGrafica,
  IconoMas,
  IconoPanel,
  IconoPersonas,
  IconoReloj,
  IconoSalir,
} from './Iconos.jsx'
import EtiquetaDemostracion from './EtiquetaDemostracion.jsx'
import Logo from './Logo.jsx'
import { BOTON_SECUNDARIO, CLASE_CAMPO } from './ui.jsx'

const SECCIONES = [
  { ruta: '/', nombre: 'Panel', Icono: IconoPanel },
  { ruta: '/dispositivos', nombre: 'Dispositivos', Icono: IconoEnchufe },
  { ruta: '/historicos', nombre: 'Históricos', Icono: IconoGrafica },
  { ruta: '/alertas', nombre: 'Alertas', Icono: IconoCampana },
  { ruta: '/reportes', nombre: 'Reportes', Icono: IconoDocumento, enMas: true },
  { ruta: '/historial', nombre: 'Historial', Icono: IconoReloj, enMas: true },
  { ruta: '/configuracion', nombre: 'Configuración', Icono: IconoAjustes, enMas: true, soloDueno: true },
  { ruta: '/administradores', nombre: 'Administradores', Icono: IconoPersonas, enMas: true, soloDueno: true },
  { ruta: '/mi-cuenta', nombre: 'Mi cuenta', Icono: IconoCuenta, enMas: true },
]

const NOMBRE_ROL = { dueño: 'Dueño', administrador: 'Administrador' }

function useSecciones() {
  const { usuario } = useAuth()
  return SECCIONES.filter((s) => !s.soloDueno || usuario?.rol === 'dueño')
}

function SelectorDomo({ conEtiqueta }) {
  const { domos, domo, domoId, setDomoId, cargando, error } = useDomo()

  if (cargando) return <span className="text-sm text-texto-suave">Cargando domos…</span>
  if (error) return <span className="text-sm text-supervivencia">{error}</span>
  if (domos.length === 0) return <span className="text-sm text-texto-suave">Sin domos asignados</span>

  return (
    <label className="flex min-w-0 flex-col gap-1 text-sm">
      <span className={conEtiqueta ? 'text-xs font-medium text-texto-suave' : 'sr-only'}>Domo</span>
      <select value={domoId ?? ''} onChange={(e) => setDomoId(Number(e.target.value))} className={`${CLASE_CAMPO} truncate`}>
        {domos.map((d) => (
          <option key={d.id} value={d.id}>
            {nombreEnSelector(d)}
          </option>
        ))}
      </select>
      {conEtiqueta && <EtiquetaDemostracion domo={domo} />}
    </label>
  )
}

function Usuario() {
  const { usuario } = useAuth()
  if (!usuario) return null
  return (
    <div className="min-w-0 leading-tight">
      <div className="truncate text-sm font-medium">
        {usuario.nombre} {usuario.apellido ?? ''}
      </div>
      <div className="text-xs text-texto-suave">{NOMBRE_ROL[usuario.rol] ?? usuario.rol}</div>
    </div>
  )
}

function BotonSalir({ className = '' }) {
  const { logout } = useAuth()
  return (
    <button type="button" onClick={logout} className={`${BOTON_SECUNDARIO} gap-2 ${className}`}>
      <IconoSalir />
      Cerrar sesión
    </button>
  )
}

// El activo se distingue por el fondo, el peso del texto y una barra: no solo por el color.
// `compacto`: el menú lateral (pantallas md+, con mouse), más bajo para que las nueve
// secciones y el pie quepan en una laptop al 100 % sin barra de desplazamiento. En el
// celular (diálogo "Más") se conservan los 44 px de alto para el dedo.
function claseEnlace({ isActive }, compacto = false) {
  return `flex items-center gap-3 rounded-lg border-l-4 px-3 text-sm transition ${compacto ? 'min-h-9 py-1.5' : 'min-h-11 py-2'} ${
    isActive
      ? 'border-marca bg-marca-suave font-semibold text-marca-fuerte'
      : 'border-transparent font-medium text-texto-suave hover:bg-superficie-suave hover:text-texto'
  }`
}

function MenuLateral() {
  const secciones = useSecciones()
  return (
    <aside className="sticky top-0 hidden h-svh w-64 shrink-0 flex-col border-r border-borde bg-superficie md:flex">
      <div className="flex items-center gap-3 px-4 py-3">
        <Logo className="size-9 shrink-0" />
        <div className="min-w-0">
          <p className="text-lg leading-tight font-semibold tracking-tight">{NOMBRE_APP}</p>
          <p className="text-xs leading-tight text-texto-suave">{ESLOGAN}</p>
        </div>
      </div>
      <nav aria-label="Secciones" className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-3 py-1">
        {secciones.map(({ ruta, nombre, Icono }) => (
          <NavLink key={ruta} to={ruta} end={ruta === '/'} className={(estado) => claseEnlace(estado, true)}>
            <Icono />
            {nombre}
          </NavLink>
        ))}
      </nav>
      <div className="space-y-2 border-t border-borde px-4 py-3">
        <SelectorDomo conEtiqueta />
        <Usuario />
        <BotonSalir className="w-full" />
      </div>
    </aside>
  )
}

function claseBarra(activo) {
  return `flex min-h-14 flex-col items-center justify-center gap-0.5 border-t-2 text-[11px] leading-tight transition ${
    activo ? 'border-marca font-semibold text-marca-fuerte' : 'border-transparent font-medium text-texto-suave'
  }`
}

function BarraInferior() {
  const secciones = useSecciones()
  const { pathname } = useLocation()
  const [masAbierto, setMasAbierto] = useState(false)
  const principales = secciones.filter((s) => !s.enMas)
  const adicionales = secciones.filter((s) => s.enMas)
  const estaEnMas = adicionales.some((s) => pathname.startsWith(s.ruta))

  return (
    <>
      <nav
        aria-label="Secciones"
        className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-borde bg-superficie pb-[env(safe-area-inset-bottom)] md:hidden"
      >
        {principales.map(({ ruta, nombre, Icono }) => (
          <NavLink key={ruta} to={ruta} end={ruta === '/'} className={({ isActive }) => claseBarra(isActive)}>
            <Icono />
            {nombre}
          </NavLink>
        ))}
        <button
          type="button"
          onClick={() => setMasAbierto(true)}
          aria-haspopup="dialog"
          className={claseBarra(estaEnMas)}
        >
          <IconoMas />
          Más
        </button>
      </nav>

      {masAbierto && (
        <Dialogo titulo="Más" onCerrar={() => setMasAbierto(false)}>
          <nav aria-label="Más secciones" className="flex flex-col gap-1">
            {adicionales.map(({ ruta, nombre, Icono }) => (
              <NavLink key={ruta} to={ruta} onClick={() => setMasAbierto(false)} className={claseEnlace}>
                <Icono />
                {nombre}
              </NavLink>
            ))}
          </nav>
          <div className="space-y-3 border-t border-borde pt-4">
            <Usuario />
            <BotonSalir className="w-full" />
          </div>
        </Dialogo>
      )}
    </>
  )
}

export default function Layout() {
  // Viene de cambiar una contraseña temporal (ver MiCuenta).
  const { state } = useLocation()
  return (
    <div className="min-h-svh md:flex">
      <MenuLateral />
      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-borde bg-superficie px-4 pt-[env(safe-area-inset-top)] pb-2 md:hidden">
          <div className="flex shrink-0 items-center gap-2 pt-2">
            <Logo className="size-8" />
            <span className="font-semibold tracking-tight">{NOMBRE_APP}</span>
          </div>
          <div className="ml-auto min-w-0 flex-1 pt-2 sm:max-w-64 sm:flex-none">
            <SelectorDomo />
          </div>
        </header>
        <main className="mx-auto max-w-7xl p-4 pb-[calc(5.5rem+env(safe-area-inset-bottom))] sm:p-6 sm:pb-[calc(5.5rem+env(safe-area-inset-bottom))] md:pb-8 lg:p-8">
          {state?.contrasenaCambiada && (
            <p role="status" className="mb-4 rounded-lg bg-normal-suave px-3 py-2 text-sm text-normal">
              Contraseña cambiada. Ya puedes usar {NOMBRE_APP}.
            </p>
          )}
          <Outlet />
        </main>
      </div>
      <BarraInferior />
    </div>
  )
}
