import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createBrowserExecutionClient } from './execution'
import BrowserExecution from './BrowserExecution'

class FakeWorker {
  constructor({ holdTask = false } = {}) {
    this.holdTask = holdTask
  }

  postMessage({ id, task }) {
    if (task === 'probe') {
      this.onmessage?.({ data: { id, type: 'result', result: ['worker', 'sha256'] } })
      return
    }
    if (this.holdTask) return
    this.onmessage?.({ data: { id, type: 'progress', progress: 10 } })
    this.onmessage?.({
      data: { id, type: 'result', result: { sha256: 'b'.repeat(64) } },
    })
  }

  terminate() {}
}

afterEach(() => {
  cleanup()
})

describe('browser execution panel', () => {
  it('reports initialized capabilities and runs a local sample task', async () => {
    const client = createBrowserExecutionClient({
      workerFactory: () => new FakeWorker(),
    })
    render(<BrowserExecution client={client} />)

    expect(await screen.findByText(/Web Worker and browser SHA-256 were initialized/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Run local execution check' }))
    expect((await screen.findByRole('status')).textContent)
      .toContain('Completed on browser: SHA-256')
  })

  it('reports unavailable workers without claiming execution support', async () => {
    const client = createBrowserExecutionClient({
      workerFactory: () => {
        throw new Error('worker initialization failed')
      },
    })
    render(<BrowserExecution client={client} />)

    expect(await screen.findByText('The browser worker could not be started.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Run local execution check' }).disabled).toBe(true)
  })

  it('shows progress and cancels browser work explicitly', async () => {
    const client = createBrowserExecutionClient({
      workerFactory: () => new FakeWorker({ holdTask: true }),
    })
    render(<BrowserExecution client={client} />)

    await screen.findByText(/Web Worker and browser SHA-256 were initialized/)
    fireEvent.click(screen.getByRole('button', { name: 'Run local execution check' }))
    expect(screen.getByRole('progressbar', { name: 'Local task progress' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel task' }))

    expect((await screen.findByRole('alert')).textContent)
      .toBe('The browser task was cancelled.')
  })
})
