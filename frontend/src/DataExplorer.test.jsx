import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import DataExplorer from './DataExplorer'
import VisualizationCanvas from './VisualizationCanvas'

const project = { id: 3, name: 'Retail' }
const dataset = { id: 8, name: 'Orders' }
const file = {
  id: 11,
  dataset_id: dataset.id,
  filename: 'orders.csv',
  status: 'Ready',
  normalization_status: 'Ready',
  normalization_result: {
    tables: [{
      name: 'orders',
      row_count: 42,
      column_count: 2,
      schema: [{ name: 'order_id' }, { name: 'revenue' }],
    }],
  },
}

function jsonResponse(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('adaptive data explorer', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn(async (input) => {
      const path = new URL(String(input), window.location.origin).pathname
      if (path === `/api/v1/datasets/${dataset.id}/files`) return jsonResponse([file])
      return jsonResponse({ detail: `Unexpected request: ${path}` }, 500)
    })
  })

  afterEach(() => cleanup())

  it('browses actual dataset files and selects normalized table metadata', async () => {
    const onSelectTable = vi.fn()
    render(
      <DataExplorer
        projects={[project]}
        datasets={[dataset]}
        projectId={String(project.id)}
        datasetId={String(dataset.id)}
        onProjectChange={vi.fn()}
        onDatasetChange={vi.fn()}
        selectedTable={null}
        onSelectTable={onSelectTable}
        onClose={vi.fn()}
      />,
    )

    fireEvent.click(await screen.findByRole('button', { name: /orders\.csv/ }))
    const tableButton = await screen.findByRole('button', { name: /42 rows/ })
    fireEvent.click(tableButton)

    await waitFor(() => {
      expect(onSelectTable).toHaveBeenCalledWith({
        datasetId: dataset.id,
        fileId: file.id,
        fileName: file.filename,
        tableIndex: 0,
        name: 'orders',
        rowCount: 42,
        columnCount: 2,
        columns: ['order_id', 'revenue'],
      })
    })
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining(`/api/v1/datasets/${dataset.id}/files`),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('states that source rows are unavailable instead of inventing a preview', () => {
    render(
      <VisualizationCanvas
        artifacts={[]}
        selectedTable={{
          fileName: file.filename,
          name: 'orders',
          rowCount: 42,
          columnCount: 2,
          columns: ['order_id', 'revenue'],
        }}
        onSave={vi.fn()}
        onReturnToConversation={vi.fn()}
        onOpenDataExplorer={vi.fn()}
        dataExplorerOpen
      />,
    )

    expect(screen.getByText(/Row preview is not provided by the current dataset API/)).toBeTruthy()
    expect(screen.queryByText('Sample order')).toBeNull()
  })

  it('keeps an active conversation scope unchanged while browsing dataset metadata', async () => {
    const onDatasetChange = vi.fn()
    render(
      <DataExplorer
        projects={[project]}
        datasets={[dataset]}
        projectId={String(project.id)}
        datasetId={String(dataset.id)}
        conversationActive
        onProjectChange={vi.fn()}
        onDatasetChange={onDatasetChange}
        selectedTable={null}
        onSelectTable={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    expect(await screen.findByText(/keeps the data scope it started with/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Orders', exact: true }))
    expect(onDatasetChange).not.toHaveBeenCalled()
  })
})
