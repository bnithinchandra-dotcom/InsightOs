import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from './App'

vi.mock('./BrowserExecution', () => ({ default: () => <div data-testid="browser-execution" /> }))
vi.mock('./ConversationWorkspace', async () => {
  const React = await import('react')
  let mountCount = 0
  return {
    default: function ConversationWorkspaceMock({ selectedConversationId, historyOpenInitially, active }) {
      const [mountId] = React.useState(() => ++mountCount)
      return (
        <div data-testid="page-assistant" data-instance={mountId} data-active={active}>
          Assistant {selectedConversationId}
          {historyOpenInitially && <span data-testid="conversation-history-open">History</span>}
        </div>
      )
    },
  }
})
vi.mock('./DataWorkspace', () => ({
  default: ({ page }) => <div data-testid={`page-${page}`}>Data {page}</div>,
}))
vi.mock('./Dashboards', () => ({ default: () => <div data-testid="page-dashboards">Dashboards</div> }))
vi.mock('./Overview', () => ({ default: () => <div data-testid="page-overview">Overview</div> }))
vi.mock('./SchemaArchitect', () => ({ default: () => <div data-testid="page-schema">Schema Architect</div> }))
vi.mock('./WorkspacePages', () => ({
  HistoryPage: () => <div data-testid="page-history">History</div>,
  ModelsAgentsPage: () => <div data-testid="page-models">Models</div>,
}))

function jsonResponse(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('application shell', () => {
  beforeEach(() => {
    localStorage.clear()
    window.location.hash = ''
    window.matchMedia = vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
    globalThis.fetch = vi.fn(async (input) => {
      const path = new URL(String(input), window.location.origin).pathname
      if (path === '/health') return jsonResponse({ status: 'healthy' })
      if (path === '/api/v1/system/info') {
        return jsonResponse({ services: {}, application: { name: 'InsightOS' } })
      }
      return jsonResponse({ detail: `Unexpected request: ${path}` })
    })
  })

  afterEach(() => cleanup())

  it('opens the assistant by default and exposes only the two primary destinations', () => {
    render(<App />)
    expect(screen.getByTestId('page-assistant')).toBeTruthy()
    const navigation = screen.getByRole('navigation', { name: 'Main navigation' })
    for (const label of ['Datasets', 'Dashboards']) {
      expect(navigation.textContent).toContain(label)
    }
    expect(navigation.textContent).not.toContain('Assistant')
    expect(navigation.textContent).not.toContain('Settings')
    expect(navigation.textContent).not.toContain('Schema Architect')
    expect(screen.getByText('Settings')).toBeTruthy()
  })

  it('keeps Schema Architect accessible as an advanced Settings tool', () => {
    window.location.hash = '#settings'
    render(<App />)
    expect(screen.getByRole('link', { name: /Open Schema Architect/ }).getAttribute('href')).toBe('#analysis')
  })

  it('preserves legacy hash routes for chat, data, projects, schema and dashboards', () => {
    const cases = [
      ['#chat', 'page-assistant'],
      ['#datasets', 'page-datasets'],
      ['#projects', 'page-projects'],
      ['#analysis', 'page-schema'],
      ['#dashboards', 'page-dashboards'],
      ['#history', 'page-assistant'],
      ['#models', 'page-models'],
    ]

    for (const [hash, testId] of cases) {
      cleanup()
      window.location.hash = hash
      render(<App />)
      expect(screen.getByTestId(testId)).toBeTruthy()
    }
  })

  it('keeps the same assistant instance mounted while switching destinations', async () => {
    render(<App />)
    const assistant = screen.getByTestId('page-assistant')
    const instance = assistant.getAttribute('data-instance')

    window.location.hash = '#datasets'
    await waitFor(() => expect(screen.getByTestId('page-datasets')).toBeTruthy())
    expect(screen.getByTestId('page-assistant').getAttribute('data-instance')).toBe(instance)
    expect(screen.getByTestId('page-assistant').getAttribute('data-active')).toBe('false')

    window.location.hash = '#chat'
    await waitFor(() => expect(screen.getByTestId('page-assistant').getAttribute('data-active')).toBe('true'))
    expect(screen.getByTestId('page-assistant').getAttribute('data-instance')).toBe(instance)
  })

  it('opens legacy history inside the assistant workspace', () => {
    window.location.hash = '#history'
    render(<App />)
    expect(screen.getByTestId('page-assistant')).toBeTruthy()
    expect(screen.getByTestId('conversation-history-open')).toBeTruthy()
  })

  it('collapses and expands the desktop navigation', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Expand navigation' }))
    const collapse = screen.getByRole('button', { name: 'Collapse navigation' })
    fireEvent.click(collapse)
    expect(screen.getByRole('button', { name: 'Expand navigation' }).getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(screen.getByRole('button', { name: 'Expand navigation' }))
    expect(screen.getByRole('button', { name: 'Collapse navigation' }).getAttribute('aria-expanded')).toBe('true')
  })

  it('applies and remembers an explicit theme choice', async () => {
    window.location.hash = '#settings'
    render(<App />)
    fireEvent.click(screen.getByRole('radio', { name: 'Dark' }))
    await waitFor(() => {
      expect(document.documentElement.dataset.theme).toBe('dark')
      expect(localStorage.getItem('insightos-theme')).toBe('dark')
    })
  })

  it('uses the system theme when no explicit preference is saved', async () => {
    window.matchMedia.mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })
    render(<App />)
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe('dark'))
    expect(localStorage.getItem('insightos-theme')).toBeNull()
  })
})
