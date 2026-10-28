import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import Dashboards from './Dashboards'

const api = vi.hoisted(() => ({
  createDashboard: vi.fn(),
  deleteDashboard: vi.fn(),
  generateAnalyticsReport: vi.fn(),
  getAnalyticsWorkflowResult: vi.fn(),
  getDashboard: vi.fn(),
  getDashboards: vi.fn(),
  getDatasetFiles: vi.fn(),
  getMonthlyForecast: vi.fn(),
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
    api.getAnalyticsWorkflowResult.mockResolvedValue(null)
    api.getMonthlyForecast.mockResolvedValue(null)
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
    window.location.hash = ''
    vi.clearAllMocks()
  })

  it('opens the dashboard targeted by a saved-result link', async () => {
    const pinnedDashboard = {
      ...dashboard,
      id: 'b'.repeat(32),
      title: 'Pinned evidence',
    }
    api.getDashboards.mockResolvedValue([dashboard, pinnedDashboard])
    api.getDashboard.mockResolvedValue(pinnedDashboard)
    window.location.hash = `#dashboards?dashboardId=${pinnedDashboard.id}`

    render(<Dashboards />)

    const selector = await screen.findByLabelText('Dashboard')
    expect(selector.value).toBe(pinnedDashboard.id)
    expect(await screen.findByText('Pinned evidence')).toBeTruthy()
  })

  it('loads saved dashboards and renders an on-demand evidence-backed report', async () => {
    render(<Dashboards />)
    expect(await screen.findByText('Sales overview')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Refresh report' }))

    expect(await screen.findByRole('heading', { name: 'Revenue by region' })).toBeTruthy()
    expect(screen.getByText('revenue is 12 for region=east.')).toBeTruthy()
    expect(screen.getByText(/Dashboard report refreshed from current source data/)).toBeTruthy()
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

  it('reloads a saved forecast from its original run without retraining it', async () => {
    const component = {
      workflow_id: 'b'.repeat(32),
      dataset_id: 29,
      file_id: 39,
      table_index: 0,
      source_fingerprint: 'a'.repeat(64),
      normalized_artifact_sha256: 'c'.repeat(64),
      date_column: 'month',
      target_column: 'revenue',
      aggregation: 'sum',
      horizon: 1,
      result_sha256: 'd'.repeat(64),
      model_artifact_sha256: 'e'.repeat(64),
      chart_specification: {
        title: 'revenue monthly forecast',
        chart_type: 'line',
        x_field: 'month',
        y_field: 'revenue',
      },
      forecast_completed_at: '2026-10-28T10:00:00',
    }
    api.getDashboard.mockResolvedValue({
      ...dashboard,
      definition: {
        ...dashboard.definition,
        forecast_components: [component],
      },
    })
    api.getMonthlyForecast.mockResolvedValue({
      status: 'completed',
      completed_at: component.forecast_completed_at,
      task: {
        status: 'completed',
        result: {
          dataset_id: component.dataset_id,
          file_id: component.file_id,
          table_index: component.table_index,
          source_fingerprint: component.source_fingerprint,
          normalized_artifact_sha256: component.normalized_artifact_sha256,
          date_column: component.date_column,
          target_column: component.target_column,
          aggregation: component.aggregation,
          horizon: component.horizon,
          result_sha256: component.result_sha256,
          model_artifact_sha256: component.model_artifact_sha256,
          evaluation: {
            method: 'chronological_holdout',
            sample_count: 6,
            period_start: '2024-07',
            period_end: '2024-12',
            candidate: { mae: 3, rmse: 4, wape: 2.5 },
            baseline: { mae: 6, rmse: 7, wape: 5 },
            baseline_name: 'last_observation',
            comparison: { outcome: 'beats_baseline' },
          },
          comparison: { outcome: 'beats_baseline' },
          uncertainty: {
            status: 'available',
            reason: null,
            limitations: ['Not a calibrated interval.'],
          },
          suitability: {
            status: 'warning',
            findings: [{
              severity: 'warning',
              code: 'small_chronological_holdout',
              message: 'Holdout metrics may be sensitive to individual months.',
            }],
          },
          forecast: [{
            period: '2025-01',
            predicted: 120.5,
            lower_bound: 115,
            upper_bound: 126,
          }],
          chart: {
            title: component.chart_specification.title,
            chart_type: 'line',
            x_field: 'month',
            y_field: 'revenue',
            data: {
              labels: ['2024-12', '2025-01'],
              x_values: ['2024-12', '2025-01'],
              series: [
                { name: 'Observed history', values: [110, null] },
                { name: 'Forecast', values: [null, 120.5] },
              ],
            },
            warnings: [],
          },
          warnings: [],
        },
      },
    })

    render(<Dashboards />)

    expect(await screen.findByRole('heading', { name: 'revenue monthly forecast' })).toBeTruthy()
    expect(screen.getByText('Observed history')).toBeTruthy()
    expect(screen.getByText('Forecast')).toBeTruthy()
    expect(screen.getByText(/Measured holdout MAE 3\.00 vs last_observation baseline MAE 6\.00/)).toBeTruthy()
    expect(screen.getByText(/6 evaluation period\(s\) \(2024-07–2024-12\)/)).toBeTruthy()
    expect(screen.getByText(/comparison beats_baseline/)).toBeTruthy()
    expect(screen.getByText(/not a calibrated prediction interval/i)).toBeTruthy()
    expect(screen.getByText(/Forecast 2025-01: 120\.50 · empirical error envelope 115\.00–126\.00/)).toBeTruthy()
    expect(screen.getByText(/Holdout metrics may be sensitive/)).toBeTruthy()
    await waitFor(() => {
      expect(api.getMonthlyForecast).toHaveBeenCalledTimes(1)
    })

    fireEvent.click(screen.getByRole('button', { name: 'Refresh report' }))
    await waitFor(() => {
      expect(api.getMonthlyForecast).toHaveBeenCalledTimes(2)
    })
    expect(api.generateAnalyticsReport).toHaveBeenCalledTimes(1)
  })

  it('shows a stale saved forecast as unavailable instead of replacing it', async () => {
    const component = {
      workflow_id: 'f'.repeat(32),
      dataset_id: 29,
      file_id: 39,
      source_fingerprint: 'a'.repeat(64),
      chart_specification: { title: 'Old forecast' },
    }
    api.getDashboard.mockResolvedValue({
      ...dashboard,
      definition: {
        ...dashboard.definition,
        forecast_components: [component],
      },
    })
    api.getMonthlyForecast.mockRejectedValue(new Error('The normalized source changed after this forecast was generated.'))

    render(<Dashboards />)

    expect(await screen.findByText(/saved forecast is unavailable or stale/i)).toBeTruthy()
    expect(screen.getByText(/normalized source changed/)).toBeTruthy()
    expect(api.generateAnalyticsReport).not.toHaveBeenCalled()
  })

  it('reloads a saved analytics result without recomputing it and marks mismatched results unavailable', async () => {
    const component = {
      workflow_id: 'a'.repeat(32),
      task_id: 'tool:generate_evidence_report:verified-report',
      operation: 'generate_evidence_report',
      result_sha256: 'b'.repeat(64),
      source_references: [{
        alias: 'customers',
        dataset_id: 1,
        file_id: 11,
        table_index: 0,
        source_fingerprint: 'c'.repeat(64),
        normalized_artifact_sha256: 'd'.repeat(64),
      }],
      saved_at: '2026-10-28T10:00:00+00:00',
    }
    const savedDashboard = {
      ...dashboard,
      definition: {
        analysis_request: null,
        chart_requests: [],
        layout: [],
        analytics_components: [component],
      },
    }
    const workflowResult = {
      workflow_id: component.workflow_id,
      task_id: component.task_id,
      operation: component.operation,
      result_sha256: component.result_sha256,
      workflow_status: 'completed',
      task_status: 'completed',
      completed_at: '2026-10-28T10:00:00+00:00',
      source_references: component.source_references,
      result: {
        summary: { joined_row_count: 12, group_count: 2, findings_count: 1 },
        findings: [{ id: 'finding-1', statement: 'Revenue is 12.' }],
        charts: [{
          title: 'Revenue by region',
          chart_type: 'bar',
          x_field: 'region',
          y_field: 'revenue',
          data: {
            labels: ['east', 'west'],
            series: [{ name: 'revenue', values: [12, 8] }],
          },
          warnings: [],
        }],
        warnings: [],
      },
      warnings: [],
      provenance: { operation_id: 'c'.repeat(64) },
    }
    api.getDashboard.mockResolvedValue(savedDashboard)
    api.getAnalyticsWorkflowResult.mockResolvedValue(workflowResult)

    render(<Dashboards />)

    expect(await screen.findByRole('heading', { name: 'Revenue by region' })).toBeTruthy()
    expect(screen.getByText('Revenue is 12.')).toBeTruthy()
    expect(screen.getByText(/12 joined row\(s\) · 2 result group\(s\)/)).toBeTruthy()
    expect(api.generateAnalyticsReport).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Refresh report' }))
    await waitFor(() => {
      expect(api.getAnalyticsWorkflowResult).toHaveBeenCalledTimes(2)
    })
    expect(api.generateAnalyticsReport).not.toHaveBeenCalled()
  })

  it('shows a saved analytics output as unavailable when its result digest changed', async () => {
    const component = {
      workflow_id: 'd'.repeat(32),
      task_id: 'tool:generate_evidence_report:stale-report',
      operation: 'generate_evidence_report',
      result_sha256: 'e'.repeat(64),
    }
    api.getDashboard.mockResolvedValue({
      ...dashboard,
      definition: {
        ...dashboard.definition,
        analytics_components: [component],
      },
    })
    api.getAnalyticsWorkflowResult.mockResolvedValue({
      workflow_id: component.workflow_id,
      task_id: component.task_id,
      operation: component.operation,
      result_sha256: 'f'.repeat(64),
    })

    render(<Dashboards />)

    expect(await screen.findByText(/saved analytics result is unavailable or stale/i)).toBeTruthy()
    expect(screen.getByText(/does not match its verified workflow result/)).toBeTruthy()
    expect(api.generateAnalyticsReport).not.toHaveBeenCalled()
  })

  it('marks a saved analytics result unavailable when its source artifact changed', async () => {
    const component = {
      workflow_id: '1'.repeat(32),
      task_id: 'tool:generate_evidence_report:source-change',
      operation: 'generate_evidence_report',
      result_sha256: '2'.repeat(64),
      source_references: [{
        dataset_id: 1,
        file_id: 11,
        table_index: 0,
        source_fingerprint: '3'.repeat(64),
        normalized_artifact_sha256: '4'.repeat(64),
      }],
    }
    api.getDashboard.mockResolvedValue({
      ...dashboard,
      definition: {
        ...dashboard.definition,
        analytics_components: [component],
      },
    })
    api.getAnalyticsWorkflowResult.mockResolvedValue({
      workflow_id: component.workflow_id,
      task_id: component.task_id,
      operation: component.operation,
      result_sha256: component.result_sha256,
      source_references: [{
        ...component.source_references[0],
        normalized_artifact_sha256: '5'.repeat(64),
      }],
      provenance: { operation_id: '6'.repeat(64) },
      result: { charts: [], findings: [] },
    })

    render(<Dashboards />)

    expect(await screen.findByText(/saved analytics result is unavailable or stale/i)).toBeTruthy()
    expect(screen.getByText(/does not match its verified workflow result/)).toBeTruthy()
  })

  it('labels legacy forecast evaluation and uncertainty as unavailable', async () => {
    const component = {
      workflow_id: 'e'.repeat(32),
      dataset_id: 29,
      file_id: 39,
      table_index: 0,
      source_fingerprint: 'a'.repeat(64),
      normalized_artifact_sha256: 'c'.repeat(64),
      date_column: 'month',
      target_column: 'revenue',
      horizon: 1,
      aggregation: 'sum',
      result_sha256: 'd'.repeat(64),
      model_artifact_sha256: 'e'.repeat(64),
      chart_specification: {
        title: 'Legacy forecast',
        chart_type: 'line',
        x_field: 'month',
        y_field: 'revenue',
      },
    }
    api.getDashboard.mockResolvedValue({
      ...dashboard,
      definition: {
        ...dashboard.definition,
        forecast_components: [component],
      },
    })
    api.getMonthlyForecast.mockResolvedValue({
      status: 'completed',
      task: {
        status: 'completed',
        result: {
        dataset_id: component.dataset_id,
        file_id: component.file_id,
        table_index: component.table_index,
        source_fingerprint: component.source_fingerprint,
        normalized_artifact_sha256: component.normalized_artifact_sha256,
        date_column: component.date_column,
        target_column: component.target_column,
        horizon: component.horizon,
        aggregation: component.aggregation,
        result_sha256: component.result_sha256,
        model_artifact_sha256: component.model_artifact_sha256,
        evaluation: {
            candidate: { mae: 3, rmse: 4, wape: null },
            baseline: { mae: 6, rmse: 7, wape: null },
            baseline_name: 'last_observation',
          },
          uncertainty: {
            status: 'unavailable',
            reason: 'This saved forecast predates out-of-sample uncertainty reporting.',
          },
          suitability: { status: 'unavailable', findings: [] },
          forecast: [{ period: '2025-01', predicted: 120.5 }],
          chart: {
            title: 'Legacy forecast',
            chart_type: 'line',
            x_field: 'month',
            y_field: 'revenue',
            data: {
              labels: ['2024-12', '2025-01'],
              x_values: ['2024-12', '2025-01'],
              series: [
                { name: 'Observed history', values: [110, null] },
                { name: 'Forecast', values: [null, 120.5] },
              ],
            },
            warnings: [],
          },
        },
      },
    })

    render(<Dashboards />)

    expect(await screen.findByText(/legacy result; comparison unavailable/)).toBeTruthy()
    expect(screen.getByText(/Uncertainty unavailable: This saved forecast predates/)).toBeTruthy()
    expect(screen.getByText(/Forecast 2025-01: 120\.50 · uncertainty bounds unavailable/)).toBeTruthy()
    expect(screen.getByText(/WAPE unavailable/)).toBeTruthy()
  })
})
