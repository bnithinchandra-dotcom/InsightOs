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
    fireEvent.click(screen.getByRole('button', { name: 'Start a conversation' }))
    expect(await screen.findByText('Ask a question about your existing projects, datasets, profiles, quality reports, EDA, or schema analyses.')).toBeTruthy()

    const input = screen.getByLabelText('Your message')
    fireEvent.change(input, { target: { value: 'Show my profile' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText('The stored profile is ready.')).toBeTruthy()
    expect(screen.getByText('Reviewing a data profile')).toBeTruthy()
    expect(input.value).toBe('')
  })

  it('preserves the draft and reports an API error', async () => {
    render(<ConversationWorkspace />)
    fireEvent.click(await screen.findByRole('button', { name: 'Start a conversation' }))
    await screen.findByText('Ask a question about your existing projects, datasets, profiles, quality reports, EDA, or schema analyses.')
    globalThis.fetch.mockImplementation(async (input) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === `/api/v1/chat/conversations/${conversation.id}/messages`) {
        return jsonResponse({ detail: 'A response is already being generated.' }, 409)
      }
      if (url.pathname === '/api/v1/chat/conversations') return jsonResponse([])
      if (url.pathname === '/api/v1/chat/provider-status') {
        return jsonResponse({ provider: 'none', status: 'not_configured', message: '' })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([])
      if (url.pathname === `/api/v1/chat/conversations/${conversation.id}`) {
        return jsonResponse({ ...conversation, total_messages: 0, messages: [], offset: 0 })
      }
      return jsonResponse({ detail: 'Unexpected request.' }, 500)
    })

    const input = screen.getByLabelText('Your message')
    fireEvent.change(input, { target: { value: 'Keep this draft' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect((await screen.findByRole('alert')).textContent).toContain('A response is already being generated.')
    await waitFor(() => expect(input.value).toBe('Keep this draft'))
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
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }))

    await waitFor(() => expect(createPayloads).toEqual([{
      project_id: 31,
      dataset_id: 42,
    }]))
  })
})
