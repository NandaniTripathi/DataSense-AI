import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { useDatasets } from './DatasetContext'
import { AnalystPage } from './pages/AnalystPage'
import { DashboardPage } from './pages/DashboardPage'
import { DatasetsPage } from './pages/DatasetsPage'
import { ExplorerPage } from './pages/ExplorerPage'
import { ReportsPage } from './pages/ReportsPage'

function Home() {
  const { active, loading } = useDatasets()
  if (loading) return null
  return <Navigate to={active ? '/dashboard' : '/datasets'} replace />
}

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="/datasets" element={<DatasetsPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/explorer" element={<ExplorerPage />} />
        <Route path="/analyst" element={<AnalystPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
