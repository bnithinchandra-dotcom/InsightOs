import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import ConversationComposer from './ConversationComposer'

afterEach(() => cleanup())

describe('conversation composer', () => {
  it('shows dropped files as local-only and never submits them', () => {
    const onSubmit = vi.fn((event) => event.preventDefault())
    render(
      <ConversationComposer draft="" sending={false} onDraftChange={vi.fn()} onSubmit={onSubmit} />,
    )

    const composer = screen.getByRole('form', { name: 'Message the assistant' })
    const file = new File(['month,revenue'], 'revenue.csv', { type: 'text/csv' })
    fireEvent.dragEnter(composer, { dataTransfer: { files: [file] } })
    expect(composer.className).toContain('is-dragging')
    fireEvent.drop(composer, { dataTransfer: { files: [file] } })

    expect(screen.getByText(/selected on this device only/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Upload in My Data' }).getAttribute('href')).toBe('#datasets')
    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText(/has not been uploaded or attached/)).toBeTruthy()
  })

  it('does not present unsupported dropped files as usable data', () => {
    render(
      <ConversationComposer draft="" sending={false} onDraftChange={vi.fn()} onSubmit={(event) => event.preventDefault()} />,
    )
    const composer = screen.getByRole('form', { name: 'Message the assistant' })
    fireEvent.drop(composer, {
      dataTransfer: { files: [new File([''], 'notes.exe', { type: 'application/octet-stream' })] },
    })
    expect(screen.getByRole('alert').textContent).toContain('Choose a CSV')
  })

  it('sends on Enter but keeps Shift+Enter available for multiline text', () => {
    const onSubmit = vi.fn((event) => event.preventDefault())
    render(
      <ConversationComposer draft="Question" sending={false} onDraftChange={vi.fn()} onSubmit={onSubmit} />,
    )
    const textarea = screen.getByLabelText('Your message')
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true })
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledOnce()
  })

  it('disables empty and submitting messages accessibly', () => {
    const props = {
      draft: '',
      sending: false,
      onDraftChange: vi.fn(),
      onSubmit: (event) => event.preventDefault(),
    }
    const { rerender } = render(<ConversationComposer {...props} />)

    expect(screen.getByRole('button', { name: 'Send message' }).disabled).toBe(true)
    rerender(<ConversationComposer {...props} draft="A question" sending />)
    expect(screen.getByRole('button', { name: 'Sending message' }).disabled).toBe(true)
  })
})
