export interface Dataset {
  id: string
  name: string
  filename: string
  n_rows: number
  n_cols: number
  size_bytes: number
  created_at: string
}

export interface ColumnProfile {
  name: string
  sql_type: string
  kind: 'numeric' | 'categorical' | 'datetime' | 'text' | 'boolean' | 'id'
  null_count: number
  null_pct: number
  unique: number
  samples: unknown[]
  stats?: Record<string, number | string | null>
  top_values?: { value: unknown; count: number }[]
}

export interface Profile {
  table: string
  n_rows: number
  n_cols: number
  duplicate_rows: number
  missing_cells: number
  missing_pct: number
  quality_score: number
  columns: ColumnProfile[]
}

export interface DatasetDetail extends Dataset {
  profile: Profile
}

export interface PlotlyFigure {
  data: unknown[]
  layout: Record<string, unknown>
}

export interface Finding {
  category: string
  severity: 'info' | 'warning' | 'positive'
  title: string
  detail: string
}

export interface Anomalies {
  total: number
  headline: string
  time_series: { period: string; value: number; expected: number; z_score: number; direction: 'spike' | 'drop' }[]
  outliers: { row: number; column: string; value: number; typical: number; z_score: number }[]
  multivariate: { row: number; score: number; values: Record<string, number | string | null> }[]
}

export interface Insights {
  primary_metric: string | null
  kpis: { label: string; value: string }[]
  findings: Finding[]
  anomalies: Anomalies
  charts: { id: string; title: string; figure: PlotlyFigure }[]
  ai_summary: string | null
}

export interface QueryTrace {
  id: number
  purpose: string
  sql: string
  ok: boolean
  error: string | null
  row_count: number
}

export interface Answer {
  summary: string
  sql: string | null
  table: { columns: string[]; rows: unknown[][] } | null
  row_count: number
  truncated: boolean
  chart: PlotlyFigure | null
  follow_ups: string[]
  queries: QueryTrace[]
  meta: { model: string; elapsed_ms: number; usage: Record<string, number> }
}

export interface Message {
  id: number
  role: 'user' | 'assistant'
  content: string
  payload: Answer | null
  created_at: string
}

export interface Conversation {
  id: string
  dataset_id: string
  title: string
  created_at: string
}

export interface ConversationDetail extends Conversation {
  messages: Message[]
}

export interface PreviewPage {
  columns: string[]
  row_ids: number[]
  rows: unknown[][]
  total: number
  page: number
  page_size: number
}

export interface Health {
  status: string
  llm_configured: boolean
  model: string
}
