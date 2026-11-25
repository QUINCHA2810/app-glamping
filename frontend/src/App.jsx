import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './auth/AuthContext.jsx'
import Layout from './components/Layout.jsx'
import { DomoProvider } from './domos/DomoContext.jsx'
import Administradores from './pages/Administradores.jsx'
import Alertas from './pages/Alertas.jsx'
import Configuracion from './pages/Configuracion.jsx'
import ContrasenaTemporal from './pages/ContrasenaTemporal.jsx'
import Dispositivos from './pages/Dispositivos.jsx'
import Historial from './pages/Historial.jsx'
import Historicos from './pages/Historicos.jsx'
import Login from './pages/Login.jsx'
import MiCuenta from './pages/MiCuenta.jsx'
import Panel from './pages/Panel.jsx'
import Reportes from './pages/Reportes.jsx'

// Sin token → /login. Mientras se valida un token guardado (/users/me) no
// se muestra nada, para no parpadear entre login y panel. Con una contraseña
// temporal solo se muestra Mi cuenta: el backend responde 403 a todo lo demás.
function RutaProtegida() {
  const { token, usuario, cargando } = useAuth()
  const { pathname } = useLocation()

  if (!token) return <Navigate to="/login" replace />
  if (cargando) {
    return <div className="flex min-h-svh items-center justify-center text-sm text-texto-suave">Cargando…</div>
  }
  if (usuario.debe_cambiar_contrasena) {
    return pathname === '/mi-cuenta' ? <ContrasenaTemporal /> : <Navigate to="/mi-cuenta" replace />
  }

  return (
    <DomoProvider>
      <Outlet />
    </DomoProvider>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RutaProtegida />}>
        <Route element={<Layout />}>
          <Route index element={<Panel />} />
          <Route path="dispositivos" element={<Dispositivos />} />
          <Route path="historicos" element={<Historicos />} />
          <Route path="historial" element={<Historial />} />
          <Route path="alertas" element={<Alertas />} />
          <Route path="configuracion" element={<Configuracion />} />
          <Route path="administradores" element={<Administradores />} />
          <Route path="reportes" element={<Reportes />} />
          <Route path="mi-cuenta" element={<MiCuenta />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
