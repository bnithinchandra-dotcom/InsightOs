import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import Dashboards from './Dashboards'

const api = vi.hoisted(() => ({
  createDashboard: vi.fn(),
  deleteDashboard: vi.fn(),
  generateAnalyticsReport: vi.fn(),
  getDashboard: vi.fn(),
  getDashboards: vi.fn(),
  getDatasetFiles: vi.fn(),
  getProjectDatasets: vi.fn(),
  getProjects: vi.fn(),
}))

vi.mock('./api', () => api)

const dashboard = {
  id: 'a'.repeat(32),
  title: 'Sales overview',
  description: null,
  definition: {
    analysis_request: {
      sources: [],
      joins: [],
      dimensions: [],
      metrics: [],
    },
    chart_requests: [],
    layout: [],
  },
}

describe('dashboards', () => {
  beforeEach(() => {
    api.getDashboards.mockResolvedValue([dashboard])
    api.getProjects.mockResolvedValue([])
    api.getDashboard.mockResolvedValue(dashboard)
    api.generateAnalyticsReport.mockResolvedValue({
      summary: {
        joined_row_count: 3,
        group_count: 2,
        findings_count: 1,
      },
      findings: [{
        id: 'finding-1',
        statement: 'revenue is 12 for region=east.',
        supporting_metric: { valid_count: 3, group_row_count: 3 },
        source_references: [{ dataset_id: 1, file_id: 2 }],
      }],
      charts: [{
        title: 'Revenue by region',
        chart_type: 'bar',
        x_field: 'region',
        y_field: 'revenue',
        data: {
          labels: ['east', 'west'],
          series: [{ name: 'revenue', values: [12, 25] }],
        },
        warnings: [],
      }],
      warnings: [],
      provenance: { operation_id: 'c'.repeat(64) },
    })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('loads saved dashboards and renders an on-demand evidence-backed report', async () => {
    render(<Dashboards />)
    expect(await screen.findByText('Sales overview')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Refresh report' }))

    expect(await screen.findByRole('heading', { name: 'Revenue by region' })).toBeTruthy()
    expect(screen.getByText('revenue is 12 for region=east.')).toBeTruthy()
    expect(screen.getByText(/Dashboards refresh only when requested/)).toBeTruthy()
    await waitFor(() => expect(api.generateAnalyticsReport).toHaveBeenCalledWith({
      sources: [],
      joins: [],
      dimensions: [],
      metrics: [],
      charts: [],
    }))
  })

  it('shows an actionable error when dashboard loading fails', async () => {
    api.getDashboards.mockRejectedValueOnce(new Error('Dashboard service unavailable.'))
    render(<Dashboards />)

    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Dashboard service unavailable.')
  })
})
