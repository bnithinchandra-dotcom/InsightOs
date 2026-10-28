import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import DataWorkspace from './DataWorkspace'

const project = { id: 17, name: 'Sample project' }
const dataset = { id: 29, project_id: project.id, name: 'Sample sales' }

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('data workspace upload flow', () => {
  it('creates a local profile and project through the existing APIs', async () => {
    const projects = []
    let profileCreated = false
    globalThis.fetch = vi.fn(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/workspace/profile' && options.method === 'POST') {
        profileCreated = true
        return jsonResponse({
          id: 1,
          display_name: 'Local analyst',
          email: 'analyst@example.invalid',
        }, 201)
      }
      if (url.pathname === '/api/v1/workspace/profile') {
        return jsonResponse({
          configured: profileCreated,
          profile: profileCreated
            ? { id: 1, display_name: 'Local analyst', email: 'analyst@example.invalid' }
            : null,
        })
      }
      if (url.pathname === '/api/v1/projects' && options.method === 'POST') {
        const created = { ...project, name: JSON.parse(options.body).name }
        projects.push(created)
        return jsonResponse(created, 201)
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse(projects)
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<DataWorkspace page="projects" />)
    fireEvent.change(await screen.findByLabelText('Display name'), {
      target: { value: 'Local analyst' },
    })
    fireEvent.change(screen.getByLabelText('Email identity'), {
      target: { value: 'analyst@example.invalid' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create local profile' }))

    await screen.findByText('Local analyst')
    fireEvent.change(screen.getByLabelText('Project name'), {
      target: { value: 'Sample project' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create project' }))

    expect(await screen.findByText('Sample project')).toBeTruthy()
    const createCall = globalThis.fetch.mock.calls.find(([url, options]) => (
      String(url).includes('/api/v1/projects') && options?.method === 'POST'
    ))
    expect(JSON.parse(createCall[1].body)).toEqual({ name: 'Sample project' })
  })

  it('creates a dataset, multipart uploads a file, normalizes it, and displays EDA results', async () => {
    let datasetCreated = false
    let uploaded = false
    let normalized = false
    globalThis.fetch = vi.fn(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/workspace/profile') {
        return jsonResponse({
          configured: true,
          profile: { id: 1, display_name: 'Local analyst', email: 'analyst@example.invalid' },
        })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([project])
      if (url.pathname === `/api/v1/projects/${project.id}/datasets` && options.method === 'POST') {
        datasetCreated = true
        return jsonResponse(dataset, 201)
      }
      if (url.pathname === `/api/v1/projects/${project.id}/datasets`) {
        return jsonResponse(datasetCreated ? [dataset] : [])
      }
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files` && options.method === 'POST') {
        uploaded = true
        expect(options.body).toBeInstanceOf(FormData)
        expect(options.headers).toBeUndefined()
        return jsonResponse({
          file: { id: 39, filename: 'sales.csv', status: 'Ready' },
        }, 201)
      }
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files`) {
        return jsonResponse(uploaded ? [{
          id: 39,
          dataset_id: dataset.id,
          filename: 'sales.csv',
          status: 'Ready',
          detected_format: 'csv',
          normalization_status: normalized ? 'Ready' : 'NotStarted',
          parsing_result: { row_count: 3 },
          profile_result: {
            tables: [{ columns: [{ name: 'revenue' }, { name: 'region' }] }],
          },
        }] : [])
      }
      if (url.pathname.endsWith('/files/39/normalize')) {
        normalized = true
        return jsonResponse({ status: 'Ready' })
      }
      if (url.pathname.endsWith('/files/39/eda')) {
        expect(JSON.parse(options.body)).toEqual({
          analysis_type: 'univariate',
          selected_columns: ['revenue'],
        })
        return jsonResponse({
          result: {
            rows_analyzed: 3,
            source: { artifact_sha256: 'a'.repeat(64) },
            columns: [{ name: 'revenue', statistics: { mean: 12.5 } }],
          },
          execution_placement: { target_id: 'backend' },
        })
      }
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<DataWorkspace page="datasets" />)
    await screen.findByRole('option', { name: /Sample project/ })
    fireEvent.change(screen.getByLabelText('New dataset name'), {
      target: { value: 'Sample sales' },
    })
    fireEvent.change(screen.getByLabelText(/File \(/), {
      target: { files: [new File(['order_id,revenue\\n1,10\\n2,15\\n3,12.5\\n'], 'sales.csv', { type: 'text/csv' })] },
    })
    expect(screen.getByLabelText(/File \(/).files[0].name).toBe('sales.csv')
    fireEvent.submit(screen.getByRole('button', { name: 'Create dataset and upload' }).closest('form'))

    expect(await screen.findByText(/Uploaded sales.csv; ingestion is ready for normalization/)).toBeTruthy()
    expect(screen.getByText(/Ingestion: Ready · Normalization: Not Started/)).toBeTruthy()
    fireEvent.click(await screen.findByRole('button', { name: 'Normalize' }))
    expect(await screen.findByText(/Normalization: Ready/)).toBeTruthy()
    fireEvent.click(await screen.findByRole('button', { name: 'Run EDA' }))

    expect(await screen.findByRole('heading', { name: 'EDA result' })).toBeTruthy()
    expect(screen.getByText(/3 rows analyzed/)).toBeTruthy()
    expect(screen.getByText(/"mean": 12.5/)).toBeTruthy()
    await waitFor(() => {
      expect(globalThis.fetch.mock.calls.some(([url]) => (
        String(url).endsWith('/files/39/eda')
      ))).toBe(true)
    })

  })

  it('assesses ML suitability and submits an explicitly selected anomaly column', async () => {
    const calls = []
    globalThis.fetch = vi.fn(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/workspace/profile') {
        return jsonResponse({
          configured: true,
          profile: { id: 1, display_name: 'Local analyst', email: 'analyst@example.invalid' },
        })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([project])
      if (url.pathname === `/api/v1/projects/${project.id}/datasets`) return jsonResponse([dataset])
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files`) {
        return jsonResponse([{
          id: 39,
          dataset_id: dataset.id,
          filename: 'sales.parquet',
          status: 'Ready',
          normalization_status: 'Ready',
          profile_result: { tables: [{ columns: [{ name: 'revenue' }] }] },
        }])
      }
      if (url.pathname === '/api/v1/analytics/ml/suitability') {
        calls.push(JSON.parse(options.body))
        return jsonResponse({
          row_count: 20,
          column_count: 1,
          source: { source_fingerprint: 'd'.repeat(64) },
          tasks: [
            {
              task_type: 'anomaly_detection',
              status: 'needs_user_input',
              candidate_columns: ['revenue'],
              missing_prerequisites: ['numeric_column_selection'],
              reason: 'Select a numeric column.',
            },
            {
              task_type: 'supervised_regression',
              status: 'needs_user_input',
              candidate_target_columns: ['revenue'],
              missing_prerequisites: ['explicit_target', 'feature_selection'],
              reason: 'Select target and features explicitly.',
            },
          ],
          limitations: ['Targets are not inferred.'],
        })
      }
      if (url.pathname.endsWith('/files/39/eda')) {
        calls.push(JSON.parse(options.body))
        return jsonResponse({
          id: 88,
          status: 'Completed',
          dataset_id: dataset.id,
          file_id: 39,
          result: {
            analysis_type: 'anomaly_detection',
            rows_analyzed: 20,
            analyzed_columns: ['revenue'],
            anomaly_summary: {
              method: 'median_absolute_deviation_modified_z_score',
              threshold: 3.5,
              anomaly_count: 1,
              evidence_rows: [{ row_index: 20, value: 1000, robust_score: 9 }],
              warnings: ['Flags are not proof of errors.'],
              limitations: ['Univariate screening only.'],
            },
          },
        })
      }
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<DataWorkspace page="datasets" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Assess ML task suitability' }))
    expect(await screen.findByText(/supervised regression — needs user input/i)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Run anomaly screening' }))
    expect(await screen.findByRole('heading', { name: /1 flagged observations/i })).toBeTruthy()
    expect(calls).toEqual([
      { dataset_id: dataset.id, file_id: 39, table_index: 0 },
      { analysis_type: 'anomaly_detection', selected_columns: ['revenue'] },
    ])
    expect(screen.getByText('Flags are not proof of errors.')).toBeTruthy()
  })

  it('submits explicit supervised target and features and displays persisted metrics', async () => {
    let evaluationPayload
    globalThis.fetch = vi.fn(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/workspace/profile') {
        return jsonResponse({
          configured: true,
          profile: { id: 1, display_name: 'Local analyst', email: 'analyst@example.invalid' },
        })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([project])
      if (url.pathname === `/api/v1/projects/${project.id}/datasets`) return jsonResponse([dataset])
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files`) {
        return jsonResponse([{
          id: 39,
          dataset_id: dataset.id,
          filename: 'sales.parquet',
          status: 'Ready',
          normalization_status: 'Ready',
          profile_result: { tables: [{ columns: [{ name: 'outcome' }, { name: 'signal' }] }] },
        }])
      }
      if (url.pathname === '/api/v1/analytics/ml/supervised' && options.method !== 'POST') {
        return jsonResponse({ detail: 'No saved evaluation.' }, 404)
      }
      if (url.pathname === '/api/v1/analytics/ml/suitability') {
        return jsonResponse({
          row_count: 100,
          column_count: 2,
          source: { source_fingerprint: 'd'.repeat(64) },
          columns: [
            { name: 'outcome', kind: 'numeric' },
            { name: 'signal', kind: 'numeric' },
          ],
          tasks: [
            {
              task_type: 'supervised_regression',
              status: 'needs_user_input',
              candidate_target_columns: ['outcome'],
              missing_prerequisites: ['explicit_target', 'feature_selection'],
              reason: 'Choose a target and features.',
            },
            {
              task_type: 'supervised_classification',
              status: 'unsuitable',
              candidate_target_columns: [],
              missing_prerequisites: ['target_column_of_compatible_type'],
              reason: 'No categorical target.',
            },
          ],
          limitations: ['Targets are not inferred.'],
        })
      }
      if (url.pathname === '/api/v1/analytics/ml/supervised' && options.method === 'POST') {
        evaluationPayload = JSON.parse(options.body)
        return jsonResponse({
          workflow_id: 'a'.repeat(32),
          task_id: 'b'.repeat(32),
          status: 'completed',
          result: {
            task_type: 'regression',
            target_column: 'outcome',
            feature_columns: ['signal'],
            evaluation: {
              test_sample_count: 20,
              split_strategy: { reason: 'Deterministic held-out split.' },
              test_result: {
                selected_candidate: 'ridge',
                model: { mae: 1.25, rmse: 1.5 },
                baseline: { mae: 5.0, rmse: 6.0 },
              },
            },
            warnings: [],
            limitations: ['Split-specific metrics.'],
          },
        })
      }
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<DataWorkspace page="datasets" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Assess ML task suitability' }))
    await screen.findByLabelText('Explicit target')
    fireEvent.change(screen.getByLabelText('Explicit features (multi-select)'), {
      target: { value: 'signal' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run supervised evaluation' }))
    expect(await screen.findByText(/held-out test n=20 · selected ridge/)).toBeTruthy()
    expect(evaluationPayload).toEqual({
      dataset_id: dataset.id,
      file_id: 39,
      table_index: 0,
      task_type: 'regression',
      target_column: 'outcome',
      feature_columns: ['signal'],
      time_column: null,
      group_column: null,
      confirmed_suspicious_features: [],
    })
  })

  it('shows failed ingestion and keeps analysis and normalization unavailable', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/workspace/profile') {
        return jsonResponse({
          configured: true,
          profile: { id: 1, display_name: 'Local analyst', email: 'analyst@example.invalid' },
        })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([project])
      if (url.pathname === `/api/v1/projects/${project.id}/datasets`) {
        return jsonResponse([dataset])
      }
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files`) {
        return jsonResponse([{
          id: 39,
          dataset_id: dataset.id,
          filename: 'invalid.csv',
          status: 'Failed',
          error_message: 'The uploaded file could not be parsed.',
          normalization_status: 'NotStarted',
        }])
      }
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<DataWorkspace page="datasets" />)
    expect(await screen.findByText(/Ingestion: Failed · Normalization: Not Started/)).toBeTruthy()
    expect(screen.getByText('The uploaded file could not be parsed.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Normalize' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Run EDA' })).toBeNull()
    expect(screen.queryByText('Monthly forecasting')).toBeNull()
  })

  it('renders duplicate column names with distinct React keys', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/workspace/profile') {
        return jsonResponse({
          configured: true,
          profile: { id: 1, display_name: 'Local analyst', email: 'analyst@example.invalid' },
        })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([project])
      if (url.pathname === `/api/v1/projects/${project.id}/datasets`) {
        return jsonResponse([dataset])
      }
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files`) {
        return jsonResponse([{
          id: 39,
          dataset_id: dataset.id,
          filename: 'sales.csv',
          status: 'Ready',
          detected_format: 'csv',
          normalization_status: 'Ready',
          profile_result: {
            tables: [{
              columns: [
                { name: 'revenue', position: 0 },
                { name: 'revenue', position: 0 },
              ],
            }],
          },
        }])
      }
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    try {
      render(<DataWorkspace page="datasets" />)
      const columnSelect = await screen.findByLabelText('Column')
      expect([...columnSelect.options].map((option) => option.value)).toEqual([
        'revenue',
        'revenue',
      ])
      expect(consoleError.mock.calls.flat().join(' ')).not.toMatch(
        /same key|unique "key" prop/i,
      )
    } finally {
      consoleError.mockRestore()
    }
  })

  it('persists EDA evidence as a workflow result and saves its verified reference to a dashboard', async () => {
    const dashboard = { id: 'e'.repeat(32), title: 'EDA evidence' }
    const planRequest = []
    const saveRequest = []
    const reportResult = {
      summary: { rows_analyzed: 3, findings_count: 0 },
      charts: [],
      findings: [],
      warnings: [],
      provenance: { operation_id: 'eda-report-operation' },
    }
    globalThis.fetch = vi.fn(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/workspace/profile') {
        return jsonResponse({
          configured: true,
          profile: { id: 1, display_name: 'Local analyst', email: 'analyst@example.invalid' },
        })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([project])
      if (url.pathname === `/api/v1/projects/${project.id}/datasets`) {
        return jsonResponse([dataset])
      }
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files`) {
        return jsonResponse([{
          id: 39,
          dataset_id: dataset.id,
          filename: 'sales.csv',
          status: 'Ready',
          normalization_status: 'Ready',
          profile_result: {
            tables: [{ columns: [{ name: 'revenue' }] }],
          },
        }])
      }
      if (url.pathname.endsWith('/files/39/eda') && options.method === 'POST') {
        return jsonResponse({
          id: 77,
          file_id: 39,
          status: 'Completed',
          result: {
            rows_analyzed: 3,
            source: { artifact_sha256: 'a'.repeat(64) },
            columns: [{ name: 'revenue', statistics: { mean: 12.5 } }],
          },
          execution_placement: { target_id: 'backend' },
        })
      }
      if (url.pathname === '/api/v1/chat/analytics/plans' && options.method === 'POST') {
        planRequest.push(JSON.parse(options.body))
        return jsonResponse({
          contract_version: 1,
          plan_id: 'b'.repeat(32),
          workflow_id: 'b'.repeat(32),
          status: 'completed',
          limits: {
            maximum_operations: 5,
            maximum_dependency_depth: 4,
            supports_result_handoffs: false,
          },
          operations: [{
            operation_id: 'eda-evidence-report',
            operation: 'generate_eda_evidence_report',
            status: 'completed',
            depends_on: [],
            result_reference: {
              workflow_id: 'b'.repeat(32),
              task_id: 'eda-evidence-report',
              result_sha256: 'c'.repeat(64),
            },
            result: reportResult,
            source_references: [],
            provenance: reportResult.provenance,
            warnings: [],
            error_code: null,
            reason: null,
          }],
          error_code: null,
          completed_at: '2026-10-28T10:00:00Z',
        })
      }
      if (url.pathname === '/api/v1/dashboards' && options.method !== 'POST') {
        return jsonResponse([dashboard])
      }
      if (url.pathname === `/api/v1/dashboards/${dashboard.id}/analytics-results`) {
        saveRequest.push(JSON.parse(options.body))
        return jsonResponse({ duplicate: false })
      }
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<DataWorkspace page="datasets" />)
    expect(await screen.findByRole('region', { name: 'Guided analytics journey' })).toBeTruthy()
    fireEvent.click(await screen.findByRole('button', { name: 'Run EDA' }))
    await screen.findByRole('heading', { name: 'EDA result' })
    fireEvent.click(screen.getByRole('button', { name: 'Build evidence report' }))

    expect(await screen.findByText('0 measured findings')).toBeTruthy()
    expect(planRequest).toEqual([{
      operations: [{
        operation_id: 'eda-evidence-report',
        operation: 'generate_eda_evidence_report',
        parameters: {
          dataset_id: dataset.id,
          file_id: 39,
          analysis_id: 77,
        },
        depends_on: [],
      }],
    }])
    fireEvent.click(screen.getByRole('button', { name: 'Save EDA report to dashboard' }))
    expect(await screen.findByText(/Saved this EDA evidence report to dashboard "EDA evidence"/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Open saved dashboard' }).getAttribute('href'))
      .toBe(`#dashboards?dashboardId=${dashboard.id}`)
    expect(saveRequest).toEqual([{
      workflow_id: 'b'.repeat(32),
      task_id: 'eda-evidence-report',
    }])
  })

  it('shows an actionable upload error returned by the API', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    globalThis.fetch = vi.fn(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/workspace/profile') {
        return jsonResponse({
          configured: true,
          profile: { id: 1, display_name: 'Local analyst', email: 'analyst@example.invalid' },
        })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([project])
      if (url.pathname === `/api/v1/projects/${project.id}/datasets` && options.method === 'POST') {
        return jsonResponse(dataset, 201)
      }
      if (url.pathname === `/api/v1/projects/${project.id}/datasets`) return jsonResponse([dataset])
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files` && options.method === 'POST') {
        return jsonResponse({
          detail: { code: 'unsupported_file_type', message: 'Unsupported file extension.' },
        }, 415)
      }
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files`) return jsonResponse([])
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<DataWorkspace page="datasets" />)
    await screen.findByRole('option', { name: /Sample project/ })
    fireEvent.change(screen.getByLabelText('New dataset name'), {
      target: { value: 'Invalid upload' },
    })
    fireEvent.change(screen.getByLabelText(/File \(/), {
      target: { files: [new File(['x'], 'unsupported.exe', { type: 'application/octet-stream' })] },
    })
    expect(screen.getByLabelText(/File \(/).files[0].name).toBe('unsupported.exe')
    fireEvent.submit(screen.getByRole('button', { name: 'Create dataset and upload' }).closest('form'))

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByRole('alert').textContent).toContain('Unsupported file extension.')
    expect(screen.getByRole('alert').textContent).toContain('dataset is retained')
    expect(consoleError.mock.calls.flat().join(' ')).not.toMatch(
      /same key|unique "key" prop/i,
    )
  })

  it('creates and retrieves a forecast before presenting observed and predicted values', async () => {
    const savedDashboard = { id: 'c'.repeat(32), title: 'Monthly report' }
    const createdDashboard = { id: 'd'.repeat(32), title: 'New forecast dashboard' }
    const savedForecasts = []
    const forecastResult = {
      dataset_id: dataset.id,
      file_id: 39,
      date_column: 'month',
      target_column: 'revenue',
      aggregation: 'sum',
      horizon: 1,
      source_fingerprint: 'a'.repeat(64),
      historical: [{ period: '2024-12', observed: 110 }],
      forecast: [{
        period: '2025-01',
        predicted: 120.5,
        lower_bound: 115,
        upper_bound: 126,
      }],
      evaluation: {
        method: 'chronological_holdout',
        sample_count: 6,
        period_start: '2024-07',
        period_end: '2024-12',
        candidate: { mae: 3, rmse: 4, wape: 2.5 },
        baseline: { mae: 6, rmse: 7, wape: 5 },
        baseline_name: 'last_observation',
        beats_baseline: true,
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
      chart: {
        title: 'revenue monthly forecast',
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
      limitations: [],
    }
    globalThis.fetch = vi.fn(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/workspace/profile') {
        return jsonResponse({
          configured: true,
          profile: { id: 1, display_name: 'Local analyst', email: 'analyst@example.invalid' },
        })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([project])
      if (url.pathname === '/api/v1/dashboards') {
        if (options.method === 'POST') {
          expect(JSON.parse(options.body)).toEqual({
            title: 'New forecast dashboard',
            description: null,
            analysis_request: null,
            chart_requests: [],
          })
          return jsonResponse(createdDashboard, 201)
        }
        return jsonResponse([savedDashboard])
      }
      if (url.pathname.endsWith('/forecasts') && options.method === 'POST') {
        savedForecasts.push({
          path: url.pathname,
          body: JSON.parse(options.body),
        })
        return jsonResponse({ duplicate: false })
      }
      if (url.pathname === `/api/v1/projects/${project.id}/datasets`) {
        return jsonResponse([dataset])
      }
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files`) {
        return jsonResponse([{
          id: 39,
          dataset_id: dataset.id,
          filename: 'sales.parquet',
          status: 'Ready',
          normalization_status: 'Ready',
          profile_result: {
            tables: [{ columns: [{ name: 'month' }, { name: 'revenue' }] }],
          },
        }])
      }
      if (url.pathname === '/api/v1/analytics/forecasts/monthly' && options.method === 'POST') {
        expect(JSON.parse(options.body)).toEqual({
          dataset_id: dataset.id,
          file_id: 39,
          table_index: 0,
          date_column: 'month',
          target_column: 'revenue',
          aggregation: 'sum',
          horizon: 1,
        })
        return jsonResponse({
          workflow_id: 'b'.repeat(32),
          status: 'completed',
        })
      }
      if (url.pathname === `/api/v1/analytics/forecasts/monthly/${'b'.repeat(32)}`) {
        return jsonResponse({
          workflow_id: 'b'.repeat(32),
          status: 'completed',
          completed_at: '2026-10-28T10:00:00',
          task: { status: 'completed', result: forecastResult },
        })
      }
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<DataWorkspace page="datasets" />)
    fireEvent.click(await screen.findByText('Monthly forecasting'))
    fireEvent.change(await screen.findByLabelText('Time column'), {
      target: { value: 'month' },
    })
    fireEvent.change(screen.getByLabelText('Numeric target'), {
      target: { value: 'revenue' },
    })
    fireEvent.change(screen.getByLabelText('Horizon (months)'), {
      target: { value: '1' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run monthly forecast' }))

    expect(await screen.findByText(/monthly forecast for sales\.parquet completed/i)).toBeTruthy()
    expect(screen.getByText('Observed history')).toBeTruthy()
    expect(screen.getByText('Forecast')).toBeTruthy()
    expect(screen.getByText(/2025-01: 120\.50 · empirical error envelope/)).toBeTruthy()
    expect(screen.getByText(/6 evaluation period\(s\) \(2024-07–2024-12\)/)).toBeTruthy()
    expect(screen.getByText(/comparison beats_baseline/)).toBeTruthy()
    expect(screen.getByText(/empirical error envelope 115\.00–126\.00/)).toBeTruthy()
    expect(screen.getByText(/not a calibrated prediction or confidence interval/i)).toBeTruthy()
    expect(screen.getByText(/Holdout metrics may be sensitive/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Save forecast to dashboard' }))
    expect(await screen.findByText(/Saved this forecast to dashboard "Monthly report"/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Save this run to dashboard'), {
      target: { value: '__create_forecast_dashboard__' },
    })
    fireEvent.change(screen.getByLabelText('New dashboard name'), {
      target: { value: 'New forecast dashboard' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save forecast to dashboard' }))
    expect(await screen.findByText(/Saved this forecast to dashboard "New forecast dashboard"/)).toBeTruthy()
    expect(savedForecasts).toEqual([
      {
        path: `/api/v1/dashboards/${savedDashboard.id}/forecasts`,
        body: { workflow_id: 'b'.repeat(32) },
      },
      {
        path: `/api/v1/dashboards/${createdDashboard.id}/forecasts`,
        body: { workflow_id: 'b'.repeat(32) },
      },
    ])
  })
})
