import { AlertTriangle, CheckCircle2, Info, MessageSquareText, RefreshCw, Sparkles } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { DataTable, formatCell } from '../components/DataTable'
import { PlotlyChart } from '../components/PlotlyChart'
import { RequireDataset } from '../components/RequireDataset'
import { btnGhost, btnPrimary, Card, ErrorBanner, PageHeader, Spinner } from '../components/ui'
import { useDatasets } from '../DatasetContext'
import type { DatasetDetail, Finding, Insights } from '../types'

const SEVERITY = {
  warning: { icon: AlertTriangle, bar: 'border-l-signal', color: 'text-amber-600' },
  positive: { icon: CheckCircle2, bar: 'border-l-teal', color: 'text-teal-600' },
  info: { icon: Info, bar: 'border-l-brand', color: 'text-brand' },
} as const

function FindingCard({ f }: { f: Finding }) {
  const s = SEVERITY[f.severity] ?? SEVERITY.info
  const Icon = s.icon
  return (
    <div className={`rounded-xl border border-line border-l-4 bg-white p-3.5 ${s.bar}`}>
      <div className="flex items-start gap-2.5">
        <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${s.color}`} aria-hidden />
        <div>
          <h3 className="text-sm font-bold text-ink">{f.title}</h3>
          <p className="mt-0.5 text-[13px] leading-relaxed text-slate-600">{f.detail}</p>
        </div>
      </div>
    </div>
  )
}

function AnomalyPanel({ ins, ask }: { ins: Insights; ask: (q: string) => void }) {
  const a = ins.anomalies
  const [tab, setTab] = useState<'time' | 'values' | 'rows'>('time')
  const metric = ins.primary_metric ?? 'the main metric'
  const tabs = [
    { id: 'time' as const, label: 'Unusual periods', n: a.time_series.length },
    { id: 'values' as const, label: 'Extreme values', n: a.outliers.length },
    { id: 'rows' as const, label: 'Unusual rows', n: a.multivariate.length },
  ]
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-bold text-ink">Anomalies</h2>
          <p className="mt-0.5 text-sm text-muted">{a.total ? a.headline : 'Nothing statistically unusual was found.'}</p>
        </div>
        {a.total > 0 && (
          <button className={btnGhost} onClick={() => ask(`Investigate the anomalies in ${metric}. What is driving the biggest ones?`)}>
            <MessageSquareText className="h-4 w-4" aria-hidden />Ask the analyst why
          </button>
        )}
      </div>
      {a.total > 0 && (
        <>
          <div role="tablist" className="mb-3 mt-4 flex flex-wrap gap-1">
            {tabs.map((t) => (
              <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
                className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${tab === t.id ? 'bg-ink text-white' : 'bg-slate-100 text-slate-600'}`}>
                {t.label} ({t.n})
              </button>
            ))}
          </div>
          {tab === 'time' && (
            <DataTable columns={['period', 'type', 'actual', 'expected', 'robust z']}
              rows={a.time_series.map((r) => [r.period, r.direction, r.value, r.expected, r.z_score])} maxHeight="18rem" />
          )}
          {tab === 'values' && (
            <DataTable columns={['row', 'column', 'value', 'typical (median)', 'robust z']}
              rows={a.outliers.map((r) => [r.row, r.column, r.value, r.typical, r.z_score])} maxHeight="18rem" />
          )}
          {tab === 'rows' && a.multivariate.length > 0 && (
            <DataTable columns={['row', 'score', ...Object.keys(a.multivariate[0].values)]}
              rows={a.multivariate.map((r) => [r.row, r.score, ...Object.values(r.values)])} maxHeight="18rem" />
          )}
          <p className="mt-2 text-xs text-muted">
            Row numbers match the # column in the Explorer. Methods: robust z-score on weekly/monthly totals, MAD outliers per column, Isolation Forest across numeric columns.
          </p>
        </>
      )}
    </Card>
  )
}

function Dashboard({ ds }: { ds: DatasetDetail }) {
  const navigate = useNavigate()
  const { health } = useDatasets()
  const [ins, setIns] = useState<Insights | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [aiBusy, setAiBusy] = useState(false)
  const [aiError, setAiError] = useState<string | null>(null)

  const load = useCallback((refresh = false) => {
    setIns(null)
    setError(null)
    api.insights(ds.id, refresh).then(setIns).catch((e: Error) => setError(e.message))
  }, [ds.id])

  useEffect(() => { load() }, [load])

  const generate = async () => {
    setAiBusy(true)
    setAiError(null)
    try {
      const { ai_summary } = await api.aiSummary(ds.id)
      setIns((cur) => (cur ? { ...cur, ai_summary } : cur))
    } catch (e) {
      setAiError(e instanceof Error ? e.message : 'Could not generate the summary.')
    } finally {
      setAiBusy(false)
    }
  }
  const ask = (q: string) => navigate('/analyst', { state: { prefill: q } })

  return (
    <div className="mx-auto max-w-7xl p-5 sm:p-8">
      <PageHeader
        title={ds.name}
        subtitle={`Automated analysis of ${ds.n_rows.toLocaleString()} rows and ${ds.n_cols} columns.`}
        actions={<button className={btnGhost} onClick={() => load(true)}><RefreshCw className="h-4 w-4" aria-hidden />Re-run analysis</button>}
      />
      {error && <ErrorBanner message={error} onRetry={() => load()} />}
      {!ins && !error && <Spinner label="Analysing your data..." />}
      {ins && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
            {ins.kpis.map((k) => (
              <Card key={k.label} className="p-4">
                <p className="text-xs font-semibold text-muted">{k.label}</p>
                <p className="mt-1 text-2xl font-extrabold tracking-tight text-ink">{k.value}</p>
              </Card>
            ))}
          </div>

          <Card className="p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="flex items-center gap-2 text-base font-bold text-ink"><Sparkles className="h-4 w-4 text-brand" aria-hidden />AI executive summary</h2>
              <button className={btnPrimary} onClick={generate} disabled={aiBusy || health?.llm_configured === false}>
                {aiBusy ? 'Writing...' : ins.ai_summary ? 'Regenerate' : 'Generate with AI'}
              </button>
            </div>
            {ins.ai_summary ? (
              <div className="prose-answer mt-3 text-sm text-slate-700"><ReactMarkdown>{ins.ai_summary}</ReactMarkdown></div>
            ) : (
              <p className="mt-2 text-sm text-muted">
                {health?.llm_configured === false
                  ? 'Add your Groq API key (GROQ_API_KEY) to backend/.env to enable written summaries. Everything else on this page works without it.'
                  : 'The AI reads the statistics below (not your raw rows) and writes a short briefing for decision makers.'}
              </p>
            )}
            {aiError && <div className="mt-3"><ErrorBanner message={aiError} /></div>}
          </Card>

          {ins.charts.filter((c) => c.id === 'trend').map((c) => (
            <Card key={c.id} className="p-5">
              <h2 className="mb-1 text-base font-bold text-ink">{c.title}</h2>
              <PlotlyChart figure={c.figure} />
              {ins.anomalies.time_series.length > 0 && <p className="text-xs text-muted">Diamonds mark periods that deviate sharply from the surrounding trend.</p>}
            </Card>
          ))}

          <div className="grid gap-6 xl:grid-cols-5">
            <div className="xl:col-span-3"><AnomalyPanel ins={ins} ask={ask} /></div>
            <div className="space-y-3 xl:col-span-2">
              <h2 className="text-base font-bold text-ink">Key findings</h2>
              {ins.findings.map((f, i) => <FindingCard key={i} f={f} />)}
            </div>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            {ins.charts.filter((c) => c.id !== 'trend').map((c) => (
              <Card key={c.id} className="p-5">
                <h2 className="mb-1 text-base font-bold text-ink">{c.title}</h2>
                <PlotlyChart figure={c.figure} />
              </Card>
            ))}
          </div>
          <p className="text-xs text-muted">Totals use {formatCell(ds.n_rows)} rows. Statistical findings are leads to investigate, not conclusions.</p>
        </div>
      )}
    </div>
  )
}

export function DashboardPage() {
  return <RequireDataset>{(ds) => <Dashboard ds={ds} />}</RequireDataset>
}
