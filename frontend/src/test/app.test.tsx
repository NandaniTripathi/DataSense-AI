import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import App from '../App'
import { DataTable } from '../components/DataTable'
import { DatasetProvider } from '../DatasetContext'

function mockFetch(routes: Record<string, unknown>) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const key = Object.keys(routes).find((k) => String(url).endsWith(k))
    if (!key) return new Response(JSON.stringify({ detail: 'not found' }), { status: 404 })
    return new Response(JSON.stringify(routes[key]), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
}

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear() })

describe('DataTable', () => {
  it('formats numbers, shows nulls and reports sort clicks', async () => {
    const onSort = vi.fn()
    render(<DataTable columns={['region', 'revenue']} rows={[['North', 1234567.891], ['South', null]]} onSort={onSort} />)
    expect(screen.getByText('1,234,567.891')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'revenue' }))
    expect(onSort).toHaveBeenCalledWith('revenue')
    expect(screen.getAllByText('-').length).toBeGreaterThan(0)
  })
})

describe('API failures', () => {
  it('surfaces the backend message when the AI request fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ detail: 'Groq is rate limiting requests right now.' }), { status: 429 })))
    await expect(api.chat({ dataset_id: 'd', message: 'hi' })).rejects.toMatchObject({
      message: 'Groq is rate limiting requests right now.', status: 429,
    })
  })

  it('falls back to a generic message for a non-JSON error body', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>Bad gateway</html>', { status: 502 })))
    await expect(api.chat({ dataset_id: 'd', message: 'hi' })).rejects.toMatchObject({
      message: 'Request failed (502)', status: 502,
    })
  })

  it('explains when the backend cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    await expect(api.chat({ dataset_id: 'd', message: 'hi' })).rejects.toMatchObject({
      message: expect.stringContaining('Cannot reach the DataSense backend'), status: 0,
    })
  })
})

describe('App', () => {
  it('sends a first-time visitor to the upload page', async () => {
    mockFetch({ '/datasets': [], '/health': { status: 'ok', llm_configured: false, model: 'm' } })
    render(<MemoryRouter initialEntries={['/']}><DatasetProvider><App /></DatasetProvider></MemoryRouter>)
    expect(await screen.findByText('Drop a CSV or Excel file here')).toBeInTheDocument()
    expect(screen.getByText('Groq key missing')).toBeInTheDocument()
  })

  it('guards analyst page when no dataset exists', async () => {
    mockFetch({ '/datasets': [], '/health': { status: 'ok', llm_configured: true, model: 'm' } })
    render(<MemoryRouter initialEntries={['/analyst']}><DatasetProvider><App /></DatasetProvider></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('No dataset selected')).toBeInTheDocument())
  })
})
