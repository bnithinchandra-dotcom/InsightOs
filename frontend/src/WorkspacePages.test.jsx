import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { HistoryPage, ModelsAgentsPage } from './WorkspacePages'

function jsonResponse(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('workspace pages', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn(async (input) => {
      const path = new URL(String(input), window.location.origin).pathname
      if (path === '/api/v1/chat/conversations') {
        return jsonResponse([{
          id: 8,
          title: 'Quarterly sales review',
          status: 'Active',
          updated_at: '2026-10-30T12:00:00Z',
        }])
      }
      if (path === '/api/v1/chat/provider-status') {
        return jsonResponse({ provider: 'local', model: 'model-a', status: 'healthy' })
      }
      if (path === '/api/v1/chat/analytics/capabilities') {
        return jsonResponse({
          operations: [{
            operation: 'generate_evidence_report',
            description: 'Create a persisted report.',
          }],
        })
      }
      return jsonResponse({ detail: `Unexpected request: ${path}` })
    })
  })

  afterEach(() => cleanup())

  it('lets a user resume a persisted conversation from History', async () => {
    const onSelectConversation = vi.fn()
    render(<HistoryPage onSelectConversation={onSelectConversation} />)

    fireEvent.click(await screen.findByRole('button', { name: /Quarterly sales review/ }))
    expect(onSelectConversation).toHaveBeenCalledWith(8)
  })

  it('loads the next paginated conversation page from History', async () => {
    const firstPage = Array.from({ length: 25 }, (_, index) => ({
      id: index + 1,
      title: `Conversation ${index + 1}`,
      status: 'Active',
      updated_at: '2026-10-30T12:00:00Z',
    }))
    globalThis.fetch = vi.fn(async (input) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname !== '/api/v1/chat/conversations') return jsonResponse([])
      return jsonResponse(url.searchParams.get('offset') === '25'
        ? [{ id: 26, title: 'Conversation 26', status: 'Active', updated_at: '2026-10-30T12:00:00Z' }]
        : firstPage)
    })

    render(<HistoryPage onSelectConversation={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Load more conversations' }))

    expect(await screen.findByRole('button', { name: /Conversation 26/ })).toBeTruthy()
    const pageRequest = globalThis.fetch.mock.calls.find(([input]) => (
      new URL(String(input), window.location.origin).searchParams.get('offset') === '25'
    ))
    expect(pageRequest).toBeTruthy()
  })

  it('shows live provider status and backend-reported capabilities', async () => {
    render(<ModelsAgentsPage />)

    expect(await screen.findByText('model-a')).toBeTruthy()
    expect(screen.getByText('healthy')).toBeTruthy()
    expect(screen.getByText('generate evidence report')).toBeTruthy()
    expect(screen.getByText('Create a persisted report.')).toBeTruthy()
  })
})
