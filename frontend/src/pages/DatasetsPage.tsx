import { FlaskConical, Trash2, UploadCloud } from 'lucide-react'
import { useCallback, useRef, useState, type DragEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { btnGhost, btnPrimary, Card, ErrorBanner, formatBytes, PageHeader, Spinner } from '../components/ui'
import { useDatasets } from '../DatasetContext'

const MAX_MB = 25

export function DatasetsPage() {
  const { datasets, active, select, added, removed } = useDatasets()
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)

  const upload = useCallback(async (file: File | undefined) => {
    if (!file) return
    setError(null)
    if (file.size > MAX_MB * 1024 * 1024) {
      setError(`That file is ${(file.size / 1024 / 1024).toFixed(1)} MB. The limit is ${MAX_MB} MB.`)
      return
    }
    setBusy(`Reading ${file.name} and profiling every column...`)
    try {
      const ds = await api.uploadDataset(file)
      added(ds)
      navigate('/dashboard')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed.')
    } finally {
      setBusy(null)
      if (inputRef.current) inputRef.current.value = ''
    }
  }, [added, navigate])

  const loadSample = async () => {
    setError(null)
    setBusy('Generating the retail sales demo dataset...')
    try {
      added(await api.createSample())
      navigate('/dashboard')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not create the demo dataset.')
    } finally {
      setBusy(null)
    }
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    void upload(e.dataTransfer.files[0])
  }

  const remove = async (id: string, name: string) => {
    if (!window.confirm(`Delete "${name}" and all its conversations? This cannot be undone.`)) return
    try { await removed(id) } catch (e) { setError(e instanceof Error ? e.message : 'Delete failed.') }
  }

  return (
    <div className="mx-auto max-w-5xl p-5 sm:p-8">
      <PageHeader title="Datasets" subtitle="Upload a spreadsheet and DataSense profiles it, finds anomalies and lets you question it in plain English." />
      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`rounded-2xl border-2 border-dashed p-8 text-center transition-colors sm:p-12 ${
          dragging ? 'border-brand bg-brand-50' : 'border-slate-300 bg-white'}`}
      >
        {busy ? (
          <div className="flex justify-center"><Spinner label={busy} /></div>
        ) : (
          <>
            <UploadCloud className="mx-auto h-10 w-10 text-brand" aria-hidden />
            <h2 className="mt-3 text-lg font-bold text-ink">Drop a CSV or Excel file here</h2>
            <p className="mt-1 text-sm text-muted">.csv, .tsv, .xlsx or .xls, up to {MAX_MB} MB. The first sheet of an Excel file is used.</p>
            <div className="mt-5 flex flex-wrap justify-center gap-3">
              <button className={btnPrimary} onClick={() => inputRef.current?.click()}>Choose a file</button>
              <button className={btnGhost} onClick={loadSample}><FlaskConical className="h-4 w-4" aria-hidden />Try the demo dataset</button>
            </div>
            <input ref={inputRef} type="file" hidden accept=".csv,.tsv,.txt,.xlsx,.xlsm,.xls" aria-label="Upload dataset"
              onChange={(e) => void upload(e.target.files?.[0])} />
          </>
        )}
      </div>

      <h2 className="mb-3 mt-10 text-base font-bold text-ink">Your datasets</h2>
      {datasets.length === 0 ? (
        <p className="text-sm text-muted">Nothing uploaded yet.</p>
      ) : (
        <Card className="divide-y divide-line">
          {datasets.map((d) => (
            <div key={d.id} className="flex flex-wrap items-center gap-3 p-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-bold text-ink">{d.name}</span>
                  {active?.id === d.id && <span className="rounded-full bg-brand-50 px-2 py-0.5 text-xs font-semibold text-brand">Active</span>}
                </div>
                <p className="truncate text-xs text-muted">
                  {d.filename} · {d.n_rows.toLocaleString()} rows · {d.n_cols} columns · {formatBytes(d.size_bytes)} · added {new Date(d.created_at).toLocaleDateString()}
                </p>
              </div>
              <button className={btnGhost} onClick={async () => { await select(d.id); navigate('/dashboard') }}>Open</button>
              <button className={`${btnGhost} text-danger`} onClick={() => void remove(d.id, d.name)} aria-label={`Delete ${d.name}`}>
                <Trash2 className="h-4 w-4" aria-hidden />
              </button>
            </div>
          ))}
        </Card>
      )}
    </div>
  )
}
