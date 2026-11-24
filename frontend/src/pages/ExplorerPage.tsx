import { ChevronLeft, ChevronRight, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { DataTable } from '../components/DataTable'
import { RequireDataset } from '../components/RequireDataset'
import { btnGhost, Card, ErrorBanner, PageHeader, Spinner } from '../components/ui'
import type { ColumnProfile, DatasetDetail, PreviewPage } from '../types'

const KIND_STYLE: Record<string, string> = {
  numeric: 'bg-blue-50 text-blue-700', categorical: 'bg-violet-50 text-violet-700',
  datetime: 'bg-amber-50 text-amber-700', text: 'bg-slate-100 text-slate-600',
  boolean: 'bg-teal-50 text-teal-700', id: 'bg-slate-100 text-slate-600',
}

function ColumnCard({ c, rows }: { c: ColumnProfile; rows: number }) {
  const st = c.stats
  const fmt = (v: unknown) => (typeof v === 'number' ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : String(v ?? '-'))
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-2">
        <h3 className="break-all text-sm font-bold text-ink">{c.name}</h3>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold ${KIND_STYLE[c.kind] ?? ''}`}>{c.kind}</span>
      </div>
      <p className="mt-1 text-xs text-muted">{c.unique.toLocaleString()} unique · {c.null_pct}% missing</p>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100" title={`${c.null_pct}% missing`}>
        <div className="h-full bg-signal" style={{ width: `${Math.min(100, c.null_pct)}%` }} />
      </div>
      {c.kind === 'numeric' && st && (
        <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
          {(['min', 'median', 'mean', 'max', 'std'] as const).map((k) => (
            <div key={k}><dt className="text-muted">{k}</dt><dd className="font-semibold">{fmt(st[k])}</dd></div>
          ))}
        </dl>
      )}
      {c.kind === 'datetime' && st && <p className="mt-3 text-xs">{String(st.min).slice(0, 10)} to {String(st.max).slice(0, 10)}</p>}
      {c.top_values && c.kind !== 'text' && (
        <ul className="mt-3 space-y-1.5">
          {c.top_values.slice(0, 5).map((t) => (
            <li key={String(t.value)} className="text-xs">
              <div className="flex justify-between gap-2"><span className="truncate">{String(t.value)}</span><span className="text-muted">{t.count.toLocaleString()}</span></div>
              <div className="h-1 rounded bg-slate-100"><div className="h-full rounded bg-brand" style={{ width: `${(100 * t.count) / rows}%` }} /></div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function Explorer({ ds }: { ds: DatasetDetail }) {
  const [tab, setTab] = useState<'data' | 'columns'>('data')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [sortBy, setSortBy] = useState<string | undefined>()
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [data, setData] = useState<PreviewPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setPage(1) }, 350)
    return () => clearTimeout(t)
  }, [searchInput])

  useEffect(() => { setPage(1); setSortBy(undefined); setSearchInput(''); setSearch('') }, [ds.id])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.preview(ds.id, { page, pageSize, sortBy, sortDir, search })
      .then((d) => { if (!cancelled) { setData(d); setError(null) } })
      .catch((e: Error) => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [ds.id, page, pageSize, sortBy, sortDir, search])

  const onSort = (col: string) => {
    if (sortBy === col) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortBy(col); setSortDir('asc') }
    setPage(1)
  }
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1

  return (
    <div className="mx-auto max-w-7xl p-5 sm:p-8">
      <PageHeader title="Dataset explorer" subtitle={`${ds.name}: browse raw rows or inspect how each column was profiled.`} />
      <div role="tablist" className="mb-4 inline-flex rounded-lg border border-line bg-white p-1">
        {(['data', 'columns'] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={`rounded-md px-4 py-1.5 text-sm font-semibold ${tab === t ? 'bg-ink text-white' : 'text-slate-600'}`}>
            {t === 'data' ? 'Rows' : `Columns (${ds.profile.n_cols})`}
          </button>
        ))}
      </div>

      {tab === 'columns' ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {ds.profile.columns.map((c) => <ColumnCard key={c.name} c={c} rows={ds.profile.n_rows} />)}
        </div>
      ) : (
        <Card className="p-4">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <label className="relative min-w-[14rem] flex-1">
              <span className="sr-only">Search rows</span>
              <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted" aria-hidden />
              <input value={searchInput} onChange={(e) => setSearchInput(e.target.value)} placeholder="Search any column..."
                className="w-full rounded-lg border border-line py-2 pl-9 pr-3 text-sm" />
            </label>
            <label className="flex items-center gap-2 text-sm text-muted">Rows per page
              <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1) }} className="rounded-lg border border-line px-2 py-1.5 text-slate-700">
                {[25, 50, 100].map((n) => <option key={n}>{n}</option>)}
              </select>
            </label>
            {loading && <Spinner />}
          </div>
          {error && <ErrorBanner message={error} />}
          {data && (
            <>
              <DataTable columns={data.columns} rows={data.rows} rowLabels={data.row_ids} sortBy={sortBy} sortDir={sortDir} onSort={onSort} maxHeight="60vh" />
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm text-muted">
                <span>{data.total.toLocaleString()} {search ? 'matching ' : ''}rows</span>
                <div className="flex items-center gap-2">
                  <button className={btnGhost} disabled={page <= 1} onClick={() => setPage((p) => p - 1)} aria-label="Previous page"><ChevronLeft className="h-4 w-4" /></button>
                  <span>Page {page} of {pages.toLocaleString()}</span>
                  <button className={btnGhost} disabled={page >= pages} onClick={() => setPage((p) => p + 1)} aria-label="Next page"><ChevronRight className="h-4 w-4" /></button>
                </div>
              </div>
            </>
          )}
        </Card>
      )}
    </div>
  )
}

export function ExplorerPage() {
  return <RequireDataset>{(ds) => <Explorer ds={ds} />}</RequireDataset>
}
