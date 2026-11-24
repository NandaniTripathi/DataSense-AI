import { Download, FileText } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { RequireDataset } from '../components/RequireDataset'
import { btnPrimary, Card, PageHeader } from '../components/ui'
import type { Conversation, DatasetDetail } from '../types'

function Reports({ ds }: { ds: DatasetDetail }) {
  const [convos, setConvos] = useState<Conversation[]>([])
  const [convoId, setConvoId] = useState('')

  useEffect(() => {
    setConvoId('')
    api.conversations(ds.id).then(setConvos).catch(() => setConvos([]))
  }, [ds.id])

  return (
    <div className="mx-auto max-w-3xl p-5 sm:p-8">
      <PageHeader title="Reports" subtitle="Download a shareable analysis of this dataset as a single HTML file. It opens in any browser, keeps its interactive charts, and has a Print / Save as PDF button." />
      <Card className="p-6">
        <div className="flex items-start gap-4">
          <div className="rounded-xl bg-brand-50 p-3 text-brand"><FileText className="h-6 w-6" aria-hidden /></div>
          <div className="flex-1">
            <h2 className="text-lg font-bold text-ink">{ds.name}: analytical report</h2>
            <p className="mt-1 text-sm text-muted">
              Includes headline numbers, key findings, anomalies, interactive charts and a data dictionary.
              If you generated an AI summary on the Dashboard, it is included as well.
            </p>
            <label className="mt-5 block text-sm font-semibold text-slate-700" htmlFor="convo">Also include an analyst conversation</label>
            <select id="convo" value={convoId} onChange={(e) => setConvoId(e.target.value)} className="mt-1.5 w-full rounded-lg border border-line px-3 py-2 text-sm">
              <option value="">None</option>
              {convos.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
            </select>
            {convos.length === 0 && <p className="mt-1 text-xs text-muted">Ask the AI Analyst something first and its answers can be added here.</p>}
            <a className={`${btnPrimary} mt-5`} href={api.reportUrl(ds.id, convoId || undefined)} download>
              <Download className="h-4 w-4" aria-hidden />Download report
            </a>
          </div>
        </div>
      </Card>
      <p className="mt-4 text-xs text-muted">Individual query results can be downloaded as CSV from any answer in the AI Analyst.</p>
    </div>
  )
}

export function ReportsPage() {
  return <RequireDataset>{(ds) => <Reports ds={ds} />}</RequireDataset>
}
