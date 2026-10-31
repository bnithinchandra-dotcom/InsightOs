import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createDashboard,
  createConversation,
  executeAnalyticsJourney,
  executeAnalyticsPlan,
  getAnalyticsCapabilities,
  getDashboards,
  getChatProviderStatus,
  getConversation,
  getProjectDatasets,
  getProjects,
  saveAnalyticsResultToDashboard,
  sendConversationMessage,
} from './api'
import { ChartCard } from './Dashboards'
import ConversationComposer from './ConversationComposer'
import DataExplorer from './DataExplorer'
import VisualizationCanvas from './VisualizationCanvas'
import { HistoryPage } from './WorkspacePages'

const MESSAGE_PAGE_SIZE = 25

const EXAMPLE_GOALS = [
  'Analyze my sales performance.',
  'Which products generate the most revenue?',
  'Why did revenue decrease?',
  "Forecast my next month's sales.",
]

const REPORT_TOOLS = new Set([
  'generate_evidence_report',
  'generate_eda_evidence_report',
])

function toVisualizationArtifact(id, tool, data, summary, references = [], warnings = []) {
  const charts = Array.isArray(data?.charts) ? data.charts : []
  const findings = Array.isArray(data?.findings) ? data.findings : []
  if (!charts.length && !(REPORT_TOOLS.has(tool) && findings.length)) return null

  const reference = references.find((item) => (
    item.type === 'analytics_workflow_result'
    && typeof item.workflow_id === 'string'
    && typeof item.task_id === 'string'
  ))
  return {
    id,
    kind: tool.replaceAll('_', ' '),
    title: typeof summary === 'string' && summary.trim()
      ? summary
      : tool.replaceAll('_', ' '),
    summary: typeof data?.summary === 'string' ? data.summary : '',
    charts,
    findings,
    warnings: Array.isArray(warnings) ? warnings : [],
    reference,
  }
}

function artifactsFromMessages(items) {
  return items.flatMap((message) => {
    if (message.role !== 'assistant') return []
    return (message.metadata?.structured_results ?? []).flatMap((result, index) => {
      if (result.status === 'failed' || result.status === 'blocked') return []
      const artifact = toVisualizationArtifact(
        `message-${message.id}-${index}`,
        result.tool ?? 'assistant result',
        result.data,
        result.summary,
        result.references,
        result.warnings,
      )
      return artifact ? [artifact] : []
    })
  })
}

function mergeArtifacts(current, incoming) {
  if (!incoming.length) return current
  const merged = new Map(current.map((artifact) => [artifact.id, artifact]))
  incoming.forEach((artifact) => merged.set(artifact.id, artifact))
  return [...merged.values()]
}

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

function ProviderBadge({ status, message }) {
  if (!status) return null
  const label = status.status.replaceAll('_', ' ')
  const cls = `provider-badge provider-${status.status}`
  return (
    <span className={cls} title={message ?? label}>
      <span className="provider-dot" />
      Model: {label}
    </span>
  )
}

function ToolActivityTimeline({ activities }) {
  if (!activities?.length) return null
  return (
    <div className="tool-timeline" aria-label="Agent steps">
      {activities.map((activity, index) => (
        <div className={`tool-activity tool-step tool-${activity.status}`} key={`${activity.tool_name}-${index}`}>
          <span className="tool-step-dot" aria-hidden="true" />
          <div className="tool-step-body">
            <strong>{activity.label}</strong>
            {activity.summary && <span>{activity.summary}</span>}
          </div>
        </div>
      ))}
    </div>
  )
}

function StructuredResultCard({ result, onSave, showCharts = true }) {
  const [expanded, setExpanded] = useState(false)
  const isReport = ['generate_evidence_report', 'generate_eda_evidence_report'].includes(result.tool)
  const saveRefs = isReport && result.status === 'succeeded'
    ? (result.references ?? []).filter(
        (reference) =>
          reference.type === 'analytics_workflow_result'
          && typeof reference.workflow_id === 'string'
          && typeof reference.task_id === 'string',
      )
    : []

  return (
    <details className="structured-chat-result result-card" open>
      <summary>{result.tool.replaceAll('_', ' ')} result</summary>
      {result.summary && <p className="result-card-summary">{result.summary}</p>}
      {showCharts && result.data?.charts?.map((chart) => (
        <ChartCard chart={chart} key={`${chart.title}-${chart.x_field}-${chart.y_field}`} />
      ))}
      {showCharts && result.data?.findings?.slice(0, 10).map((finding) => (
        <p className="result-finding" key={finding.id ?? finding.statement}>{finding.statement}</p>
      ))}
      {saveRefs.map((reference) => (
        <button
          className="button button-secondary result-save-btn"
          type="button"
          key={`${reference.workflow_id}-${reference.task_id}`}
          onClick={() => onSave(reference)}
        >
          Save verified report to dashboard
        </button>
      ))}
      {result.data !== null && result.data !== undefined && (
        <details className="result-technical" onToggle={(e) => setExpanded(e.target.open)}>
          <summary>{expanded ? 'Hide technical details' : 'Show technical details'}</summary>
          <pre>{JSON.stringify(result.data, null, 2)}</pre>
        </details>
      )}
    </details>
  )
}

function ConversationWorkspace({
  selectedConversationId = null,
  onNewConversation,
  onSelectConversation,
  onCloseHistory,
  historyOpenInitially = false,
  active = true,
}) {
  const [conversation, setConversation] = useState(null)
  const [messages, setMessages] = useState([])
  const [workspaceMode, setWorkspaceMode] = useState('conversation')
  const [workspaceArtifacts, setWorkspaceArtifacts] = useState([])
  const [activeVisualizationId, setActiveVisualizationId] = useState('')
  const [dataExplorerOpen, setDataExplorerOpen] = useState(true)
  const [selectedTable, setSelectedTable] = useState(null)
  const [mobilePanel, setMobilePanel] = useState('assistant')
  const [historyOpen, setHistoryOpen] = useState(false)
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
  const messagesEndRef = useRef(null)
  const composerInputRef = useRef(null)
  const requestedConversationId = useRef(null)
  const conversationRequestToken = useRef(0)

  useEffect(() => {
    if (!active) return undefined
    const controller = new AbortController()
    Promise.all([
      getChatProviderStatus(controller.signal),
      getProjects(controller.signal),
    ])
      .then(([status, projectItems]) => {
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
  }, [active])

  useEffect(() => {
    if (!active) return undefined
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
  }, [active])

  useEffect(() => {
    if (!active || !projectId) return undefined
    const controller = new AbortController()
    getProjectDatasets(projectId, controller.signal)
      .then((items) => setDatasets(items))
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') setError(requestError.message)
      })
    return () => controller.abort()
  }, [active, projectId])

  useEffect(() => {
    if (typeof messagesEndRef.current?.scrollIntoView === 'function' && messages.length > 0) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages])

  const openConversation = useCallback(async (id) => {
    const requestToken = ++conversationRequestToken.current
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
      if (requestToken !== conversationRequestToken.current) return
      setConversation(result)
      setMessages(page.messages ?? [])
      const artifacts = artifactsFromMessages(page.messages ?? [])
      setWorkspaceArtifacts(artifacts)
      setActiveVisualizationId(artifacts[artifacts.length - 1]?.id ?? '')
      setWorkspaceMode(artifacts.length ? 'visualization' : 'conversation')
      setSelectedTable(null)
      setMobilePanel(artifacts.length ? 'canvas' : 'assistant')
      setHasOlderMessages((page.offset ?? 0) > 0)
      setProjectId(result.project_id ? String(result.project_id) : '')
      setDatasetId(result.dataset_id ? String(result.dataset_id) : '')
    } catch (requestError) {
      if (requestToken === conversationRequestToken.current) setError(requestError.message)
    }
  }, [])

  useEffect(() => {
    if (
      active
      &&
      selectedConversationId
      && String(requestedConversationId.current) !== String(selectedConversationId)
    ) {
      requestedConversationId.current = selectedConversationId
      openConversation(selectedConversationId)
    }
  }, [active, openConversation, selectedConversationId])

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
      return created
    } catch (requestError) {
      setError(requestError.message)
      return null
    }
  }

  function startNewConversation() {
    conversationRequestToken.current += 1
    requestedConversationId.current = null
    setConversation(null)
    setMessages([])
    setWorkspaceMode('conversation')
    setWorkspaceArtifacts([])
    setActiveVisualizationId('')
    setSelectedTable(null)
    setMobilePanel('assistant')
    setHasOlderMessages(false)
    setDraft('')
    setError('')
    setSaveNotice('')
    setPendingAnalyticsSave(null)
    onNewConversation?.()
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
      const olderArtifacts = artifactsFromMessages(page.messages ?? [])
      setWorkspaceArtifacts((current) => mergeArtifacts(current, olderArtifacts))
      if (!workspaceArtifacts.length && olderArtifacts.length) {
        setActiveVisualizationId(olderArtifacts[olderArtifacts.length - 1].id)
        setWorkspaceMode('visualization')
      }
      setMessages((current) => [...page.messages, ...current])
      setHasOlderMessages(offset > 0)
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  async function submitMessage(event) {
    event.preventDefault()
    const content = draft.trim()
    if (!content || sending) return

    setSending(true)
    setError('')
    try {
      let activeConversation = conversation
      if (!activeConversation) {
        activeConversation = await startConversation()
        if (!activeConversation) return
      }

      const response = await sendConversationMessage(activeConversation.id, content)
      setConversation(response.conversation)
      const nextMessages = [
        ...messages,
        response.user_message,
        response.assistant_message,
      ]
      const artifacts = artifactsFromMessages([response.assistant_message])
      setWorkspaceArtifacts((current) => mergeArtifacts(current, artifacts))
      if (artifacts.length) {
        setActiveVisualizationId(artifacts[artifacts.length - 1].id)
        setWorkspaceMode('visualization')
        setMobilePanel('canvas')
      }
      setMessages(nextMessages.slice(-MESSAGE_PAGE_SIZE))
      setHasOlderMessages(
        nextMessages.length > MESSAGE_PAGE_SIZE || nextMessages[0].sequence > 1,
      )
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
      const artifact = toVisualizationArtifact(
        `workflow-${result.workflow_id}-${result.task_id}`,
        analyticsOperation,
        result.result,
        result.result?.title ?? analyticsOperation,
        result.references,
        result.warnings,
      )
      if (artifact) {
        setWorkspaceArtifacts((current) => mergeArtifacts(current, [artifact]))
        setActiveVisualizationId(artifact.id)
        setWorkspaceMode('visualization')
        setMobilePanel('canvas')
      }
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
      const artifacts = (result.operations ?? []).flatMap((operation) => {
        if (!operation.result) return []
        const artifact = toVisualizationArtifact(
          `plan-${result.plan_id}-${operation.operation_id}`,
          operation.operation,
          operation.result,
          operation.result.title ?? operation.operation.replaceAll('_', ' '),
          operation.result_reference
            ? [{
                type: 'analytics_workflow_result',
                workflow_id: operation.result_reference.workflow_id,
                task_id: operation.result_reference.task_id,
              }]
            : [],
          operation.warnings,
        )
        return artifact ? [artifact] : []
      })
      if (artifacts.length) {
        setWorkspaceArtifacts((current) => mergeArtifacts(current, artifacts))
        setActiveVisualizationId(artifacts[artifacts.length - 1].id)
        setWorkspaceMode('visualization')
        setMobilePanel('canvas')
      }
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

  const lastAssistant = [...messages].reverse().find((item) => item.role === 'assistant')
  const lastSteps = lastAssistant?.metadata?.tool_activities ?? []
  const stepsSucceeded = lastSteps.filter((step) => step.status === 'succeeded').length
  const stepsFailed = lastSteps.filter((step) => step.status === 'failed').length
  const providerLabel = providerStatus ? providerStatus.status.replaceAll('_', ' ') : 'checking'
  const showHistory = historyOpen || historyOpenInitially
  const closeHistory = () => {
    if (historyOpenInitially) onCloseHistory?.()
    setHistoryOpen(false)
  }

  if (!active) return null

  return (
    <section
      className={`conversation-workspace ${workspaceMode === 'visualization' ? 'is-visualization' : ''} ${workspaceMode === 'visualization' && !dataExplorerOpen ? 'is-data-hidden' : ''}`}
      data-mobile-panel={mobilePanel}
      aria-label="InsightOS assistant"
      aria-busy={loading}
    >
      <DataExplorer
        key={`scope-${projectId}-${datasetId}`}
        projects={projects}
        datasets={datasets}
        projectId={projectId}
        datasetId={datasetId}
        conversationActive={Boolean(conversation)}
        active={workspaceMode === 'visualization'}
        open={dataExplorerOpen}
        onProjectChange={(value) => {
          setProjectId(value)
          setDatasetId('')
          setSelectedTable(null)
        }}
        onDatasetChange={(value) => {
          setDatasetId(value)
          setSelectedTable(null)
        }}
        selectedTable={selectedTable}
        onSelectTable={(table) => {
          setSelectedTable(table)
          setWorkspaceMode('visualization')
          setMobilePanel('canvas')
        }}
        onClose={() => {
          setDataExplorerOpen(false)
          setMobilePanel('canvas')
        }}
      />
      {workspaceMode === 'visualization' && (
        <VisualizationCanvas
          artifacts={workspaceArtifacts}
          activeArtifactId={activeVisualizationId}
          onSelectArtifact={setActiveVisualizationId}
          selectedTable={selectedTable}
          conversationScopeFixed={Boolean(conversation)}
          onSave={(reference) => {
            setMobilePanel('assistant')
            beginAnalyticsSave(reference)
          }}
          onReturnToConversation={() => {
            setWorkspaceMode('conversation')
            setMobilePanel('assistant')
          }}
          onOpenDataExplorer={() => {
            setDataExplorerOpen(true)
            setMobilePanel('data')
          }}
          dataExplorerOpen={dataExplorerOpen}
        />
      )}
      <div className="conversation-main">
        <header className="conversation-header">
          <div className="assistant-workspace-identity">
            <span className="assistant-workspace-mark" aria-hidden="true">✦</span>
            <div className="assistant-workspace-heading">
              <p className="eyebrow">InsightOS · Business intelligence</p>
              {conversation && <h2>{conversation.title}</h2>}
            </div>
          </div>
          <div className="conversation-header-actions">
            <button
              className="conversation-history-button"
              type="button"
              onClick={() => setHistoryOpen(true)}
            >
              Conversation history
            </button>
            {(workspaceArtifacts.length > 0 || selectedTable) && workspaceMode === 'conversation' && (
              <button
                className="conversation-history-button"
                type="button"
                onClick={() => {
                  setWorkspaceMode('visualization')
                  setMobilePanel('canvas')
                }}
              >
                Open workspace
              </button>
            )}
            {providerStatus && (
              <span className={`provider-status provider-${providerStatus.status}`}>
                <span className="provider-dot" />
                {providerLabel}
              </span>
            )}
            <button className="new-conversation-button" type="button" onClick={startNewConversation}>
              <span aria-hidden="true">＋</span> New conversation
            </button>
          </div>
        </header>
        {providerStatus?.message && !['healthy', 'configured'].includes(providerStatus.status) && (
          <p className="provider-notice" role="status">{providerStatus.message}</p>
        )}
        {error && <p className="conversation-error" role="alert">{error}</p>}
        {saveNotice && <p className="save-notice" role="status">{saveNotice}</p>}

        <div className="conversation-messages" aria-live="polite">
          {hasOlderMessages && (
            <button className="text-button" type="button" onClick={loadOlderMessages}>
              Load older messages
            </button>
          )}
          {!conversation && (
            <div className="assistant-welcome">
              <div className="assistant-hero">
                <span className="assistant-hero-mark" aria-hidden="true">✦</span>
                <p className="assistant-welcome-kicker">InsightOS · Business intelligence</p>
                <h1>Your business has a story. Let&apos;s uncover it.</h1>
                <p className="assistant-welcome-description">
                  Ask questions about your business, explore your data, and discover insights that help you make better decisions.
                </p>
              </div>
              <div className="assistant-example-goals" aria-label="Example goals">
                {EXAMPLE_GOALS.map((goal) => (
                  <button
                    key={goal}
                    className="example-goal-chip"
                    type="button"
                    onClick={() => {
                      setDraft(goal)
                      composerInputRef.current?.focus()
                    }}
                  >
                    {goal}
                  </button>
                ))}
              </div>
            </div>
          )}
          {conversation && messages.length === 0 && (
            <div className="conversation-empty conversation-empty-active">
              <p>What would you like to understand about your business?</p>
              <div className="assistant-example-goals" aria-label="Example goals">
                {EXAMPLE_GOALS.map((goal) => (
                  <button
                    key={goal}
                    className="example-goal-chip"
                    type="button"
                    onClick={() => {
                      setDraft(goal)
                      composerInputRef.current?.focus()
                    }}
                  >
                    {goal}
                  </button>
                ))}
              </div>
            </div>
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
                <p className="chat-role"><span className="chat-avatar" aria-hidden="true">{message.role === 'assistant' ? '✦' : 'Y'}</span>{message.role === 'assistant' ? 'InsightOS' : 'You'}</p>
                <p
                  className={`chat-content ${isFailedAssistantMessage ? 'chat-error' : ''}`}
                  role={isFailedAssistantMessage ? 'alert' : undefined}
                >
                  {content}
                </p>
                <ToolActivityTimeline activities={message.metadata?.tool_activities} />
                {message.metadata?.structured_results?.map((result, index) => (
                  <StructuredResultCard
                    key={`${result.tool}-${index}`}
                    result={result}
                    onSave={beginAnalyticsSave}
                    showCharts={workspaceMode !== 'visualization'}
                  />
                ))}
                {message.metadata?.warnings?.map((warning, index) => (
                  <p className="chat-warning" key={`${index}-${warning}`}>{warning}</p>
                ))}
              </article>
            )
          })}
          {sending && (
            <div className="sending-indicator" role="status">
              <span className="sending-dot" /><span className="sending-dot" /><span className="sending-dot" />
              <span>Working on your request…</span>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

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

        <ConversationComposer
          draft={draft}
          sending={sending}
          onDraftChange={setDraft}
          onSubmit={submitMessage}
          inputRef={composerInputRef}
          contextControls={(
            <div className="composer-data-context" aria-label="Data context">
              <label className="context-select">
                <span>Project</span>
                <select
                  aria-label="Project context"
                  value={projectId}
                  onChange={(event) => {
                    setProjectId(event.target.value)
                    setDatasetId('')
                    setSelectedTable(null)
                  }}
                  disabled={sending || Boolean(conversation)}
                >
                  <option value="">All available projects</option>
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>{project.name}</option>
                  ))}
                </select>
              </label>
              {projectId && (
                <label className="context-select">
                  <span>Dataset</span>
                  <select
                    aria-label="Dataset context"
                    value={datasetId}
                    onChange={(event) => {
                      setDatasetId(event.target.value)
                      setSelectedTable(null)
                    }}
                    disabled={sending || Boolean(conversation)}
                  >
                    <option value="">All datasets in project</option>
                    {datasets.map((dataset) => (
                      <option key={dataset.id} value={dataset.id}>{dataset.name}</option>
                    ))}
                  </select>
                </label>
              )}
              {conversation && (
                <span className="conversation-scope-locked" role="note">
                  Scope fixed for this conversation; start a new one to change it.
                </span>
              )}
            </div>
          )}
        />
        <p className="conversation-disclaimer">
          Analysis results are deterministic where supported. Model-generated interpretations may be incomplete; verify results against source data.
        </p>

        <details className="assistant-advanced">
          <summary>Advanced options &amp; run details</summary>
          <div className="assistant-advanced-content">
        <details className="advanced-analytics-panel">
          <summary>Advanced analytics</summary>
          <div className="advanced-analytics-body">
            <section className="structured-chat-result" aria-label="Unified analytics workflow" style={{ border: 'none', padding: 0 }}>
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
                        {analyticsExecution.provenance?.operation_id}
                      </p>
                      {analyticsExecution.result.summary && (
                        <details className="result-technical">
                          <summary>Summary</summary>
                          <pre>{JSON.stringify(analyticsExecution.result.summary, null, 2)}</pre>
                        </details>
                      )}
                      {workspaceMode !== 'visualization' && analyticsExecution.result.charts?.map((chart) => (
                        <ChartCard
                          chart={chart}
                          key={`${chart.title}-${chart.x_field}-${chart.y_field}`}
                        />
                      ))}
                      {workspaceMode !== 'visualization' && analyticsExecution.result.findings?.slice(0, 20).map((finding) => (
                        <p key={finding.id}>{finding.statement}</p>
                      ))}
                      {analyticsExecution.warnings?.map((warning, index) => (
                        <p className="chat-warning" key={`${warning}-${index}`}>{warning}</p>
                      ))}
                    </>
                  )}
                </div>
              )}
            </section>
          </div>
        </details>

        <details className="advanced-analytics-panel">
          <summary>Advanced: Multi-operation analytics plan</summary>
          <div className="advanced-analytics-body">
            <section className="structured-chat-result" aria-label="Multi-operation analytics plan" style={{ border: 'none', padding: 0 }}>
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
                  placeholder={'{"operations":[{"operation_id":"report","operation":"generate_evidence_report","parameters":{},"depends_on":[]}]}'}
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
                          {workspaceMode !== 'visualization' && operation.result.charts?.map((chart) => (
                            <ChartCard
                              chart={chart}
                              key={`${chart.title}-${chart.x_field}-${chart.y_field}`}
                            />
                          ))}
                          {workspaceMode !== 'visualization' && operation.result.findings?.slice(0, 20).map((finding) => (
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
          </div>
        </details>
      <details className="advanced-agent-details">
        <summary>Assistant details</summary>
        <aside className="agent-rail" aria-label="Advanced assistant details">
        <section className="rail-section">
          <h3>Data boundary</h3>
          <p className="rail-hint">The agent only reads data inside this scope.</p>
        </section>

        <section className="rail-section">
          <h3>Current run</h3>
          {sending ? (
            <p className="rail-run is-active"><span className="sending-dot" /> Executing tools…</p>
          ) : lastSteps.length ? (
            <p className="rail-run">
              {lastSteps.length} step{lastSteps.length === 1 ? '' : 's'}
              {' · '}{stepsSucceeded} ok{stepsFailed ? ` · ${stepsFailed} failed` : ''}
            </p>
          ) : (
            <p className="rail-hint">No tools have run in this conversation yet.</p>
          )}
        </section>

        <section className="rail-section">
          <h3>Verified tools</h3>
          {capabilityError ? (
            <p className="rail-hint">Live tool list unavailable until a workspace profile exists.</p>
          ) : analyticsCapabilities.length === 0 ? (
            <p className="rail-hint">Loading registered tools…</p>
          ) : (
            <ul className="rail-tools">
              {analyticsCapabilities.slice(0, 8).map((capability) => (
                <li key={capability.operation} title={capability.description}>
                  <span className="rail-tool-dot" aria-hidden="true" />
                  {capability.operation.replaceAll('_', ' ')}
                </li>
              ))}
            </ul>
          )}
          <p className="rail-hint">Typed arguments, bounded results, no shell or filesystem access.</p>
        </section>

        <section className="rail-section">
          <h3>Model</h3>
          <ProviderBadge status={providerStatus} message={providerStatus?.message} />
          <p className="rail-hint">Model tool selection is unverified. Data-workspace tools work without a model.</p>
        </section>
      </aside>
      </details>
          </div>
        </details>
      </div>
      {workspaceMode === 'visualization' && (
        <nav className="adaptive-mobile-panel-switcher" aria-label="Workspace panels">
          {[
            { id: 'data', label: 'Data' },
            { id: 'canvas', label: 'Canvas' },
            { id: 'assistant', label: 'Assistant' },
          ].map((panel) => (
            <button
              type="button"
              key={panel.id}
              className={mobilePanel === panel.id ? 'is-active' : ''}
              aria-pressed={mobilePanel === panel.id}
              onClick={() => {
                if (panel.id === 'data') setDataExplorerOpen(true)
                setMobilePanel(panel.id)
              }}
            >
              {panel.label}
            </button>
          ))}
        </nav>
      )}
      {showHistory && (
        <div className="adaptive-history-overlay">
          <button
            className="adaptive-history-scrim"
            type="button"
            aria-label="Close conversation history"
            onClick={closeHistory}
          />
          <section
            className="adaptive-history-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="Conversation history"
          >
            <button
              className="adaptive-history-close"
              type="button"
              onClick={closeHistory}
              aria-label="Close conversation history"
            >
              ×
            </button>
            <HistoryPage
              onSelectConversation={(id) => {
                onSelectConversation?.(id)
                if (!onSelectConversation) {
                  requestedConversationId.current = String(id)
                  openConversation(id)
                }
                closeHistory()
              }}
            />
          </section>
        </div>
      )}
    </section>
  )
}

export default ConversationWorkspace
