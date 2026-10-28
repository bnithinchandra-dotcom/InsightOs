import { useCallback, useEffect, useState } from 'react'
import {
  createDashboard,
  createConversation,
  executeAnalyticsJourney,
  executeAnalyticsPlan,
  getAnalyticsCapabilities,
  getDashboards,
  getChatProviderStatus,
  getConversation,
  getConversations,
  getProjectDatasets,
  getProjects,
  saveAnalyticsResultToDashboard,
  sendConversationMessage,
} from './api'
import { ChartCard } from './Dashboards'

const CONVERSATION_PAGE_SIZE = 25
const MESSAGE_PAGE_SIZE = 25

function validateAnalyticsPlanDraft(plan, capabilities, limits) {
  if (!plan || typeof plan !== 'object' || Array.isArray(plan)) {
    return 'An analytics plan must be a JSON object.'
  }
  if (!Array.isArray(plan.operations)) {
    return 'The analytics plan must contain an operations array.'
  }
  const maximumOperations = limits?.maximum_operations ?? 5
  const maximumDepth = limits?.maximum_dependency_depth ?? 4
  if (plan.operations.length < 1 || plan.operations.length > maximumOperations) {
    return `A plan must contain 1 to ${maximumOperations} operations.`
  }
  const ids = plan.operations.map((operation) => operation?.operation_id)
  if (ids.some((id) => typeof id !== 'string' || !/^[A-Za-z][A-Za-z0-9_-]{0,47}$/.test(id))) {
    return 'Each operation needs a valid operation_id.'
  }
  if (new Set(ids).size !== ids.length) {
    return 'Operation IDs must be unique.'
  }
  const known = new Set(ids)
  const eligible = new Set(
    capabilities
      .filter((capability) => capability.plan_eligible)
      .map((capability) => capability.operation),
  )
  for (const operation of plan.operations) {
    if (!eligible.has(operation.operation)) {
      return `Operation "${operation.operation}" is not eligible for analytics plans.`
    }
    if (!operation.parameters || typeof operation.parameters !== 'object' || Array.isArray(operation.parameters)) {
      return `Operation "${operation.operation_id}" parameters must be a JSON object.`
    }
    const dependencies = operation.depends_on ?? []
    if (!Array.isArray(dependencies) || new Set(dependencies).size !== dependencies.length) {
      return `Operation "${operation.operation_id}" has invalid dependencies.`
    }
    if (dependencies.includes(operation.operation_id)) {
      return `Operation "${operation.operation_id}" cannot depend on itself.`
    }
    if (dependencies.some((dependency) => !known.has(dependency))) {
      return `Operation "${operation.operation_id}" depends on an unknown operation.`
    }
    if ((operation.result_references ?? []).length > 0) {
      return 'Registered report operations do not support upstream result handoffs.'
    }
  }
  const pending = new Set(ids)
  const ordered = []
  const depths = new Map()
  while (pending.size) {
    const ready = plan.operations.filter((operation) => (
      pending.has(operation.operation_id)
      && (operation.depends_on ?? []).every((dependency) => ordered.includes(dependency))
    ))
    if (!ready.length) return 'Operation dependencies must not contain a cycle.'
    for (const operation of ready) {
      const depth = 1 + Math.max(
        0,
        ...(operation.depends_on ?? []).map((dependency) => depths.get(dependency)),
      )
      if (depth > maximumDepth) {
        return `Dependency depth cannot exceed ${maximumDepth} operations.`
      }
      depths.set(operation.operation_id, depth)
    }
    ready.forEach((operation) => {
      ordered.push(operation.operation_id)
      pending.delete(operation.operation_id)
    })
  }
  return ''
}

function ConversationWorkspace() {
  const [conversations, setConversations] = useState([])
  const [conversationOffset, setConversationOffset] = useState(0)
  const [hasMoreConversations, setHasMoreConversations] = useState(false)
  const [conversation, setConversation] = useState(null)
  const [messages, setMessages] = useState([])
  const [hasOlderMessages, setHasOlderMessages] = useState(false)
  const [projects, setProjects] = useState([])
  const [datasets, setDatasets] = useState([])
  const [projectId, setProjectId] = useState('')
  const [datasetId, setDatasetId] = useState('')
  const [providerStatus, setProviderStatus] = useState(null)
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [dashboardOptions, setDashboardOptions] = useState([])
  const [analyticsSaveTarget, setAnalyticsSaveTarget] = useState('')
  const [newDashboardTitle, setNewDashboardTitle] = useState('')
  const [pendingAnalyticsSave, setPendingAnalyticsSave] = useState(null)
  const [savingAnalytics, setSavingAnalytics] = useState(false)
  const [saveNotice, setSaveNotice] = useState('')
  const [analyticsCapabilities, setAnalyticsCapabilities] = useState([])
  const [analyticsOperation, setAnalyticsOperation] = useState('')
  const [analyticsParameters, setAnalyticsParameters] = useState('{}')
  const [analyticsExecution, setAnalyticsExecution] = useState(null)
  const [runningAnalytics, setRunningAnalytics] = useState(false)
  const [analyticsPlanDraft, setAnalyticsPlanDraft] = useState('{"operations":[]}')
  const [analyticsPlanExecution, setAnalyticsPlanExecution] = useState(null)
  const [runningAnalyticsPlan, setRunningAnalyticsPlan] = useState(false)
  const [analyticsPlanLimits, setAnalyticsPlanLimits] = useState(null)
  const [capabilityError, setCapabilityError] = useState('')

  const loadConversations = useCallback(async (offset = 0, append = false) => {
    const items = await getConversations({ limit: CONVERSATION_PAGE_SIZE, offset })
    setConversations((current) => append ? [...current, ...items] : items)
    setConversationOffset(offset + items.length)
    setHasMoreConversations(items.length === CONVERSATION_PAGE_SIZE)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      getConversations({ limit: CONVERSATION_PAGE_SIZE, signal: controller.signal }),
      getChatProviderStatus(controller.signal),
      getProjects(controller.signal),
    ])
      .then(([items, status, projectItems]) => {
        setConversations(items)
        setConversationOffset(items.length)
        setHasMoreConversations(items.length === CONVERSATION_PAGE_SIZE)
        setProviderStatus(status)
        setProjects(projectItems)
      })
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') setError(requestError.message)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    getAnalyticsCapabilities(controller.signal)
      .then((response) => {
        const operations = Array.isArray(response.operations) ? response.operations : []
        setAnalyticsCapabilities(operations)
        setAnalyticsPlanLimits(response.plan_limits ?? null)
        const defaultOperation = operations.find((item) => item.workflow_report_result)
        if (defaultOperation) setAnalyticsOperation(defaultOperation.operation)
      })
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') {
          setCapabilityError(requestError.message)
        }
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!projectId) return undefined
    const controller = new AbortController()
    getProjectDatasets(projectId, controller.signal)
      .then((items) => setDatasets(items))
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') setError(requestError.message)
      })
    return () => controller.abort()
  }, [projectId])

  async function openConversation(id) {
    setError('')
    try {
      const result = await getConversation(id, { limit: MESSAGE_PAGE_SIZE })
      let page = result
      if (result.total_messages > MESSAGE_PAGE_SIZE) {
        page = await getConversation(id, {
          limit: MESSAGE_PAGE_SIZE,
          offset: result.total_messages - MESSAGE_PAGE_SIZE,
        })
      }
      setConversation(result)
      setMessages(page.messages ?? [])
      setHasOlderMessages((page.offset ?? 0) > 0)
      setProjectId(result.project_id ? String(result.project_id) : '')
      setDatasetId(result.dataset_id ? String(result.dataset_id) : '')
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  async function startConversation() {
    setError('')
    try {
      const payload = {}
      if (projectId) payload.project_id = Number(projectId)
      if (datasetId) payload.dataset_id = Number(datasetId)
      const created = await createConversation(payload)
      setConversation(created)
      setMessages([])
      setHasOlderMessages(false)
      setConversations((current) => [created, ...current.filter((item) => item.id !== created.id)])
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  async function loadMoreConversations() {
    setError('')
    try {
      await loadConversations(conversationOffset, true)
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  async function loadOlderMessages() {
    if (!conversation || messages.length === 0) return
    const offset = Math.max(0, messages[0].sequence - 1 - MESSAGE_PAGE_SIZE)
    if (offset === messages[0].sequence - 1) return
    try {
      const page = await getConversation(conversation.id, {
        limit: MESSAGE_PAGE_SIZE,
        offset,
      })
      setMessages((current) => [...page.messages, ...current])
      setHasOlderMessages(offset > 0)
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  async function submitMessage(event) {
    event.preventDefault()
    const content = draft.trim()
    if (!conversation || !content || sending) return
    setSending(true)
    setError('')
    try {
      const response = await sendConversationMessage(conversation.id, content)
      setConversation(response.conversation)
      const nextMessages = [
        ...messages,
        response.user_message,
        response.assistant_message,
      ]
      setMessages(nextMessages.slice(-MESSAGE_PAGE_SIZE))
      setHasOlderMessages(
        nextMessages.length > MESSAGE_PAGE_SIZE || nextMessages[0].sequence > 1,
      )
      setConversations((current) => [
        response.conversation,
        ...current.filter((item) => item.id !== response.conversation.id),
      ])
      setDraft('')
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setSending(false)
    }
  }

  async function beginAnalyticsSave(reference) {
    setError('')
    setSaveNotice('')
    try {
      const options = await getDashboards()
      setDashboardOptions(options)
      setAnalyticsSaveTarget(options[0]?.id ?? '__create_dashboard__')
      setNewDashboardTitle('Analytics report')
      setPendingAnalyticsSave(reference)
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  async function saveAnalyticsResult(event) {
    event.preventDefault()
    if (!pendingAnalyticsSave || savingAnalytics) return
    setSavingAnalytics(true)
    setError('')
    setSaveNotice('')
    try {
      let dashboard = dashboardOptions.find(
        (item) => item.id === analyticsSaveTarget,
      )
      if (analyticsSaveTarget === '__create_dashboard__') {
        const title = newDashboardTitle.trim()
        if (!title) return
        dashboard = await createDashboard({
          title,
          description: null,
          analysis_request: null,
          chart_requests: [],
        })
        setDashboardOptions((current) => [dashboard, ...current])
      }
      if (!dashboard) throw new Error('Choose or create a dashboard first.')
      const response = await saveAnalyticsResultToDashboard(dashboard.id, {
        workflow_id: pendingAnalyticsSave.workflow_id,
        task_id: pendingAnalyticsSave.task_id,
      })
      setSaveNotice(
        `Saved the verified analytics result to dashboard "${response.dashboard.title}".`,
      )
      setPendingAnalyticsSave(null)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setSavingAnalytics(false)
    }
  }

  async function submitStructuredAnalytics(event) {
    event.preventDefault()
    if (!analyticsOperation || runningAnalytics) return
    setRunningAnalytics(true)
    setError('')
    setSaveNotice('')
    setAnalyticsExecution(null)
    setPendingAnalyticsSave(null)
    try {
      let parameters
      try {
        parameters = JSON.parse(analyticsParameters)
      } catch {
        throw new Error('Operation parameters must be valid JSON.')
      }
      if (!parameters || typeof parameters !== 'object' || Array.isArray(parameters)) {
        throw new Error('Operation parameters must be a JSON object.')
      }
      const result = await executeAnalyticsJourney({
        operation: analyticsOperation,
        parameters,
      })
      setAnalyticsExecution(result)
      if (
        result.workflow_status === 'completed'
        && result.task_status === 'completed'
        && typeof result.workflow_id === 'string'
        && typeof result.task_id === 'string'
      ) {
        await beginAnalyticsSave({
          workflow_id: result.workflow_id,
          task_id: result.task_id,
        })
      }
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setRunningAnalytics(false)
    }
  }

  async function submitAnalyticsPlan(event) {
    event.preventDefault()
    if (runningAnalyticsPlan) return
    setError('')
    setSaveNotice('')
    let plan
    try {
      plan = JSON.parse(analyticsPlanDraft)
    } catch {
      setError('Analytics plan must be valid JSON.')
      return
    }
    const validationError = validateAnalyticsPlanDraft(
      plan,
      analyticsCapabilities,
      analyticsPlanLimits,
    )
    if (validationError) {
      setError(validationError)
      return
    }
    setRunningAnalyticsPlan(true)
    try {
      const result = await executeAnalyticsPlan(plan)
      setAnalyticsPlanExecution(result)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setRunningAnalyticsPlan(false)
    }
  }

  const structuredReportCapabilities = analyticsCapabilities.filter(
    (item) => item.workflow_report_result,
  )
  const selectedAnalyticsCapability = analyticsCapabilities.find(
    (item) => item.operation === analyticsOperation,
  )

  return (
    <section className="conversation-workspace panel" aria-label="InsightOS assistant">
      <aside className="conversation-sidebar">
        <div className="conversation-sidebar-heading">
          <h2>Conversations</h2>
          <button className="button button-secondary" type="button" onClick={startConversation}>
            New chat
          </button>
        </div>
        <label className="conversation-context-field">
          <span>Project context <span className="muted">(optional)</span></span>
          <select
            value={projectId}
            onChange={(event) => {
              setProjectId(event.target.value)
              setDatasetId('')
            }}
          >
            <option value="">Discover available data</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>{project.name}</option>
            ))}
          </select>
        </label>
        {projectId && (
          <label className="conversation-context-field">
            <span>Dataset context <span className="muted">(optional)</span></span>
            <select value={datasetId} onChange={(event) => setDatasetId(event.target.value)}>
              <option value="">All datasets in project</option>
              {datasets.map((dataset) => (
                <option key={dataset.id} value={dataset.id}>{dataset.name}</option>
              ))}
            </select>
          </label>
        )}
        <div className="conversation-list" aria-label="Saved conversations">
          {loading && <p className="muted">Loading conversations…</p>}
          {!loading && conversations.length === 0 && (
            <p className="muted">Your saved conversations will appear here.</p>
          )}
          {conversations.map((item) => (
            <button
              className={`conversation-list-item ${conversation?.id === item.id ? 'selected' : ''}`}
              key={item.id}
              type="button"
              onClick={() => openConversation(item.id)}
            >
              <strong>{item.title}</strong>
              <small>{item.status}</small>
            </button>
          ))}
          {hasMoreConversations && (
            <button className="text-button" type="button" onClick={loadMoreConversations}>
              Load more conversations
            </button>
          )}
        </div>
      </aside>

      <div className="conversation-main">
        <div className="conversation-header">
          <div>
            <p className="eyebrow">Data assistant</p>
            <h2>{conversation?.title ?? 'Start a conversation'}</h2>
          </div>
          {providerStatus && (
            <span className={`provider-status provider-${providerStatus.status}`}>
              Model: {providerStatus.status.replaceAll('_', ' ')}
            </span>
          )}
        </div>

        {providerStatus?.message && (
          <p className="provider-notice" role="status">{providerStatus.message}</p>
        )}
        {error && <p className="conversation-error" role="alert">{error}</p>}

        <section className="structured-chat-result" aria-label="Unified analytics workflow">
          <h3>Structured analytics workflow</h3>
          <p className="muted">
            Select a registered evidence-report operation and provide its validated parameters as JSON.
            The operation runs through the persisted workflow system; it does not invoke a model.
          </p>
          {capabilityError && (
            <p className="conversation-error" role="alert">
              Analytics capabilities could not be loaded: {capabilityError}
            </p>
          )}
          {structuredReportCapabilities.length > 0 ? (
            <form className="conversation-composer" onSubmit={submitStructuredAnalytics}>
              <label>
                Evidence-report operation
                <select
                  value={analyticsOperation}
                  onChange={(event) => {
                    setAnalyticsOperation(event.target.value)
                    setAnalyticsExecution(null)
                    setPendingAnalyticsSave(null)
                  }}
                  disabled={runningAnalytics}
                >
                  {structuredReportCapabilities.map((capability) => (
                    <option key={capability.operation} value={capability.operation}>
                      {capability.operation.replaceAll('_', ' ')}
                    </option>
                  ))}
                </select>
              </label>
              <label className="visually-hidden" htmlFor="analytics-parameters">
                Operation parameters as JSON
              </label>
              <textarea
                id="analytics-parameters"
                value={analyticsParameters}
                onChange={(event) => setAnalyticsParameters(event.target.value)}
                maxLength={4096}
                placeholder="Paste an object matching the selected operation schema."
                rows={5}
                spellCheck="false"
                aria-describedby="analytics-parameter-schema"
                disabled={runningAnalytics}
              />
              <button
                className="button button-primary"
                type="submit"
                disabled={runningAnalytics || !analyticsOperation}
              >
                {runningAnalytics ? 'Running workflow…' : 'Run structured analytics'}
              </button>
            </form>
          ) : !capabilityError && analyticsCapabilities.length === 0 ? (
            <p className="muted">Loading registered analytics capabilities…</p>
          ) : !capabilityError ? (
            <p className="muted">
              Registered analytics capabilities are available through existing paths, but no report operation is enabled for structured workflow submission.
            </p>
          ) : null}
          {selectedAnalyticsCapability && (
            <>
              <p className="muted">{selectedAnalyticsCapability.description}</p>
              <details className="structured-chat-result">
                <summary>Registered analytics capabilities ({analyticsCapabilities.length})</summary>
                {analyticsCapabilities.map((capability) => (
                  <article key={capability.operation}>
                    <strong>{capability.operation}</strong>
                    <p>{capability.description}</p>
                    <p className="muted">
                      {capability.source_scope} · {capability.execution_kind} · outputs:{' '}
                      {capability.supported_outputs.join(', ')} ·{' '}
                      {capability.workflow_report_result
                        ? 'available in this workflow'
                        : 'available through its existing registered path'}
                    </p>
                  </article>
                ))}
              </details>
              <details id="analytics-parameter-schema" className="structured-chat-result">
                <summary>Parameter schema</summary>
                <pre>{JSON.stringify(selectedAnalyticsCapability.parameter_schema, null, 2)}</pre>
              </details>
            </>
          )}
          {analyticsExecution && (
            <div className="structured-chat-result" aria-live="polite">
              <strong>
                Workflow {analyticsExecution.workflow_id}
                {analyticsExecution.task_id && ` · task ${analyticsExecution.task_id}`}
              </strong>
              <p>
                {analyticsExecution.operation.replaceAll('_', ' ')} · workflow{' '}
                {analyticsExecution.workflow_status} · operation {analyticsExecution.task_status}
              </p>
              {analyticsExecution.summary && <p>{analyticsExecution.summary}</p>}
              {analyticsExecution.error_code && (
                <p className="conversation-error" role="alert">
                  {analyticsExecution.error_code}
                </p>
              )}
              {analyticsExecution.result && (
                <>
                  {analyticsExecution.source_references?.length > 0 && (
                    <details className="structured-chat-result">
                      <summary>Verified sources</summary>
                      {analyticsExecution.source_references.map((source) => (
                        <p key={`${source.dataset_id}-${source.file_id}-${source.table_index}`}>
                          {source.alias ?? 'source'} · dataset {source.dataset_id} · file {source.file_id} ·
                          {' '}table {source.table_index} · source {source.source_fingerprint} ·
                          {' '}artifact {source.normalized_artifact_sha256}
                        </p>
                      ))}
                    </details>
                  )}
                  <p className="muted">
                    Result SHA-256 {analyticsExecution.result_sha256} · provenance{' '}
                    {analyticsExecution.provenance.operation_id}
                  </p>
                  {analyticsExecution.result.summary && (
                    <pre>{JSON.stringify(analyticsExecution.result.summary, null, 2)}</pre>
                  )}
                  {analyticsExecution.result.charts?.map((chart) => (
                    <ChartCard
                      chart={chart}
                      key={`${chart.title}-${chart.x_field}-${chart.y_field}`}
                    />
                  ))}
                  {analyticsExecution.result.findings?.slice(0, 20).map((finding) => (
                    <p key={finding.id}>{finding.statement}</p>
                  ))}
                  {analyticsExecution.warnings?.map((warning, index) => (
                    <p className="chat-warning" key={`${warning}-${index}`}>{warning}</p>
                  ))}
                </>
              )}
            </div>
          )}
          <section className="structured-chat-result" aria-label="Multi-operation analytics plan">
            <h4>Multi-operation plan</h4>
            <p className="muted">
              Submit up to {analyticsPlanLimits?.maximum_operations ?? 5} registered evidence-report
              operations, with dependency ordering up to {analyticsPlanLimits?.maximum_dependency_depth ?? 4}
              {' '}operations deep. Dependencies gate execution only; report outputs cannot be passed as inputs.
              A failed operation does not prevent unrelated operations from running.
            </p>
            <form className="conversation-composer" onSubmit={submitAnalyticsPlan}>
              <label htmlFor="analytics-plan-json">Analytics plan as JSON</label>
              <textarea
                id="analytics-plan-json"
                value={analyticsPlanDraft}
                onChange={(event) => setAnalyticsPlanDraft(event.target.value)}
                maxLength={32 * 1024}
                rows={8}
                spellCheck="false"
                placeholder={'{"operations":[{"operation_id":"report","operation":"generate_evidence_report","parameters":{},"depends_on":[]}]}'
                }
                disabled={runningAnalyticsPlan}
              />
              <button
                className="button button-primary"
                type="submit"
                disabled={runningAnalyticsPlan || analyticsCapabilities.length === 0}
              >
                {runningAnalyticsPlan ? 'Running plan…' : 'Validate and run plan'}
              </button>
            </form>
            {analyticsPlanExecution && (
              <div aria-live="polite">
                <h5>
                  Plan {analyticsPlanExecution.plan_id} · {analyticsPlanExecution.status}
                </h5>
                <p className="muted">
                  {analyticsPlanExecution.operations.length} persisted operation(s)
                  {analyticsPlanExecution.status === 'partial'
                    ? ' · successful results can be saved individually'
                    : ''}
                </p>
                {analyticsPlanExecution.operations.map((operation) => (
                  <article
                    className="structured-chat-result"
                    aria-label={`Operation ${operation.operation_id}: ${operation.status}`}
                    key={operation.operation_id}
                  >
                    <strong>
                      {operation.operation_id} · {operation.operation.replaceAll('_', ' ')} ·{' '}
                      {operation.status}
                    </strong>
                    <p className="muted">
                      Depends on: {operation.depends_on.length
                        ? operation.depends_on.join(', ')
                        : 'none'}
                    </p>
                    {operation.error_code && (
                      <p className="conversation-error" role="status">
                        {operation.error_code}{operation.reason ? ` · ${operation.reason}` : ''}
                      </p>
                    )}
                    {operation.result && (
                      <>
                        <p className="muted">
                          Result SHA-256 {operation.result_reference?.result_sha256 ?? 'unavailable'} · provenance{' '}
                          {operation.provenance?.operation_id ?? 'unavailable'}
                        </p>
                        {operation.source_references.map((source) => (
                          <p className="muted" key={`${source.dataset_id}-${source.file_id}-${source.table_index}`}>
                            {source.alias} · dataset {source.dataset_id} · file {source.file_id} ·
                            {' '}table {source.table_index} · source {source.source_fingerprint} ·
                            {' '}artifact {source.normalized_artifact_sha256}
                          </p>
                        ))}
                        {operation.result.summary && (
                          <pre>{JSON.stringify(operation.result.summary, null, 2)}</pre>
                        )}
                        {operation.result.charts?.map((chart) => (
                          <ChartCard
                            chart={chart}
                            key={`${chart.title}-${chart.x_field}-${chart.y_field}`}
                          />
                        ))}
                        {operation.result.findings?.slice(0, 20).map((finding) => (
                          <p key={finding.id}>{finding.statement}</p>
                        ))}
                        {operation.warnings.map((warning, index) => (
                          <p className="chat-warning" key={`${warning}-${index}`}>{warning}</p>
                        ))}
                        {operation.status === 'completed'
                          && operation.result_reference
                          && typeof operation.result_reference.workflow_id === 'string'
                          && typeof operation.result_reference.task_id === 'string' && (
                            <button
                              className="button button-secondary"
                              type="button"
                              onClick={() => beginAnalyticsSave({
                                workflow_id: operation.result_reference.workflow_id,
                                task_id: operation.result_reference.task_id,
                              })}
                            >
                              Save verified result to dashboard
                            </button>
                        )}
                      </>
                    )}
                  </article>
                ))}
              </div>
            )}
          </section>
        </section>

        <div className="conversation-messages" aria-live="polite">
            {hasOlderMessages && (
              <button className="text-button" type="button" onClick={loadOlderMessages}>
                Load older messages
              </button>
            )}
          {!conversation && (
            <div className="conversation-empty">
              <h3>What would you like to understand about your data?</h3>
              <p>Start a saved conversation. The assistant uses only available InsightOS analysis tools and reports when a model provider is unavailable.</p>
              <button className="button button-primary" type="button" onClick={startConversation}>
                Start a conversation
              </button>
            </div>
          )}
          {conversation && messages.length === 0 && (
            <p className="muted">Ask a question about your existing projects, datasets, profiles, quality reports, EDA, or schema analyses.</p>
          )}
          {messages.map((message) => {
            const executionStatus = message.metadata?.execution_status
            const isFailedAssistantMessage = message.role === 'assistant' && (
              executionStatus
                ? executionStatus !== 'completed'
                : !message.content?.trim()
            )
            const content = message.content?.trim()
              ? message.content
              : message.role === 'assistant'
                ? 'No response was generated. Please retry.'
                : ''

            return (
              <article className={`chat-message chat-${message.role}`} key={message.id}>
                <p className="chat-role">{message.role === 'assistant' ? 'InsightOS' : 'You'}</p>
                <p
                  className={`chat-content ${isFailedAssistantMessage ? 'chat-error' : ''}`}
                  role={isFailedAssistantMessage ? 'alert' : undefined}
                >
                  {content}
                </p>
                {message.metadata?.tool_activities?.map((activity, index) => (
                  <div className={`tool-activity tool-${activity.status}`} key={`${activity.tool_name}-${index}`}>
                    <strong>{activity.label}</strong>
                    <span>{activity.summary}</span>
                  </div>
                ))}
                {message.metadata?.structured_results?.map((result, index) => (
                  <details className="structured-chat-result" key={`${result.tool}-${index}`}>
                    <summary>{result.tool.replaceAll('_', ' ')} result</summary>
                    <p>{result.summary}</p>
                    {result.data !== null && result.data !== undefined && (
                      <pre>{JSON.stringify(result.data, null, 2)}</pre>
                    )}
                    {[
                      'generate_evidence_report',
                      'generate_eda_evidence_report',
                    ].includes(result.tool) && result.status === 'succeeded' && (
                      result.references ?? []
                    ).filter((reference) => (
                      reference.type === 'analytics_workflow_result'
                      && typeof reference.workflow_id === 'string'
                      && typeof reference.task_id === 'string'
                    )).map((reference) => (
                      <button
                        className="button button-secondary"
                        type="button"
                        key={`${reference.workflow_id}-${reference.task_id}`}
                        onClick={() => beginAnalyticsSave(reference)}
                      >
                        Save verified report to dashboard
                      </button>
                    ))}
                  </details>
                ))}
                {message.metadata?.warnings?.map((warning, index) => (
                  <p className="chat-warning" key={`${index}-${warning}`}>{warning}</p>
                ))}
              </article>
            )
          })}
          {sending && <p className="muted" role="status">Working on your request…</p>}
        </div>

        {saveNotice && <p className="muted" role="status">{saveNotice}</p>}
        {pendingAnalyticsSave && (
          <form className="conversation-composer" onSubmit={saveAnalyticsResult}>
            <label>
              Save this report to dashboard
              <select
                value={analyticsSaveTarget}
                onChange={(event) => setAnalyticsSaveTarget(event.target.value)}
              >
                {dashboardOptions.map((item) => (
                  <option key={item.id} value={item.id}>{item.title}</option>
                ))}
                <option value="__create_dashboard__">Create a new dashboard</option>
              </select>
            </label>
            {analyticsSaveTarget === '__create_dashboard__' && (
              <label>
                New dashboard name
                <input
                  value={newDashboardTitle}
                  onChange={(event) => setNewDashboardTitle(event.target.value)}
                  maxLength={120}
                  required
                />
              </label>
            )}
            <button
              className="button button-primary"
              type="submit"
              disabled={savingAnalytics || (
                analyticsSaveTarget === '__create_dashboard__'
                && !newDashboardTitle.trim()
              )}
            >
              {savingAnalytics ? 'Saving…' : 'Save verified report'}
            </button>
            <button
              className="button button-secondary"
              type="button"
              onClick={() => setPendingAnalyticsSave(null)}
              disabled={savingAnalytics}
            >
              Cancel
            </button>
          </form>
        )}

        <form className="conversation-composer" onSubmit={submitMessage}>
          <label className="visually-hidden" htmlFor="conversation-message">Your message</label>
          <textarea
            id="conversation-message"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={8000}
            placeholder={conversation ? 'Ask about your data…' : 'Start a conversation first…'}
            disabled={!conversation || sending}
            rows={3}
          />
          <button
            className="button button-primary"
            type="submit"
            disabled={!conversation || !draft.trim() || sending}
          >
            {sending ? 'Sending…' : 'Send'}
          </button>
        </form>
        <p className="conversation-disclaimer">Analysis results are deterministic where supported. Model-generated interpretations may be incomplete; verify results against source data.</p>
      </div>
    </section>
  )
}

export default ConversationWorkspace
