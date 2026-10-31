import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { getHealth, getSystemInfo } from './api'
import BrowserExecution from './BrowserExecution'
import ConversationWorkspace from './ConversationWorkspace'
import DataWorkspace from './DataWorkspace'
import Dashboards from './Dashboards'
import Overview from './Overview'
import SchemaArchitect from './SchemaArchitect'
import './App.css'
import './redesign.css'

const navigationGroups = [
  {
    label: null,
    items: [{ id: 'overview', label: 'Overview', icon: 'grid' }],
  },
  {
    label: 'Workflow',
    items: [
      { id: 'projects', label: 'Projects', icon: 'folder', step: '1', stage: 'Connect' },
      { id: 'datasets', label: 'Data', icon: 'database', step: '1', stage: 'Connect' },
      { id: 'analysis', label: 'Schema Architect', icon: 'activity', step: '2', stage: 'Understand' },
      { id: 'chat', label: 'Assistant', icon: 'spark', step: '3', stage: 'Analyze' },
      { id: 'dashboards', label: 'Dashboards', icon: 'chart', step: '4', stage: 'Insights' },
    ],
  },
  {
    label: 'System',
    items: [
      { id: 'system', label: 'System', icon: 'pulse' },
      { id: 'settings', label: 'Settings', icon: 'settings' },
    ],
  },
]

const stageByPage = Object.fromEntries(
  navigationGroups.flatMap((group) => group.items).filter((item) => item.stage).map((item) => [item.id, item.stage]),
)

const services = [
  { key: 'backend', label: 'Backend' },
  { key: 'database', label: 'Database' },
  { key: 'redis', label: 'Redis' },
  { key: 'storage', label: 'Storage' },
]

const pageDetails = {
  chat: {
    title: 'Assistant',
    description: 'Work with your existing projects and deterministic data-analysis capabilities.',
  },
  overview: {
    title: 'Overview',
    description: 'Your workspace for turning data into decisions.',
  },
  projects: {
    title: 'Projects',
    description: 'Organize datasets, analysis, and dashboards in one place.',
  },
  datasets: {
    title: 'Data Workspace',
    description: 'Bring your data into an InsightOS project and prepare it for analysis.',
  },
  data: {
    title: 'Data Workspace',
    description: 'Bring your data into an InsightOS project and prepare it for analysis.',
  },
  analysis: {
    title: 'Schema Architect',
    description: 'Inspect normalized dataset tables and review evidence-backed model recommendations.',
  },
  dashboards: {
    title: 'Dashboards',
    description: 'Build interactive analytical views from your data and insights.',
  },
  system: {
    title: 'System',
    description: 'Infrastructure status and application environment.',
  },
  settings: {
    title: 'Settings',
    description: 'Application preferences and configuration.',
  },
}

function subscribeToHash(callback) {
  window.addEventListener('hashchange', callback)
  return () => window.removeEventListener('hashchange', callback)
}

function getCurrentHash() {
  return window.location.hash.slice(1).split('?')[0].split('/')[0]
}

async function fetchSystemStatus(signal) {
  const [healthResult, infoResult] = await Promise.all([
    getHealth(signal),
    getSystemInfo(signal),
  ])
  return {
    health: healthResult?.status === 'healthy' ? 'healthy' : 'unhealthy',
    systemInfo: infoResult,
  }
}

function Icon({ name, size = 18 }) {
  const paths = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    folder: <><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h5l2 2h8A1.5 1.5 0 0 1 21 9.5v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z" /><path d="M3 10h18" /></>,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" /><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></>,
    activity: <><path d="M3 12h4l3-8 4 16 3-8h4" /></>,
    chart: <><path d="M4 19V5M4 19h17" /><path d="m7 15 4-4 3 2 6-7" /></>,
    pulse: <><path d="M3 12h4l3-8 4 16 3-8h4" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.6a8 8 0 0 1-1.6.9l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.6-.9l-1.7.6-1.4-2.4 1.4-1.1a7 7 0 0 1 0-1.9l-1.4-1.1 1.4-2.4 1.7.6a8 8 0 0 1 1.6-.9l.3-1.8h2.8l.3 1.8a8 8 0 0 1 1.6.9l1.7-.6 1.4 2.4-1.4 1.1a7 7 0 0 1-.1 1.8z" transform="translate(-1 -1)" /></>,
    arrow: <><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></>,
    refresh: <><path d="M20 7v5h-5" /><path d="M4 17v-5h5" /><path d="M5.6 9a7 7 0 0 1 11.6-2L20 12M4 12l2.8 5a7 7 0 0 0 11.6-2" /></>,
    spark: <><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /><path d="m19 16 .9 2.1L22 19l-2.1.9L19 22l-.9-2.1L16 19l2.1-.9z" /></>,
  }

  return (
    <svg
      aria-hidden="true"
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  )
}

function statusLabel(value) {
  if (value === 'healthy') return 'Healthy'
  if (value === 'unhealthy') return 'Unhealthy'
  if (value === 'loading') return 'Loading'
  if (value === 'error') return 'Error'
  return 'Unavailable'
}

function StatusPill({ status }) {
  return (
    <span className={`status-pill status-${status}`} role="status">
      <span className="status-dot" />
      {statusLabel(status)}
    </span>
  )
}

function PageHeader({ title, description, stage, children }) {
  return (
    <div className="page-header">
      <div>
        <p className="eyebrow">{stage ? `Stage · ${stage}` : 'Workspace'}</p>
        <h1>{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      {children}
    </div>
  )
}

function SystemStatus({ health, systemInfo, loading, error, onRefresh, compact = false }) {
  const serviceStatus = (key) => {
    if (loading) return 'loading'
    if (error || !systemInfo) return 'unavailable'
    const value = systemInfo.services?.[key]
    if (value === 'healthy') return 'healthy'
    if (value === 'unhealthy') return 'unhealthy'
    return 'unavailable'
  }

  if (compact) {
    return (
      <div className="compact-status">
        {error ? (
          <span className="backend-error">Unable to connect to InsightOS backend.</span>
        ) : (
          <span className="compact-status-label">Infrastructure</span>
        )}
        <div className="compact-service-list">
          {services.map((service) => (
            <span key={service.key} className="compact-service">
              <span className={`status-dot dot-${serviceStatus(service.key)}`} />
              {service.label}
            </span>
          ))}
        </div>
        <a className="text-link" href="#system">View system <Icon name="arrow" size={14} /></a>
      </div>
    )
  }

  return (
    <section className="panel system-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Infrastructure</p>
          <h2>System status</h2>
        </div>
        <button className="icon-button" type="button" onClick={onRefresh} aria-label="Refresh system status">
          <Icon name="refresh" />
        </button>
      </div>
      {error && <p className="backend-error">{error}</p>}
      <div className="system-service-grid">
        {services.map((service) => {
          const status = service.key === 'backend'
            ? health
            : serviceStatus(service.key)
          return (
            <div className="system-service" key={service.key}>
              <span>{service.label}</span>
              <StatusPill status={status} />
            </div>
          )
        })}
      </div>
      {systemInfo && !error && (
        <div className="system-meta">
          <span>{systemInfo.application?.name ?? 'InsightOS'}</span>
          <span>v{systemInfo.application?.version ?? 'Unavailable'}</span>
          <span>{systemInfo.application?.environment ?? 'Unavailable'}</span>
        </div>
      )}
    </section>
  )
}

function SystemPage({ health, systemInfo, loading, error, onRefresh }) {
  return (
    <div className="system-page">
      <section className="panel app-info-panel">
        <div>
          <p className="eyebrow">Application</p>
          <h2>{systemInfo?.application?.name ?? 'InsightOS'}</h2>
          <p className="muted">Application metadata reported by the backend.</p>
        </div>
        <div className="app-info-values">
          <div><span>Version</span><strong>{systemInfo?.application?.version ?? 'Unavailable'}</strong></div>
          <div><span>Environment</span><strong>{systemInfo?.application?.environment ?? 'Unavailable'}</strong></div>
        </div>
      </section>
      <SystemStatus
        health={health}
        systemInfo={systemInfo}
        loading={loading}
        error={error}
        onRefresh={onRefresh}
      />
      <BrowserExecution />
    </div>
  )
}

function SettingsPage({ systemInfo }) {
  return (
    <div className="settings-grid">
      <section className="panel settings-section">
        <p className="eyebrow">Appearance</p>
        <h2>Theme</h2>
        <div className="settings-row"><span>Color theme</span><strong>Dark</strong></div>
      </section>
      <section className="panel settings-section">
        <p className="eyebrow">Application</p>
        <h2>Environment</h2>
        <div className="settings-row"><span>Version</span><strong>{systemInfo?.application?.version ?? 'Unavailable'}</strong></div>
        <div className="settings-row"><span>Environment</span><strong>{systemInfo?.application?.environment ?? 'Unavailable'}</strong></div>
      </section>
      <section className="panel settings-section">
        <p className="eyebrow">Account</p>
        <h2>Authentication</h2>
        <p className="muted">Account and authentication settings will be introduced in a later foundation stage.</p>
        <span className="subtle-badge">Not configured</span>
      </section>
    </div>
  )
}

function PageContent({ page, props }) {
  if (page === 'chat') return <ConversationWorkspace />
  if (page === 'overview') return <Overview {...props} />
  if (page === 'projects' || page === 'datasets' || page === 'data') {
    return <DataWorkspace page={page === 'projects' ? 'projects' : 'datasets'} />
  }
  if (page === 'analysis') {
    return <SchemaArchitect />
  }
  if (page === 'dashboards') return <Dashboards />
  if (page === 'system') return <SystemPage {...props} />
  return <SettingsPage systemInfo={props.systemInfo} />
}

function App() {
  const currentHash = useSyncExternalStore(subscribeToHash, getCurrentHash, () => '')
  const page = pageDetails[currentHash] ? currentHash : 'overview'
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [health, setHealth] = useState('loading')
  const [systemInfo, setSystemInfo] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const refreshStatus = useCallback(async (signal) => {
    setLoading(true)
    setError('')
    setHealth('loading')
    try {
      const result = await fetchSystemStatus(signal)
      setHealth(result.health)
      setSystemInfo(result.systemInfo)
    } catch (requestError) {
      if (requestError.name !== 'AbortError') {
        setError('Unable to connect to InsightOS backend.')
        setHealth('unavailable')
        setSystemInfo(null)
      }
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    fetchSystemStatus(controller.signal)
      .then((result) => {
        setHealth(result.health)
        setSystemInfo(result.systemInfo)
      })
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') {
          setError('Unable to connect to InsightOS backend.')
          setHealth('unavailable')
          setSystemInfo(null)
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  const navigate = () => {
    setMobileNavOpen(false)
  }

  const details = pageDetails[page] ?? pageDetails.overview
  const navProps = (item) => ({
    href: `#${item.id}`,
    className: `nav-link ${page === item.id ? 'active' : ''}`,
    onClick: () => navigate(item.id),
    'aria-current': page === item.id ? 'page' : undefined,
  })
  const pageProps = { health, systemInfo, loading, error, onRefresh: () => refreshStatus() }

  return (
    <div className="app-layout">
      <button
        className={`mobile-scrim ${mobileNavOpen ? 'visible' : ''}`}
        type="button"
        aria-label="Close navigation"
        onClick={() => setMobileNavOpen(false)}
      />
      <aside className={`sidebar ${mobileNavOpen ? 'open' : ''}`}>
        <a className="brand" href="#overview" onClick={() => navigate('overview')}>
          <span className="brand-mark">I</span>
          <span><strong>InsightOS</strong><small>Data intelligence</small></span>
        </a>
        <div className="workspace-switcher">
          <span className="workspace-avatar">W</span>
          <span><strong>My workspace</strong><small>Personal workspace</small></span>
          <span className="switcher-caret">⌄</span>
        </div>
        <nav className="navigation" aria-label="Main navigation">
          {navigationGroups.map((group, index) => (
            <div className="nav-group" key={group.label ?? index}>
              {group.label && <span className="nav-group-label">{group.label}</span>}
              {group.items.map((item) => (
                <a key={item.id} {...navProps(item)}>
                  <Icon name={item.icon} />
                  <span>{item.label}</span>
                  {item.step && <span className="nav-step" aria-hidden="true">{item.step}</span>}
                </a>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className={`status-dot dot-${health}`} />
          <span>{health === 'healthy' ? 'All systems operational' : health === 'loading' ? 'Checking system status' : 'System status unavailable'}</span>
        </div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <button className="mobile-menu-button" type="button" onClick={() => setMobileNavOpen(true)} aria-label="Open navigation">
            <span /><span /><span />
          </button>
          <div className="breadcrumb">
            {stageByPage[page] ? <span className="stage-chip">{stageByPage[page]}</span> : <span>Workspace</span>}
            <span className="breadcrumb-divider">/</span><strong>{details.title}</strong>
          </div>
          <div className="topbar-actions">
            <a className="topbar-system-link" href="#system" onClick={() => navigate('system')}>
              <StatusPill status={health} />
            </a>
            <button className="account-placeholder" type="button" disabled aria-label="Account settings not configured" title="Authentication is not configured">
              <span className="account-avatar">N</span>
            </button>
          </div>
        </header>

        <main className="main-content">
          {page !== 'overview' && (
          <PageHeader title={details.title} description={details.description} stage={stageByPage[page]}>
            {page === 'system' && (
              <button className="button button-secondary" type="button" onClick={pageProps.onRefresh}>
                <Icon name="refresh" size={16} /> Refresh status
              </button>
            )}
          </PageHeader>
          )}
          <PageContent page={page} props={{ ...pageProps, navigate }} />
        </main>
        <footer className="app-footer">
          <span>InsightOS</span><span>Foundation stage</span>
        </footer>
      </div>
    </div>
  )
}

export default App
