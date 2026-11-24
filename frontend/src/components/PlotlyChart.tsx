import { useEffect, useRef, useState } from 'react'
import type { PlotlyFigure } from '../types'

// Plotly is big (~3.5MB), so it is loaded on demand the first time a chart renders.
export function PlotlyChart({ figure, height }: { figure: PlotlyFigure; height?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const node = ref.current
    if (!node) return
    let disposed = false
    let ro: ResizeObserver | null = null
    import('plotly.js-dist-min')
      .then(({ default: Plotly }) => {
        if (disposed) return
        const layout = { ...figure.layout, autosize: true, ...(height ? { height } : {}) }
        void Plotly.react(node, figure.data, layout, {
          responsive: true,
          displaylogo: false,
          modeBarButtonsToRemove: ['lasso2d', 'select2d'],
        })
        ro = new ResizeObserver(() => {
          // skip hidden/detached charts (e.g. after switching tabs): Plotly throws on those
          if (!node.isConnected || node.offsetParent === null || node.clientWidth === 0) return
          try { Plotly.Plots.resize(node) } catch { /* chart is being torn down */ }
        })
        ro.observe(node)
      })
      .catch(() => setFailed(true))
    return () => {
      disposed = true
      ro?.disconnect()
      import('plotly.js-dist-min').then(({ default: P }) => P.purge(node)).catch(() => undefined)
    }
  }, [figure, height])

  if (failed) return <p className="p-4 text-sm text-muted">The chart could not be loaded.</p>
  return <div ref={ref} className="w-full" style={{ minHeight: height ?? (figure.layout.height as number | undefined) ?? 320 }} />
}
