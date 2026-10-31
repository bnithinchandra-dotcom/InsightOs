import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import ConversationWorkspace from './ConversationWorkspace'

const conversation = {
  id: 12,
  title: 'Summarize data',
  status: 'Active',
  project_id: null,
  dataset_id: null,
  created_at: '2026-10-19T10:00:00',
  updated_at: '2026-10-19T10:00:00',
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('conversation workspace', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/chat/conversations' && options.method === 'POST') {
        return jsonResponse(conversation, 201)
      }
      if (url.pathname === '/api/v1/chat/conversations' && options.method !== 'POST') {
        return jsonResponse([])
      }
      if (url.pathname === '/api/v1/chat/provider-status') {
        return jsonResponse({
          provider: 'none',
          status: 'not_configured',
          model: null,
          message: 'Provider is not configured.',
        })
      }
      if (url.pathname === '/api/v1/chat/analytics/capabilities') {
        return jsonResponse({
          operations: [{
            operation: 'generate_evidence_report',
            description: 'Generate deterministic evidence reports.',
            source_scope: 'multiple_datasets',
            execution_kind: 'deterministic',
            supported_outputs: ['summary', 'findings', 'charts', 'provenance'],
            workflow_report_result: true,
            plan_eligible: true,
            dependency_ordering: true,
            supported_result_references: [],
            parameter_schema: {
              type: 'object',
              properties: { sources: { type: 'array' } },
              required: ['sources'],
            },
          }],
          plan_limits: {
            maximum_operations: 5,
            maximum_dependency_depth: 4,
            supports_result_handoffs: false,
          },
        })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([])
      if (url.pathname === `/api/v1/chat/conversations/${conversation.id}`) {
        return jsonResponse({
          ...conversation,
          total_messages: 0,
          limit: 25,
          offset: 0,
          messages: [],
        })
      }
      if (url.pathname === `/api/v1/chat/conversations/${conversation.id}/messages`) {
        return jsonResponse({
          conversation: { ...conversation, status: 'Active' },
          user_message: {
            id: 22,
            sequence: 1,
            role: 'user',
            content: 'Show my profile',
            created_at: '2026-10-19T10:01:00',
          },
          assistant_message: {
            id: 23,
            sequence: 2,
            role: 'assistant',
            content: 'The stored profile is ready.',
            metadata: {
              execution_status: 'completed',
              tool_activities: [{
                tool_name: 'get_profile',
                label: 'Reviewing a data profile',
                status: 'succeeded',
                summary: 'Retrieved the stored dataset profile.',
              }],
              structured_results: [],
              warnings: [],
            },
            created_at: '2026-10-19T10:01:01',
          },
          execution_status: 'completed',
          tool_activities: [],
          structured_results: [],
          warnings: [],
          error_code: null,
        })
      }
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })
  })

  afterEach(() => cleanup())

  it('creates a conversation, sends a message, and displays the persisted result', async () => {
    render(<ConversationWorkspace />)
    expect(await screen.findByText('Provider is not configured.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'New conversation' }))
    expect(await screen.findByRole('heading', { name: "Your business has a story. Let's uncover it." })).toBeTruthy()

    fireEvent.change(document.querySelector('input[type="file"]'), {
      target: { files: [new File(['a,b'], 'sales.csv', { type: 'text/csv' })] },
    })
    expect(screen.getByText(/selected on this device only/)).toBeTruthy()

    const input = screen.getByLabelText('Your message')
    fireEvent.change(input, { target: { value: 'Show my profile' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText('The stored profile is ready.')).toBeTruthy()
    expect(screen.queryByLabelText('Visualization Canvas')).toBeNull()
    expect(screen.getByText('Reviewing a data profile')).toBeTruthy()
    const sentRequest = globalThis.fetch.mock.calls.find(([url]) => (
      new URL(String(url), window.location.origin).pathname
      === `/api/v1/chat/conversations/${conversation.id}/messages`
    ))
    expect(JSON.parse(sentRequest[1].body)).toEqual({ content: 'Show my profile' })
    expect(input.value).toBe('')
  })

  it('loads a conversation selected from History', async () => {
    render(<ConversationWorkspace selectedConversationId={conversation.id} />)

    expect(await screen.findByRole('heading', { name: 'Summarize data' })).toBeTruthy()
    const request = globalThis.fetch.mock.calls.find(([url]) => (
      new URL(String(url), window.location.origin).pathname
      === `/api/v1/chat/conversations/${conversation.id}`
    ))
    expect(request).toBeTruthy()
  })

  it('creates a dashboard and saves a completed Assistant report by reference', async () => {
    const dashboard = {
      id: 'a'.repeat(32),
      title: 'Pinned Assistant report',
      definition: {
        analysis_request: null,
        chart_requests: [],
        layout: [],
        forecast_components: [],
        analytics_components: [],
      },
    }
    const workflowId = 'b'.repeat(32)
    const taskId = 'tool:generate_evidence_report:verified-report'
    globalThis.fetch.mockImplementation(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/chat/conversations' && options.method === 'POST') {
        return jsonResponse(conversation, 201)
      }
      if (url.pathname === '/api/v1/chat/conversations') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/provider-status') {
        return jsonResponse({
          provider: 'none',
          status: 'not_configured',
          message: 'Provider is not configured.',
        })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([])
      if (url.pathname === `/api/v1/chat/conversations/${conversation.id}/messages`) {
        return jsonResponse({
          conversation,
          user_message: {
            id: 22,
            sequence: 1,
            role: 'user',
            content: 'Save the report',
            created_at: '2026-10-19T10:01:00',
          },
          assistant_message: {
            id: 23,
            sequence: 2,
            role: 'assistant',
            content: 'The verified report is ready.',
            metadata: {
              execution_status: 'completed',
              tool_activities: [],
              structured_results: [{
                tool: 'generate_evidence_report',
                status: 'succeeded',
                summary: 'Generated a verified report.',
                data: { findings: [] },
                references: [{
                  type: 'analytics_workflow_result',
                  workflow_id: workflowId,
                  task_id: taskId,
                }],
              }],
              warnings: [],
            },
            created_at: '2026-10-19T10:01:01',
          },
          execution_status: 'completed',
          tool_activities: [],
          structured_results: [],
          warnings: [],
          error_code: null,
        })
      }
      if (url.pathname === '/api/v1/dashboards' && options.method === 'GET') {
        return jsonResponse([])
      }
      if (url.pathname === '/api/v1/dashboards' && options.method === 'POST') {
        expect(JSON.parse(options.body)).toMatchObject({
          title: 'Assistant evidence',
          analysis_request: null,
          chart_requests: [],
        })
        return jsonResponse(dashboard, 201)
      }
      if (
        url.pathname === `/api/v1/dashboards/${dashboard.id}/analytics-results`
        && options.method === 'POST'
      ) {
        expect(JSON.parse(options.body)).toEqual({
          workflow_id: workflowId,
          task_id: taskId,
        })
        return jsonResponse({
          dashboard,
          analytics_component: {},
          duplicate: false,
        })
      }
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<ConversationWorkspace />)
    fireEvent.click(await screen.findByRole('button', { name: 'New conversation' }))
    await screen.findByRole('heading', { name: "Your business has a story. Let's uncover it." })
    const input = screen.getByLabelText('Your message')
    fireEvent.change(input, { target: { value: 'Save the report' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText('The verified report is ready.')).toBeTruthy()
    fireEvent.click(screen.getByText('generate evidence report result'))
    fireEvent.click(screen.getByRole('button', { name: 'Save verified report to dashboard' }))
    fireEvent.change(await screen.findByLabelText('Save this report to dashboard'), {
      target: { value: '__create_dashboard__' },
    })
    fireEvent.change(screen.getByLabelText('New dashboard name'), {
      target: { value: 'Assistant evidence' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save verified report' }))

    expect(await screen.findByText(
      'Saved the verified analytics result to dashboard "Pinned Assistant report".',
    )).toBeTruthy()
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining(`/api/v1/dashboards/${dashboard.id}/analytics-results`),
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('discovers and runs a registered structured report, then offers dashboard save', async () => {
    const workflowId = 'c'.repeat(32)
    const taskId = 'tool:generate_evidence_report:structured'
    const dashboard = {
      id: 'd'.repeat(32),
      title: 'Structured analytics',
      definition: {
        analysis_request: null,
        chart_requests: [],
        layout: [],
        forecast_components: [],
        analytics_components: [],
      },
    }
    globalThis.fetch.mockImplementation(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/chat/conversations') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/provider-status') {
        return jsonResponse({ provider: 'none', status: 'not_configured', message: '' })
      }
      if (url.pathname === '/api/v1/chat/analytics/capabilities') {
        return jsonResponse({
          operations: [{
            operation: 'generate_evidence_report',
            description: 'Compute deterministic grouped findings and charts.',
            source_scope: 'multiple_datasets',
            execution_kind: 'deterministic',
            supported_outputs: ['summary', 'findings', 'charts', 'provenance'],
            workflow_report_result: true,
            plan_eligible: true,
            dependency_ordering: true,
            supported_result_references: [],
            parameter_schema: {
              type: 'object',
              properties: { sources: { type: 'array' } },
              required: ['sources'],
            },
          }],
        })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/analytics/executions' && options.method === 'POST') {
        expect(JSON.parse(options.body)).toEqual({
          operation: 'generate_evidence_report',
          parameters: { sources: [] },
        })
        return jsonResponse({
          contract_version: 1,
          request_id: workflowId,
          execution_id: workflowId,
          workflow_id: workflowId,
          task_id: taskId,
          operation: 'generate_evidence_report',
          workflow_status: 'completed',
          task_status: 'completed',
          dependency_task_ids: [],
          source_references: [],
          parameters: { sources: [] },
          result_sha256: 'e'.repeat(64),
          result: {
            summary: { joined_row_count: 7, group_count: 1, findings_count: 1 },
            findings: [{ id: 'finding-1', statement: 'Revenue equals 42.' }],
            charts: [{
              title: 'Revenue by region',
              chart_type: 'bar',
              x_field: 'region',
              y_field: 'revenue',
              data: {
                labels: ['west'],
                series: [{ name: 'revenue', values: [42] }],
              },
              warnings: [],
            }],
            warnings: [],
          },
          evidence: [],
          warnings: [],
          provenance: { operation_id: 'f'.repeat(64) },
          completed_at: '2026-10-28T10:00:00+00:00',
        })
      }
      if (url.pathname === '/api/v1/dashboards' && options.method === 'GET') {
        return jsonResponse([dashboard])
      }
      if (
        url.pathname === `/api/v1/dashboards/${dashboard.id}/analytics-results`
        && options.method === 'POST'
      ) {
        return jsonResponse({
          dashboard,
          analytics_component: {},
          duplicate: false,
        })
      }
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<ConversationWorkspace />)
    const operationSelect = await screen.findByLabelText('Evidence-report operation')
    expect(operationSelect.options[0].value).toBe('generate_evidence_report')
    fireEvent.change(screen.getByLabelText('Operation parameters as JSON'), {
      target: { value: '{"sources":[]}' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run structured analytics' }))

    expect(await screen.findByText(/workflow completed · operation completed/)).toBeTruthy()
    expect(screen.getByText('Revenue equals 42.')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Revenue by region' })).toBeTruthy()
    expect(await screen.findByLabelText('Save this report to dashboard')).toBeTruthy()
    expect(screen.getByText(/Parameter schema/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Save verified report' }))
    expect(await screen.findByText(
      'Saved the verified analytics result to dashboard "Structured analytics".',
    )).toBeTruthy()
  })

  it('reports malformed structured parameters before sending an execution request', async () => {
    render(<ConversationWorkspace />)
    await screen.findByLabelText('Evidence-report operation')
    fireEvent.change(screen.getByLabelText('Operation parameters as JSON'), {
      target: { value: '{invalid' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run structured analytics' }))

    expect(await screen.findByText('Operation parameters must be valid JSON.')).toBeTruthy()
    expect(globalThis.fetch).not.toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/chat/analytics/executions'),
      expect.anything(),
    )
  })

  it('validates a plan locally and presents successful and failed operations for saving', async () => {
    const planWorkflowId = 'c'.repeat(32)
    let planSubmissions = 0
    const planDashboard = {
      id: 'd'.repeat(32),
      title: 'Structured analytics',
      definition: {
        analysis_request: null,
        chart_requests: [],
        layout: [],
        forecast_components: [],
        analytics_components: [],
      },
    }
    const resultReference = {
      workflow_id: planWorkflowId,
      task_id: 'summary',
      result_sha256: 'd'.repeat(64),
    }
    globalThis.fetch.mockImplementation(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/chat/conversations') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/provider-status') {
        return jsonResponse({ provider: 'none', status: 'not_configured', message: '' })
      }
      if (url.pathname === '/api/v1/chat/analytics/capabilities') {
        return jsonResponse({
          operations: [{
            operation: 'generate_evidence_report',
            description: 'Generate verified report output.',
            source_scope: 'multiple_datasets',
            execution_kind: 'deterministic',
            supported_outputs: ['summary', 'findings', 'charts', 'provenance'],
            workflow_report_result: true,
            plan_eligible: true,
            parameter_schema: { type: 'object', properties: {} },
          }],
          plan_limits: {
            maximum_operations: 5,
            maximum_dependency_depth: 4,
            supports_result_handoffs: false,
          },
        })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/analytics/plans' && options.method === 'POST') {
        planSubmissions += 1
        if (planSubmissions > 1) {
          return jsonResponse({ detail: 'Temporary plan error.' }, 503)
        }
        expect(JSON.parse(options.body)).toEqual({
          operations: [
            {
              operation_id: 'failed',
              operation: 'generate_evidence_report',
              parameters: { sources: [] },
              depends_on: [],
            },
            {
              operation_id: 'summary',
              operation: 'generate_evidence_report',
              parameters: { sources: [] },
              depends_on: [],
            },
            {
              operation_id: 'blocked',
              operation: 'generate_evidence_report',
              parameters: { sources: [] },
              depends_on: ['failed'],
            },
          ],
        })
        return jsonResponse({
          plan_id: planWorkflowId,
          workflow_id: planWorkflowId,
          status: 'partial',
          operations: [
            {
              operation_id: 'failed',
              operation: 'generate_evidence_report',
              status: 'failed',
              depends_on: [],
              result_reference: null,
              result: null,
              source_references: [],
              provenance: null,
              warnings: [],
              error_code: 'synthetic_failure',
              reason: 'Operation failed.',
            },
            {
              operation_id: 'blocked',
              operation: 'generate_evidence_report',
              status: 'blocked',
              depends_on: ['failed'],
              result_reference: null,
              result: null,
              source_references: [],
              provenance: null,
              warnings: [],
              error_code: 'dependency_not_completed',
              reason: null,
            },
            {
              operation_id: 'summary',
              operation: 'generate_evidence_report',
              status: 'completed',
              depends_on: [],
              result_reference: resultReference,
              result: {
                summary: { joined_row_count: 7 },
                findings: [{ id: 'finding-1', statement: 'Seven rows were joined.' }],
                charts: [],
                warnings: [],
              },
              source_references: [],
              provenance: { operation_id: 'e'.repeat(64) },
              warnings: [],
              error_code: null,
              reason: null,
            },
          ],
        })
      }
      if (url.pathname === '/api/v1/dashboards' && options.method === 'GET') {
        return jsonResponse([planDashboard])
      }
      if (
        url.pathname === `/api/v1/dashboards/${planDashboard.id}/analytics-results`
        && options.method === 'POST'
      ) {
        expect(JSON.parse(options.body)).toEqual({
          workflow_id: planWorkflowId,
          task_id: 'summary',
        })
        return jsonResponse({ dashboard: planDashboard, analytics_component: {}, duplicate: false })
      }
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<ConversationWorkspace />)
    const planInput = await screen.findByLabelText('Analytics plan as JSON')
    fireEvent.change(planInput, {
      target: {
        value: JSON.stringify({
          operations: [
            {
              operation_id: 'failed',
              operation: 'generate_evidence_report',
              parameters: { sources: [] },
              depends_on: [],
            },
            {
              operation_id: 'summary',
              operation: 'generate_evidence_report',
              parameters: { sources: [] },
              depends_on: [],
            },
            {
              operation_id: 'blocked',
              operation: 'generate_evidence_report',
              parameters: { sources: [] },
              depends_on: ['failed'],
            },
          ],
        }),
      },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Validate and run plan' }))

    expect(await screen.findByText(/Plan .* · partial/)).toBeTruthy()
    expect(screen.getByLabelText('Operation failed: failed')).toBeTruthy()
    expect(screen.getByLabelText('Operation blocked: blocked')).toBeTruthy()
    expect(screen.getByText('Seven rows were joined.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Validate and run plan' }))
    expect(await screen.findByText('Temporary plan error.')).toBeTruthy()
    expect(screen.getByText('Seven rows were joined.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Save verified result to dashboard' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Save verified report' }))
    expect(await screen.findByText(
      'Saved the verified analytics result to dashboard "Structured analytics".',
    )).toBeTruthy()
  })

  it('rejects invalid plan dependencies without sending an execution request', async () => {
    render(<ConversationWorkspace />)
    fireEvent.change(await screen.findByLabelText('Analytics plan as JSON'), {
      target: {
        value: JSON.stringify({
          operations: [
            {
              operation_id: 'same',
              operation: 'generate_evidence_report',
              parameters: {},
              depends_on: ['same'],
            },
          ],
        }),
      },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Validate and run plan' }))
    expect(await screen.findByText('Operation "same" cannot depend on itself.')).toBeTruthy()
    expect(globalThis.fetch).not.toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/chat/analytics/plans'),
      expect.anything(),
    )
  })

  it('preserves the draft and reports an API error', async () => {
    render(<ConversationWorkspace />)
    fireEvent.click(await screen.findByRole('button', { name: 'New conversation' }))
    await screen.findByRole('heading', { name: "Your business has a story. Let's uncover it." })
    globalThis.fetch.mockImplementation(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === `/api/v1/chat/conversations/${conversation.id}/messages`) {
        return jsonResponse({ detail: 'A response is already being generated.' }, 409)
      }
      if (url.pathname === '/api/v1/chat/conversations' && options.method === 'POST') {
        return jsonResponse(conversation, 201)
      }
      if (url.pathname === '/api/v1/chat/conversations') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/provider-status') {
        return jsonResponse({ provider: 'none', status: 'not_configured', message: '' })
      }
      if (url.pathname === '/api/v1/chat/analytics/capabilities') {
        return jsonResponse({ operations: [] })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([])
      if (url.pathname === `/api/v1/chat/conversations/${conversation.id}`) {
        return jsonResponse({ ...conversation, total_messages: 0, messages: [], offset: 0 })
      }
      return jsonResponse({ detail: 'Unexpected request.' }, 500)
    })

    const input = screen.getByLabelText('Your message')
    fireEvent.change(input, { target: { value: 'Keep this draft' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect((await screen.findByRole('alert')).textContent).toContain('A response is already being generated.')
    await waitFor(() => expect(input.value).toBe('Keep this draft'))
  })

  it('shows model failures as errors and allows another message to recover', async () => {
    let requestCount = 0
    globalThis.fetch.mockImplementation(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/chat/conversations' && options.method === 'POST') {
        return jsonResponse(conversation, 201)
      }
      if (url.pathname === '/api/v1/chat/conversations') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/provider-status') {
        return jsonResponse({ provider: 'ollama', status: 'healthy', message: '' })
      }
      if (url.pathname === '/api/v1/chat/analytics/capabilities') {
        return jsonResponse({ operations: [] })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([])
      if (url.pathname === `/api/v1/chat/conversations/${conversation.id}`) {
        return jsonResponse({ ...conversation, total_messages: 0, messages: [], offset: 0 })
      }
      if (url.pathname === `/api/v1/chat/conversations/${conversation.id}/messages`) {
        requestCount += 1
        const failed = requestCount === 1
        return jsonResponse({
          conversation: { ...conversation, status: 'Active' },
          user_message: {
            id: 30 + requestCount * 2,
            sequence: requestCount * 2 - 1,
            role: 'user',
            content: JSON.parse(options.body).content,
            created_at: '2026-10-19T10:01:00',
          },
          assistant_message: {
            id: 31 + requestCount * 2,
            sequence: requestCount * 2,
            role: 'assistant',
            content: failed
              ? 'The configured model returned an empty response. Retry, and check the local model if this continues.'
              : '20',
            metadata: {
              execution_status: failed ? 'failed' : 'completed',
              tool_activities: [],
              structured_results: [],
              warnings: [],
            },
            created_at: '2026-10-19T10:01:01',
          },
          execution_status: failed ? 'failed' : 'completed',
          tool_activities: [],
          structured_results: [],
          warnings: [],
          error_code: failed ? 'provider_response_invalid' : null,
        })
      }
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<ConversationWorkspace />)
    fireEvent.click(await screen.findByRole('button', { name: 'New conversation' }))
    await screen.findByRole('heading', { name: "Your business has a story. Let's uncover it." })

    const input = screen.getByLabelText('Your message')
    fireEvent.change(input, { target: { value: '10+10' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    const failure = await screen.findByRole('alert')
    expect(failure.textContent).toContain('returned an empty response')
    expect(input.value).toBe('')
    expect(screen.getByRole('button', { name: 'Send message' }).disabled).toBe(true)

    fireEvent.change(input, { target: { value: 'Try again: 10+10' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText('20')).toBeTruthy()
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(requestCount).toBe(2)
  })

  it('uses the existing project and dataset APIs for optional conversation context', async () => {
    const createPayloads = []
    globalThis.fetch.mockImplementation(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/chat/conversations' && options.method === 'POST') {
        createPayloads.push(JSON.parse(options.body))
        return jsonResponse({
          ...conversation,
          project_id: 31,
          dataset_id: 42,
        }, 201)
      }
      if (url.pathname === '/api/v1/chat/conversations') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/provider-status') {
        return jsonResponse({ provider: 'none', status: 'not_configured', message: '' })
      }
      if (url.pathname === '/api/v1/projects') {
        return jsonResponse([{ id: 31, name: 'Existing project' }])
      }
      if (url.pathname === '/api/v1/projects/31/datasets') {
        return jsonResponse([{ id: 42, name: 'Existing dataset', project_id: 31 }])
      }
      return jsonResponse({ detail: 'Unexpected request.' }, 500)
    })

    render(<ConversationWorkspace />)
    fireEvent.change(await screen.findByLabelText(/Project context/), {
      target: { value: '31' },
    })
    fireEvent.change(await screen.findByLabelText(/Dataset context/), {
      target: { value: '42' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'New conversation' }))
    fireEvent.change(screen.getByLabelText('Your message'), {
      target: { value: 'Summarize this dataset' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => expect(createPayloads).toEqual([{
      project_id: 31,
      dataset_id: 42,
    }]))
  })
it('displays human-readable error when analytics capabilities return 404', async () => {
    globalThis.fetch.mockImplementation(async (input) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/chat/conversations') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/provider-status') {
        return jsonResponse({ provider: 'none', status: 'not_configured', message: '' })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/analytics/capabilities') {
        return jsonResponse({ detail: 'Not Found' }, 404)
      }
      return jsonResponse({ detail: 'Unexpected request.' }, 500)
    })

    render(<ConversationWorkspace />)
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByRole('alert').textContent).toContain('Analytics capabilities endpoint not found')
  })

  it('displays human-readable error when analytics capabilities return 409 profile required', async () => {
    globalThis.fetch.mockImplementation(async (input) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/chat/conversations') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/provider-status') {
        return jsonResponse({ provider: 'none', status: 'not_configured', message: '' })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/analytics/capabilities') {
        return jsonResponse({
          detail: {
            code: 'workspace_profile_required',
            message: 'Create a local workspace profile before using protected workspace data.',
          },
        }, 409)
      }
      return jsonResponse({ detail: 'Unexpected request.' }, 500)
    })

    render(<ConversationWorkspace />)
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByRole('alert').textContent).toContain('No workspace profile found')
  })

  it('renders example goal chips in initial state and fills draft on click', async () => {
    globalThis.fetch.mockImplementation(async (input) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/chat/conversations') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/provider-status') {
        return jsonResponse({ provider: 'none', status: 'not_configured', message: '' })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/analytics/capabilities') {
        return jsonResponse({ operations: [] })
      }
      return jsonResponse({ detail: 'Unexpected request.' }, 500)
    })

    render(<ConversationWorkspace />)
    expect(await screen.findByRole('heading', { name: "Your business has a story. Let's uncover it." })).toBeTruthy()
    expect(screen.queryByLabelText('Saved conversations')).toBeNull()
    expect(globalThis.fetch.mock.calls.some(([input, options]) => (
      new URL(String(input), window.location.origin).pathname === '/api/v1/chat/conversations'
      && options?.method !== 'POST'
    ))).toBe(false)
    const chip = screen.getByRole('button', { name: 'Which products generate the most revenue?' })
    expect(chip).toBeTruthy()
    fireEvent.click(chip)

    const textarea = screen.getByLabelText('Your message')
    expect(textarea.value).toBe('Which products generate the most revenue?')
  })

  it('switches to visualization mode only when the persisted assistant result contains a chart', async () => {
    const defaultFetch = globalThis.fetch.getMockImplementation()
    globalThis.fetch.mockImplementation(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === `/api/v1/chat/conversations/${conversation.id}/messages`) {
        return jsonResponse({
          conversation,
          user_message: {
            id: 32,
            sequence: 1,
            role: 'user',
            content: 'I need help understanding this.',
            created_at: '2026-10-19T10:01:00',
          },
          assistant_message: {
            id: 33,
            sequence: 2,
            role: 'assistant',
            content: 'Here is the persisted result.',
            metadata: {
              execution_status: 'completed',
              tool_activities: [],
              structured_results: [{
                tool: 'generate_evidence_report',
                status: 'succeeded',
                summary: 'Monthly revenue',
                data: {
                  charts: [{
                    title: 'Monthly revenue',
                    chart_type: 'bar',
                    x_field: 'month',
                    y_field: 'revenue',
                    data: {
                      labels: ['Jan'],
                      series: [{ name: 'Revenue', values: [12] }],
                    },
                  }],
                  findings: [],
                },
                references: [],
              }],
              warnings: [],
            },
            created_at: '2026-10-19T10:01:01',
          },
          execution_status: 'completed',
          tool_activities: [],
          structured_results: [],
          warnings: [],
          error_code: null,
        })
      }
      return defaultFetch(input, options)
    })

    render(<ConversationWorkspace />)
    fireEvent.change(await screen.findByLabelText('Your message'), {
      target: { value: 'I need help understanding this.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByLabelText('Visualization Canvas')).toBeTruthy()
    expect(screen.getAllByRole('heading', { name: 'Monthly revenue' }).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: 'Conversation' }))
    expect(screen.queryByLabelText('Visualization Canvas')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Open workspace' }))
    expect(screen.getByLabelText('Visualization Canvas')).toBeTruthy()
    expect(screen.getAllByRole('heading', { name: 'Monthly revenue' }).length).toBeGreaterThan(0)
  })

  it('opens conversation history inside the assistant workspace', async () => {
    render(<ConversationWorkspace />)
    fireEvent.click(await screen.findByRole('button', { name: 'Conversation history' }))
    expect(await screen.findByRole('dialog', { name: 'Conversation history' })).toBeTruthy()
    expect(screen.getByText('Your conversations will appear here.')).toBeTruthy()
  })
})
