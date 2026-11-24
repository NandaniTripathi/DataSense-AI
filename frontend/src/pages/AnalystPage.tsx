import { Copy, Download, Plus, Send, Sparkles, Trash2, Wand2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { DataTable } from '../components/DataTable'
import { PlotlyChart } from '../components/PlotlyChart'
import { RequireDataset } from '../components/RequireDataset'
import { btnGhost, ErrorBanner } from '../components/ui'
import { useDatasets } from '../DatasetContext'
import type { Answer, Conversation, DatasetDetail, Message } from '../types'

function starterQuestions(ds: DatasetDetail): string[] {
  const cols = ds.profile.columns
  const num = cols.find((c) => c.kind === 'numeric')
  const cat = cols.find((c) => c.kind === 'categorical')
  const date = cols.find((c) => c.kind === 'datetime')
  const out: string[] = []
  if (num && cat) out.push(`Which ${cat.name.replace(/_/g, ' ')} has the highest total ${num.name.replace(/_/g, ' ')}?`)
  if (num && date) out.push(`How has ${num.name.replace(/_/g, ' ')} changed month over month?`)
  if (num) out.push(`Are there any unusual ${num.name.replace(/_/g, ' ')} values I should check?`)
  if (cat) out.push(`Show the distribution of rows by ${cat.name.replace(/_/g, ' ')}.`)
  out.push('Give me a quick summary of what is in this dataset.')
  return out.slice(0, 4)
}

function AnswerCard({ m, onAsk }: { m: Message; onAsk: (q: string) => void }) {
  const a = m.payload as Answer | null
  const [tab, setTab] = useState<'chart' | 'table' | 'sql'>('chart')
  const [copied, setCopied] = useState(false)
  if (!a) return <div className="prose-answer"><ReactMarkdown>{m.content}</ReactMarkdown></div>
  const hasChart = !!a.chart
  const active = tab === 'chart' && !hasChart ? 'table' : tab
  const retried = a.queries.filter((q) => !q.ok).length

  return (
    <div className="space-y-3">
      <div className="prose-answer text-[15px] text-slate-800"><ReactMarkdown>{a.summary}</ReactMarkdown></div>

      {a.table && (
        <div className="rounded-xl border border-line">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
            <div role="tablist" className="flex gap-1">
              {([['chart', 'Chart'], ['table', `Table (${a.row_count.toLocaleString()}${a.truncated ? '+' : ''})`], ['sql', 'SQL']] as const)
                .filter(([id]) => id !== 'chart' || hasChart)
                .map(([id, label]) => (
                  <button key={id} role="tab" aria-selected={active === id} onClick={() => setTab(id)}
                    className={`rounded-md px-3 py-1 text-sm font-semibold ${active === id ? 'bg-ink text-white' : 'text-slate-600 hover:bg-slate-100'}`}>{label}</button>
                ))}
            </div>
            <a className="inline-flex items-center gap-1.5 text-sm font-semibold text-brand hover:underline" href={api.csvUrl(m.id)} download>
              <Download className="h-3.5 w-3.5" aria-hidden />CSV
            </a>
          </div>
          <div className="p-3">
            {active === 'chart' && a.chart && <PlotlyChart figure={a.chart} />}
            {active === 'table' && <DataTable columns={a.table.columns} rows={a.table.rows} maxHeight="22rem" />}
            {active === 'sql' && (
              <div>
                <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-ink p-3 text-[12.5px] leading-relaxed text-slate-100">{a.sql}</pre>
                <button className="mt-2 inline-flex items-center gap-1.5 text-sm font-semibold text-brand"
                  onClick={() => { void navigator.clipboard?.writeText(a.sql ?? ''); setCopied(true); setTimeout(() => setCopied(false), 1500) }}>
                  <Copy className="h-3.5 w-3.5" aria-hidden />{copied ? 'Copied' : 'Copy SQL'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      <p className="text-xs text-muted">
        {a.queries.length === 0 ? 'No query was needed.' : `Computed from your data with ${a.queries.length} SQL ${a.queries.length === 1 ? 'query' : 'queries'}`}
        {retried > 0 && ` (${retried} corrected automatically)`} · {(a.meta.elapsed_ms / 1000).toFixed(1)}s
      </p>

      {a.follow_ups.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {a.follow_ups.map((q) => (
            <button key={q} onClick={() => onAsk(q)} className="rounded-full border border-brand/30 bg-brand-50 px-3 py-1.5 text-left text-[13px] font-semibold text-brand hover:bg-brand/10">
              {q}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function Analyst({ ds }: { ds: DatasetDetail }) {
  const { health } = useDatasets()
  const location = useLocation()
  const navigate = useNavigate()
  const [convos, setConvos] = useState<Conversation[]>([])
  const [convoId, setConvoId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showList, setShowList] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const starters = useMemo(() => starterQuestions(ds), [ds])

  const loadList = useCallback(() => api.conversations(ds.id).then(setConvos).catch(() => setConvos([])), [ds.id])

  useEffect(() => {
    setConvoId(null); setMessages([]); setError(null)
    void loadList()
  }, [ds.id, loadList])

  useEffect(() => {
    const prefill = (location.state as { prefill?: string } | null)?.prefill
    if (prefill) {
      setInput(prefill)
      navigate(location.pathname, { replace: true, state: null })
    }
  }, [location, navigate])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [messages, busy])

  const send = async (text: string) => {
    const q = text.trim()
    if (!q || busy) return
    setError(null)
    setInput('')
    setBusy(true)
    setMessages((m) => [...m, { id: -Date.now(), role: 'user', content: q, payload: null, created_at: new Date().toISOString() }])
    try {
      const res = await api.chat({ dataset_id: ds.id, message: q, conversation_id: convoId ?? undefined })
      setConvoId(res.conversation_id)
      setMessages((m) => [...m.filter((x) => x.id >= 0), res.user_message, res.assistant_message])
      void loadList()
    } catch (e) {
      setMessages((m) => m.slice(0, -1))
      setInput(q)
      setError(e instanceof Error ? e.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  const open = async (id: string) => {
    setError(null); setShowList(false)
    try {
      const c = await api.conversation(id)
      setConvoId(c.id); setMessages(c.messages)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not open that conversation.') }
  }
  const remove = async (id: string) => {
    if (!window.confirm('Delete this conversation?')) return
    await api.deleteConversation(id).catch(() => undefined)
    if (id === convoId) { setConvoId(null); setMessages([]) }
    void loadList()
  }
  const newChat = () => { setConvoId(null); setMessages([]); setError(null); setShowList(false) }
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(input) }
  }
  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant')

  return (
    <div className="flex h-full">
      <aside className={`${showList ? 'absolute inset-y-0 right-0 z-30 flex' : 'hidden'} w-72 shrink-0 flex-col border-r border-line bg-white p-3 lg:static lg:flex`}>
        <button className={`${btnGhost} w-full`} onClick={newChat}><Plus className="h-4 w-4" aria-hidden />New conversation</button>
        <h2 className="mb-1 mt-4 px-1 text-xs font-semibold text-muted">History</h2>
        <ul className="-mx-1 flex-1 space-y-0.5 overflow-y-auto">
          {convos.length === 0 && <li className="px-2 py-2 text-sm text-muted">No conversations yet.</li>}
          {convos.map((c) => (
            <li key={c.id} className={`group flex items-center rounded-lg ${c.id === convoId ? 'bg-brand-50' : 'hover:bg-slate-50'}`}>
              <button onClick={() => void open(c.id)} className="min-w-0 flex-1 truncate px-2 py-2 text-left text-sm font-medium text-slate-700">{c.title}</button>
              <button onClick={() => void remove(c.id)} aria-label={`Delete conversation ${c.title}`} className="px-2 text-slate-400 hover:text-danger"><Trash2 className="h-3.5 w-3.5" /></button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center justify-between border-b border-line bg-white px-5 py-3">
          <div className="min-w-0">
            <h1 className="truncate text-base font-extrabold text-ink">AI Analyst</h1>
            <p className="truncate text-xs text-muted">Asking about {ds.name}</p>
          </div>
          <button className={`${btnGhost} lg:hidden`} onClick={() => setShowList((s) => !s)}>History</button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-8" aria-live="polite">
          <div className="mx-auto max-w-3xl space-y-6">
            {health?.llm_configured === false && (
              <ErrorBanner message="The Groq API key is not set. Add GROQ_API_KEY to backend/.env and restart the backend to enable the AI Analyst." />
            )}
            {messages.length === 0 && (
              <div className="py-10 text-center">
                <Wand2 className="mx-auto h-9 w-9 text-brand" aria-hidden />
                <h2 className="mt-3 text-xl font-extrabold text-ink">Ask anything about {ds.name}</h2>
                <p className="mx-auto mt-1 max-w-md text-sm text-muted">Answers come from real SQL run on your data. Every result shows the query behind it.</p>
                <div className="mt-6 flex flex-wrap justify-center gap-2">
                  {starters.map((q) => (
                    <button key={q} onClick={() => void send(q)} disabled={busy} className="rounded-xl border border-line bg-white px-3.5 py-2 text-left text-sm font-medium text-slate-700 hover:border-brand hover:text-brand">{q}</button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m) =>
              m.role === 'user' ? (
                <div key={m.id} className="flex justify-end">
                  <div className="max-w-[85%] rounded-2xl rounded-br-md bg-ink px-4 py-2.5 text-sm text-white">{m.content}</div>
                </div>
              ) : (
                <div key={m.id} className="flex gap-3">
                  <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand text-white"><Sparkles className="h-4 w-4" aria-hidden /></div>
                  <div className="min-w-0 flex-1 rounded-2xl rounded-tl-md border border-line bg-white p-4">
                    <AnswerCard m={m} onAsk={(q) => void send(q)} />
                  </div>
                </div>
              ),
            )}
            {busy && (
              <div className="flex items-center gap-3 text-sm text-muted" role="status">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand/10"><span className="flex gap-1"><i className="thinking-dot h-1.5 w-1.5 rounded-full bg-brand" /><i className="thinking-dot h-1.5 w-1.5 rounded-full bg-brand" /><i className="thinking-dot h-1.5 w-1.5 rounded-full bg-brand" /></span></div>
                Writing queries and checking the results...
              </div>
            )}
            {error && <ErrorBanner message={error} />}
            <div ref={bottomRef} />
          </div>
        </div>

        <div className="border-t border-line bg-white p-3 sm:p-4">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <label className="flex-1">
              <span className="sr-only">Your question</span>
              <textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={onKey} rows={1} maxLength={2000}
                placeholder={lastAssistant ? 'Ask a follow-up...' : 'Ask a question about your data...'}
                className="max-h-40 w-full resize-none rounded-xl border border-line px-4 py-3 text-sm" />
            </label>
            <button onClick={() => void send(input)} disabled={busy || !input.trim()} aria-label="Send question"
              className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand text-white hover:bg-brand-600 disabled:opacity-40">
              <Send className="h-4 w-4" aria-hidden />
            </button>
          </div>
          <p className="mx-auto mt-1.5 max-w-3xl text-center text-xs text-muted">Enter to send, Shift+Enter for a new line. Check important numbers against the SQL shown.</p>
        </div>
      </section>
    </div>
  )
}

export function AnalystPage() {
  return <RequireDataset>{(ds) => <Analyst ds={ds} />}</RequireDataset>
}
