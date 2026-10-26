import { useCallback, useEffect, useState } from 'react'
import {
  createConversation,
  getChatProviderStatus,
  getConversation,
  getConversations,
  getProjectDatasets,
  getProjects,
  sendConversationMessage,
} from './api'

const CONVERSATION_PAGE_SIZE = 25
const MESSAGE_PAGE_SIZE = 25

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
