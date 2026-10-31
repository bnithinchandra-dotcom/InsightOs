import { useEffect, useRef, useState } from 'react'
import { getAnalyticsCapabilities, getChatProviderStatus, getConversations } from './api'

const HISTORY_PAGE_SIZE = 25

function PageState({ loading, error, children }) {
  if (loading) return <p className="workspace-page-state" role="status">Loading workspace…</p>
  if (error) return <p className="workspace-page-error" role="alert">{error}</p>
  return children
}

export function HistoryPage({ onSelectConversation }) {
  const [conversations, setConversations] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [paginationError, setPaginationError] = useState('')
  const [hasMore, setHasMore] = useState(false)
  const paginationController = useRef(null)

  useEffect(() => {
    const controller = new AbortController()
    getConversations({ limit: HISTORY_PAGE_SIZE, signal: controller.signal })
      .then((items) => {
        setConversations(items)
        setHasMore(items.length === HISTORY_PAGE_SIZE)
      })
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') setError(requestError.message)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => {
      controller.abort()
      paginationController.current?.abort()
    }
  }, [])

  async function loadMoreConversations() {
    if (loadingMore || !hasMore) return
    const controller = new AbortController()
    paginationController.current = controller
    setLoadingMore(true)
    setPaginationError('')
    try {
      const items = await getConversations({
        limit: HISTORY_PAGE_SIZE,
        offset: conversations.length,
        signal: controller.signal,
      })
      setConversations((current) => [...current, ...items])
      setHasMore(items.length === HISTORY_PAGE_SIZE)
    } catch (requestError) {
      if (requestError.name !== 'AbortError') setPaginationError(requestError.message)
    } finally {
      if (!controller.signal.aborted) setLoadingMore(false)
      if (paginationController.current === controller) paginationController.current = null
    }
  }

  return (
    <section className="workspace-page history-page" aria-labelledby="history-heading">
      <div className="workspace-page-heading">
        <p className="eyebrow">Your workspace</p>
        <h2 id="history-heading">Conversation history</h2>
        <p className="muted">Pick up where you left off.</p>
      </div>
      <PageState loading={loading} error={error}>
        {conversations.length === 0 ? (
          <p className="workspace-page-state">Your conversations will appear here.</p>
        ) : (
          <ul className="history-list">
            {conversations.map((conversation) => (
              <li key={conversation.id}>
                <button
                  type="button"
                  className="history-item"
                  onClick={() => onSelectConversation(conversation.id)}
                >
                  <span>{conversation.title}</span>
                  <small>{new Date(conversation.updated_at).toLocaleString()}</small>
                </button>
              </li>
            ))}
          </ul>
        )}
        {paginationError && <p className="workspace-page-error" role="alert">{paginationError}</p>}
        {hasMore && (
          <button
            className="button button-secondary history-load-more"
            type="button"
            onClick={loadMoreConversations}
            disabled={loadingMore}
          >
            {loadingMore ? 'Loading…' : 'Load more conversations'}
          </button>
        )}
      </PageState>
    </section>
  )
}

export function ModelsAgentsPage() {
  const [provider, setProvider] = useState(null)
  const [capabilities, setCapabilities] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      getChatProviderStatus(controller.signal),
      getAnalyticsCapabilities(controller.signal),
    ])
      .then(([providerStatus, capabilityResponse]) => {
        setProvider(providerStatus)
        setCapabilities(Array.isArray(capabilityResponse.operations) ? capabilityResponse.operations : [])
      })
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') setError(requestError.message)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  return (
    <section className="workspace-page models-page" aria-labelledby="models-heading">
      <div className="workspace-page-heading">
        <p className="eyebrow">Advanced configuration</p>
        <h2 id="models-heading">Models &amp; Agents</h2>
        <p className="muted">The assistant chooses how to work. Review the available model connection and supported capabilities here.</p>
      </div>
      <PageState loading={loading} error={error}>
        <section className="settings-section">
          <p className="eyebrow">Model connection</p>
          <h3>{provider?.model ?? provider?.provider ?? 'Provider status'}</h3>
          <p className="muted">
            Status: <strong>{provider?.status?.replaceAll('_', ' ') ?? 'Unavailable'}</strong>
          </p>
          {provider?.message && <p className="muted">{provider.message}</p>}
          <p className="workspace-page-note">Model configuration is managed by the deployment environment. Model status does not guarantee a successful response.</p>
        </section>
        <section className="settings-section">
          <p className="eyebrow">Assistant capabilities</p>
          <h3>Available analysis</h3>
          {capabilities.length === 0 ? (
            <p className="muted">No capabilities are currently reported.</p>
          ) : (
            <ul className="capability-list">
              {capabilities.map((capability) => (
                <li key={capability.operation}>
                  <strong>{capability.operation.replaceAll('_', ' ')}</strong>
                  <span>{capability.description}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </PageState>
    </section>
  )
}
