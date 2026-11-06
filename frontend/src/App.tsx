import { Routes, Route, Navigate } from 'react-router-dom'
import Login from './pages/Login'
import Search from './pages/Search'
import Cv from './pages/Cv'
import Preferences from './pages/Preferences'
import PrivateRoute from './components/PrivateRoute'
import AppLayout from './components/AppLayout'

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/search" element={<PrivateRoute><AppLayout><Search /></AppLayout></PrivateRoute>} />
      <Route path="/cv" element={<PrivateRoute><AppLayout><Cv /></AppLayout></PrivateRoute>} />
      <Route path="/preferences" element={<PrivateRoute><AppLayout><Preferences /></AppLayout></PrivateRoute>} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  )
}
