import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from './api'
import type { Dataset, DatasetDetail, Health } from './types'

interface Ctx {
  datasets: Dataset[]
  active: DatasetDetail | null
  health: Health | null
  loading: boolean
  select: (id: string | null) => Promise<void>
  refresh: () => Promise<void>
  added: (d: DatasetDetail) => void
  removed: (id: string) => Promise<void>
}

const DatasetCtx = createContext<Ctx | null>(null)
const KEY = 'datasense.activeDataset'

export function DatasetProvider({ children }: { children: ReactNode }) {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [active, setActive] = useState<DatasetDetail | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [loading, setLoading] = useState(true)

  const select = useCallback(async (id: string | null) => {
    if (!id) {
      setActive(null)
      localStorage.removeItem(KEY)
      return
    }
    try {
      setActive(await api.getDataset(id))
      localStorage.setItem(KEY, id)
    } catch {
      setActive(null)
      localStorage.removeItem(KEY)
    }
  }, [])

  const refresh = useCallback(async () => {
    const list = await api.listDatasets()
    setDatasets(list)
    return list
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [list, h] = await Promise.all([api.listDatasets(), api.health().catch(() => null)])
        if (cancelled) return
        setDatasets(list)
        setHealth(h)
        const saved = localStorage.getItem(KEY)
        const target = list.find((d) => d.id === saved)?.id ?? list[0]?.id ?? null
        if (target) await select(target)
      } catch {
        /* backend offline: pages show their own error state */
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [select])

  const added = useCallback((d: DatasetDetail) => {
    setDatasets((prev) => [d, ...prev.filter((x) => x.id !== d.id)])
    setActive(d)
    localStorage.setItem(KEY, d.id)
  }, [])

  const removed = useCallback(async (id: string) => {
    await api.deleteDataset(id)
    const list = await refresh()
    if (active?.id === id) await select(list[0]?.id ?? null)
  }, [active, refresh, select])

  const value = useMemo(
    () => ({ datasets, active, health, loading, select, refresh: async () => { await refresh() }, added, removed }),
    [datasets, active, health, loading, select, refresh, added, removed],
  )
  return <DatasetCtx.Provider value={value}>{children}</DatasetCtx.Provider>
}

export function useDatasets() {
  const ctx = useContext(DatasetCtx)
  if (!ctx) throw new Error('useDatasets must be used inside DatasetProvider')
  return ctx
}
