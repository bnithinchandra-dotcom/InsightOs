import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'

const project = { id: 41, name: 'Disposable fixture project' }
const dataset = { id: 72, project_id: project.id, name: 'Disposable fixture dataset' }
const file = {
  id: 93,
  dataset_id: dataset.id,
  filename: 'orders.csv',
  status: 'Ready',
  normalization_status: 'Ready',
  normalization_result: {
    tables: [
      {
        table_index: 0,
        table_name: 'orders',
        row_count: 3,
        column_count: 2,
        schema: [
          { name: 'order_id', type: 'int64' },
          { name: 'amount', type: 'double' },
        ],
      },
    ],
  },
}

function completedAnalysis(id = 501, stale = false) {
  return {
    id,
    dataset_id: dataset.id,
    status: stale ? 'Stale' : 'Completed',
    stale,
    configuration: { business_context: 'Orders are customer purchases.' },
    result: {
      schema: {
        rows_scanned: 3,
        tables: [{
          identity: { file_id: file.id, table_index: 0, table_name: 'orders' },
          row_count: 3,
          column_count: 2,
          columns: [{
            name: 'amount',
            position: 1,
            normalized_type: 'double',
            null_rate: 0,
            candidate_key_status: 'not_candidate',
            role_candidates: ['numeric_measure'],
          }],
        }],
      },
      relationships: [],
      model_recommendations: [],
      warnings: [],
      limitations: ['Business meaning requires confirmation.'],
    },
    provenance: { operation_type: 'schema_architect', operation_version: '1' },
  }
}

function historyItems(offset, total = 11) {
  const count = Math.max(0, Math.min(10, total - offset))
  return Array.from({ length: count }, (_, index) => {
    const id = total - offset - index
    return {
      id,
      status: id === 1 ? 'Completed' : 'Failed',
      stale: id === 1,
      created_at: '2026-10-19T12:00:00',
    }
  })
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('Schema Architect workflow', () => {
  beforeEach(() => {
    window.location.hash = '#analysis'
    globalThis.fetch = vi.fn(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/health') return jsonResponse({ status: 'healthy' })
      if (url.pathname === '/api/v1/system/info') {
        return jsonResponse({ application: { name: 'InsightOS', version: 'test' }, services: {} })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([project])
      if (url.pathname === `/api/v1/projects/${project.id}/datasets`) return jsonResponse([dataset])
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files`) return jsonResponse([file])
      if (url.pathname === `/api/v1/datasets/${dataset.id}/schema-analyses` && options.method === 'POST') {
        return jsonResponse(completedAnalysis())
      }
      if (url.pathname === `/api/v1/datasets/${dataset.id}/schema-analyses`) {
        return jsonResponse({
          dataset_id: dataset.id,
          total: 11,
          limit: Number(url.searchParams.get('limit')),
          offset: Number(url.searchParams.get('offset')),
          items: historyItems(Number(url.searchParams.get('offset'))),
        })
      }
      if (url.pathname === `/api/v1/datasets/${dataset.id}/schema-analyses/1`) {
        return jsonResponse(completedAnalysis(1, true))
      }
      if (url.pathname.includes('/schema-analyses/')) {
        return jsonResponse({ detail: 'Schema analysis not found.' }, 404)
      }
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })
  })

  afterEach(() => {
    cleanup()
  })

  it('selects normalized tables, creates and retrieves analysis, recomputes, paginates, and shows stale state', async () => {
    render(<App />)

    await screen.findByRole('option', { name: project.name })
    fireEvent.change(screen.getByLabelText('Project'), { target: { value: String(project.id) } })
    await screen.findByRole('option', { name: dataset.name })
    fireEvent.change(screen.getByLabelText('Dataset'), { target: { value: String(dataset.id) } })

    const tableCheckbox = await screen.findByRole('checkbox', { name: /orders/ })
    fireEvent.click(tableCheckbox)
    fireEvent.change(screen.getByLabelText(/Business context/), {
      target: { value: 'Orders are customer purchases.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Analyze selected tables' }))

    expect(await screen.findByRole('heading', { name: 'Proposed analytical model' })).toBeTruthy()
    expect(await screen.findByText('Analysis #501')).toBeTruthy()
    expect(await screen.findByText('Business meaning requires confirmation.')).toBeTruthy()
    await waitFor(() => {
      const createRequest = globalThis.fetch.mock.calls.find(([url, options]) => (
        String(url).includes('/schema-analyses') && options?.method === 'POST'
      ))
      expect(createRequest).toBeTruthy()
      expect(JSON.parse(createRequest[1].body)).toMatchObject({
        files: [{ file_id: file.id, table_indices: [0] }],
        business_context: 'Orders are customer purchases.',
        recompute: false,
      })
    })

    fireEvent.click(screen.getByRole('button', { name: 'Recompute' }))
    await waitFor(() => {
      const requests = globalThis.fetch.mock.calls.filter(([url, options]) => (
        String(url).includes('/schema-analyses') && options?.method === 'POST'
      ))
      expect(requests).toHaveLength(2)
      expect(JSON.parse(requests[1][1].body).recompute).toBe(true)
    })

    const historyRegion = screen.getByRole('heading', { name: /History/ }).closest('section')
    fireEvent.click(within(historyRegion).getByRole('button', { name: 'Next' }))
    const staleEntry = await within(historyRegion).findByRole('button', { name: /Analysis #1/ })
    expect(within(staleEntry).getByText('Stale')).toBeTruthy()
    fireEvent.click(staleEntry)
    expect(await screen.findByText(/dataset inputs changed after this result/)).toBeTruthy()
  })

  it('shows sanitized API errors returned by the service', async () => {
    globalThis.fetch.mockImplementation(async (input) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/health') return jsonResponse({ status: 'healthy' })
      if (url.pathname === '/api/v1/system/info') return jsonResponse({ services: {} })
      if (url.pathname === '/api/v1/projects') return jsonResponse({ detail: 'Project listing unavailable.' }, 503)
      return jsonResponse({ detail: 'Unexpected request.' }, 500)
    })
    render(<App />)
    expect((await screen.findByRole('alert')).textContent).toContain('Project listing unavailable.')
  })
})
