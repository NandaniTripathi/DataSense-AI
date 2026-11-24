import { ArrowDown, ArrowUp } from 'lucide-react'

interface Props {
  columns: string[]
  rows: unknown[][]
  rowLabels?: (number | string)[]
  sortBy?: string
  sortDir?: 'asc' | 'desc'
  onSort?: (col: string) => void
  maxHeight?: string
}

export function formatCell(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return v.toLocaleString('en-US')
    return v.toLocaleString('en-US', { maximumFractionDigits: 4 })
  }
  return String(v)
}

export function DataTable({ columns, rows, rowLabels, sortBy, sortDir, onSort, maxHeight = '28rem' }: Props) {
  return (
    <div className="overflow-auto rounded-lg border border-line" style={{ maxHeight }}>
      <table className="min-w-full border-collapse text-left text-[13px]">
        <thead className="sticky top-0 z-10 bg-slate-50">
          <tr>
            {rowLabels && <th className="border-b border-line px-3 py-2 text-right font-semibold text-muted">#</th>}
            {columns.map((c) => {
              const active = sortBy === c
              return (
                <th key={c} scope="col" aria-sort={active ? (sortDir === 'desc' ? 'descending' : 'ascending') : undefined}
                  className="whitespace-nowrap border-b border-line px-3 py-2 font-semibold text-slate-700">
                  {onSort ? (
                    <button onClick={() => onSort(c)} className="inline-flex items-center gap-1 hover:text-brand">
                      {c}
                      {active && (sortDir === 'desc' ? <ArrowDown className="h-3 w-3" /> : <ArrowUp className="h-3 w-3" />)}
                    </button>
                  ) : c}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="odd:bg-white even:bg-slate-50/50 hover:bg-brand-50/60">
              {rowLabels && <td className="px-3 py-1.5 text-right text-xs text-muted">{rowLabels[i]}</td>}
              {r.map((v, j) => (
                <td key={j} className={`whitespace-nowrap px-3 py-1.5 ${typeof v === 'number' ? 'text-right' : ''}`}>
                  {v === null || v === undefined ? <span className="text-slate-300">-</span> : formatCell(v)}
                </td>
              ))}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={columns.length + (rowLabels ? 1 : 0)} className="px-3 py-8 text-center text-muted">No rows to show.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
