import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useDatasets } from '../DatasetContext'
import type { DatasetDetail } from '../types'
import { btnPrimary, EmptyState, Spinner } from './ui'

/** Renders children only when a dataset is selected; otherwise guides the user to upload one. */
export function RequireDataset({ children }: { children: (dataset: DatasetDetail) => ReactNode }) {
  const { active, loading } = useDatasets()
  if (loading) return <div className="p-8"><Spinner /></div>
  if (!active) {
    return (
      <EmptyState
        title="No dataset selected"
        body="Upload a CSV or Excel file, or load the demo dataset, to start exploring."
        action={<Link to="/datasets" className={btnPrimary}>Go to Datasets</Link>}
      />
    )
  }
  return <>{children(active)}</>
}
