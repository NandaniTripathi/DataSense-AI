import { Database, FileText, LayoutDashboard, Menu, Sparkles, Table2, X } from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useDatasets } from '../DatasetContext'

const NAV = [
  { to: '/datasets', label: 'Datasets', icon: Database },
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/explorer', label: 'Explorer', icon: Table2 },
  { to: '/analyst', label: 'AI Analyst', icon: Sparkles },
  { to: '/reports', label: 'Reports', icon: FileText },
]

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden>
        <rect width="32" height="32" rx="8" fill="#172236" />
        <rect x="7" y="17" width="4" height="8" rx="1.5" fill="#4F6BED" />
        <rect x="14" y="11" width="4" height="14" rx="1.5" fill="#14B8A6" />
        <rect x="21" y="6" width="4" height="19" rx="1.5" fill="#F59E0B" />
      </svg>
      <span className="text-[17px] font-extrabold tracking-tight text-white">DataSense AI</span>
    </div>
  )
}

function SidebarBody({ onNavigate }: { onNavigate?: () => void }) {
  const { datasets, active, select, health } = useDatasets()
  return (
    <div className="flex h-full flex-col gap-6 p-5">
      <Logo />
      <div>
        <label htmlFor="dataset-switch" className="mb-1.5 block text-xs font-semibold text-slate-400">Active dataset</label>
        <select
          id="dataset-switch"
          value={active?.id ?? ''}
          onChange={(e) => void select(e.target.value || null)}
          disabled={datasets.length === 0}
          className="w-full rounded-lg border border-white/10 bg-ink-2 px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
        >
          {datasets.length === 0 && <option value="">No datasets yet</option>}
          {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
        {active && (
          <p className="mt-1.5 text-xs text-slate-400">{active.n_rows.toLocaleString()} rows · {active.n_cols} columns</p>
        )}
      </div>
      <nav className="flex flex-col gap-1" aria-label="Main">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} onClick={onNavigate}
            className={({ isActive }) =>
              `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-semibold transition-colors ${
                isActive ? 'bg-brand text-white' : 'text-slate-300 hover:bg-white/5 hover:text-white'}`}>
            <Icon className="h-[18px] w-[18px]" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto rounded-lg bg-ink-2 p-3 text-xs text-slate-400">
        <div className="flex items-center gap-2 font-semibold text-slate-200">
          <span className={`h-2 w-2 rounded-full ${health?.llm_configured ? 'bg-teal' : 'bg-signal'}`} />
          {health === null ? 'Backend offline' : health.llm_configured ? 'Groq connected' : 'Groq key missing'}
        </div>
        {health && <p className="mt-1">{health.llm_configured ? health.model : 'Add GROQ_API_KEY to backend/.env'}</p>}
      </div>
    </div>
  )
}

export function Layout() {
  const [open, setOpen] = useState(false)
  return (
    <div className="flex h-full">
      <aside className="hidden w-64 shrink-0 bg-ink lg:block"><SidebarBody /></aside>

      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-72 bg-ink shadow-2xl">
            <button onClick={() => setOpen(false)} aria-label="Close menu" className="absolute right-3 top-4 text-slate-300"><X /></button>
            <SidebarBody onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center justify-between bg-ink px-4 py-3 lg:hidden">
          <Logo />
          <button onClick={() => setOpen(true)} aria-label="Open menu" className="text-slate-200"><Menu /></button>
        </div>
        <main className="min-h-0 flex-1 overflow-y-auto"><Outlet /></main>
      </div>
    </div>
  )
}
