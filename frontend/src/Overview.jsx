import { useEffect, useState } from 'react'
import {
  getAnalyticsCapabilities,
  getChatProviderStatus,
  getConversations,
  getDashboards,
  getProjects,
} from './api'

// Everything shown here is derived from live backend responses or from the
// capability boundaries documented in the README. Nothing is simulated.

const STAGE_ICONS = {
  connect: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" /><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></>,
  understand: <><rect x="3" y="4" width="7" height="6" rx="1.5" /><rect x="14" y="14" width="7" height="6" rx="1.5" /><path d="M10 7h4a3 3 0 0 1 3 3v4" /></>,
  analyze: <><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /><path d="m19 16 .9 2.1L22 19l-2.1.9L19 22l-.9-2.1L16 19l2.1-.9z" /></>,
  insights: <><path d="M4 19V5M4 19h17" /><path d="m7 15 4-4 3 2 6-7" /></>,
}

function StageIcon({ name }) {
  return (
    <svg className="icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {STAGE_ICONS[name]}
    </svg>
  )
}

const PLANNED = [
  'Database and REST connectors',
  'ZIP and folder ingestion',
  'Alerts',
  'Authentication and multi-user access',
]

const FALLBACK_CAPABILITIES = [
  { operation: 'univariate_eda', description: 'Deterministic univariate analysis of a verified normalized table.' },
  { operation: 'quality_analysis', description: 'Versioned, advisory data-quality reports.' },
  { operation: 'evidence_report', description: 'Multi-dataset reports with explicit join keys, charts and provenance.' },
  { operation: 'monthly_forecast', description: 'Bounded linear-trend forecast with chronological holdout.' },
]

function label(value) {
  return String(value ?? '').replaceAll('_', ' ')
}

function settle(result) {
  return result.status === 'fulfilled'
    ? { data: result.value, error: null }
    : { data: null, error: result.reason }
}

function useWorkspaceSnapshot() {
  const [snapshot, setSnapshot] = useState({ loading: true })

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    Promise.allSettled([
      getProjects(signal),
      getDashboards(signal),
      getConversations({ limit: 5, signal }),
      getChatProviderStatus(signal),
      getAnalyticsCapabilities(signal),
    ]).then(([projects, dashboards, conversations, provider, capabilities]) => {
      if (signal.aborted) return
      setSnapshot({
        loading: false,
        projects: settle(projects),
        dashboards: settle(dashboards),
        conversations: settle(conversations),
        provider: settle(provider),
        capabilities: settle(capabilities),
      })
    })
    return () => controller.abort()
  }, [])

  return snapshot
}

function nextAction(snapshot) {
  const projectError = snapshot.projects?.error
  if (projectError?.code === 'workspace_profile_required') {
    return {
      title: 'Create your local workspace profile',
      body: 'A one-time local profile lets InsightOS scope projects, conversations and dashboards to you.',
      cta: 'Open Projects',
      href: '#projects',
    }
  }
  if (projectError) {
    return {
      title: 'Backend is not reachable',
      body: 'Start the backend (docker compose up) and refresh. System status shows which service is down.',
      cta: 'View system status',
      href: '#system',
    }
  }
  const projects = snapshot.projects?.data ?? []
  const conversations = snapshot.conversations?.data ?? []
  const dashboards = snapshot.dashboards?.data ?? []
  if (projects.length === 0) {
    return {
      title: 'Create your first project',
      body: 'Projects group datasets, analysis and dashboards. It takes one field.',
      cta: 'Create a project',
      href: '#projects',
    }
  }
  if (conversations.length === 0) {
    return {
      title: 'Upload data, normalize it, then ask the assistant',
      body: 'Add a CSV, XLSX, JSON, Parquet or XML file. Once normalized, you can run EDA or ask questions about it.',
      cta: 'Open Data workspace',
      href: '#datasets',
    }
  }
  if (dashboards.length === 0) {
    return {
      title: 'Turn a verified result into a dashboard',
      body: 'Run an evidence report or forecast, then save it. Dashboards keep a reference to the verified run.',
      cta: 'Open Assistant',
      href: '#chat',
    }
  }
  return {
    title: 'Pick up where you left off',
    body: 'Continue a conversation or refresh a dashboard against current data.',
    cta: 'Open Assistant',
    href: '#chat',
  }
}

function Badge({ tone, children }) {
  return <span className={`ov-badge ov-badge-${tone}`}>{children}</span>
}

function countText(result, noun) {
  if (!result || result.error) return null
  const count = result.data.length
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

export default function Overview({ health, loading: statusLoading, error: statusError }) {
  const snapshot = useWorkspaceSnapshot()
  const action = snapshot.loading ? null : nextAction(snapshot)
  const provider = snapshot.provider?.data
  const operations = snapshot.capabilities?.data?.operations
  const capabilities = Array.isArray(operations) && operations.length > 0
    ? operations
    : FALLBACK_CAPABILITIES
  const capabilitiesLive = capabilities === operations
  const recent = snapshot.conversations?.data ?? []

  const providerReady = provider && ['configured', 'healthy'].includes(provider.status)

  const stages = [
    {
      id: 'connect', icon: 'connect', step: '01', href: '#datasets',
      title: 'Connect and ingest',
      body: 'Upload CSV, TSV, XLSX, JSON, Parquet or XML. Files are validated, profiled and normalized to verified Parquet.',
      status: countText(snapshot.projects, 'project'),
      tone: 'live', badge: 'Files live',
      note: 'Databases and REST sources are planned.',
    },
    {
      id: 'understand', icon: 'understand', step: '02', href: '#analysis',
      title: 'Understand and model',
      body: 'Deterministic profiling, quality reports and Schema Architect relationship and star-schema recommendations.',
      status: 'Evidence-backed, advisory',
      tone: 'partial', badge: 'Partial',
      note: 'Business semantics stay unconfirmed until you review them.',
    },
    {
      id: 'analyze', icon: 'analyze', step: '03', href: '#chat', featured: true,
      title: 'Agentic analytics',
      body: 'Ask in plain language. The assistant runs allow-listed deterministic tools and shows each step and its evidence.',
      status: provider ? `Model ${label(provider.status)}` : 'Model status unavailable',
      tone: providerReady ? 'live' : 'partial', badge: providerReady ? 'Ready' : 'Needs model',
      note: providerReady
        ? 'Tool selection by the model is still unverified.'
        : 'Set INSIGHTOS_LLM_PROVIDER to enable chat. Data-workspace tools work without it.',
    },
    {
      id: 'insights', icon: 'insights', step: '04', href: '#dashboards',
      title: 'Insights and outcomes',
      body: 'Evidence reports, monthly forecasts and dashboards that reference the original verified run.',
      status: countText(snapshot.dashboards, 'dashboard'),
      tone: 'partial', badge: 'Partial',
      note: 'Alerts are planned.',
    },
  ]

  return (
    <div className="ov">
      <section className="ov-hero">
        <div className="ov-hero-copy">
          <p className="ov-kicker">InsightOS command center</p>
          <h2>From raw files to evidence-backed answers.</h2>
          <p>
            Bring data in, understand its structure, then ask questions. Every result
            carries its source, method and provenance, so you can check it.
          </p>
        </div>
        <div className="ov-next" aria-live="polite">
          <p className="ov-kicker">Next best step</p>
          {snapshot.loading ? (
            <p className="ov-muted">Checking your workspace…</p>
          ) : (
            <>
              <h3>{action.title}</h3>
              <p>{action.body}</p>
              <a className="ov-cta" href={action.href}>{action.cta} <span aria-hidden="true">→</span></a>
            </>
          )}
        </div>
      </section>

      <section aria-label="Workflow">
        <div className="ov-section-head">
          <h2>Your workflow</h2>
          <p>Work left to right. Each stage uses the output of the one before it.</p>
        </div>
        <ol className="ov-stages">
          {stages.map((stage) => (
            <li key={stage.id} className={`ov-stage ${stage.featured ? 'is-featured' : ''}`}>
              <a href={stage.href} className="ov-stage-link">
                <div className="ov-stage-top">
                  <span className="ov-stage-icon"><StageIcon name={stage.icon} /></span>
                  <span className="ov-step">{stage.step}</span>
                </div>
                <h3>{stage.title}</h3>
                <p>{stage.body}</p>
                <div className="ov-stage-foot">
                  <Badge tone={stage.tone}>{stage.badge}</Badge>
                  {stage.status && <span className="ov-stage-status">{stage.status}</span>}
                </div>
                <small>{stage.note}</small>
              </a>
            </li>
          ))}
        </ol>
      </section>

      <div className="ov-columns">
        <section className="ov-card" aria-label="What the agent can do">
          <div className="ov-card-head">
            <div>
              <h2>What the agent can do</h2>
              <p>{capabilitiesLive ? 'Registered operations reported by your backend.' : 'Core operations. The live list loads once your workspace profile exists.'}</p>
            </div>
            <Badge tone={capabilitiesLive ? 'live' : 'partial'}>{capabilitiesLive ? 'Live' : 'Documented'}</Badge>
          </div>
          <ul className="ov-cap-list">
            {capabilities.slice(0, 6).map((capability) => (
              <li key={capability.operation}>
                <strong>{label(capability.operation)}</strong>
                <span>{capability.description}</span>
              </li>
            ))}
          </ul>
          <p className="ov-guardrail">
            No arbitrary code, SQL, shell or filesystem access. Every tool has typed arguments and bounded results.
          </p>
        </section>

        <div className="ov-stack">
          <section className="ov-card" aria-label="Recent conversations">
            <div className="ov-card-head">
              <div><h2>Recent conversations</h2></div>
              <a className="ov-link" href="#chat">Open →</a>
            </div>
            {recent.length === 0 ? (
              <p className="ov-muted">No conversations yet. Your assistant history will appear here.</p>
            ) : (
              <ul className="ov-recent">
                {recent.map((item) => (
                  <li key={item.id}><a href="#chat">{item.title}</a><small>{label(item.status)}</small></li>
                ))}
              </ul>
            )}
          </section>

          <section className="ov-card" aria-label="Not available yet">
            <div className="ov-card-head"><div><h2>Not available yet</h2></div></div>
            <ul className="ov-planned">
              {PLANNED.map((item) => <li key={item}><Badge tone="planned">Planned</Badge>{item}</li>)}
            </ul>
          </section>
        </div>
      </div>

      <div className="ov-statusbar" role="status">
        <span className={`status-dot dot-${statusLoading ? 'loading' : health}`} />
        {statusError
          ? 'Unable to connect to the InsightOS backend.'
          : statusLoading
            ? 'Checking system status'
            : health === 'healthy' ? 'All services operational' : 'One or more services need attention'}
        <a className="ov-link" href="#system">System details →</a>
      </div>
    </div>
  )
}
