import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createBrowserExecutionClient,
  ExecutionError,
  selectExecutionTarget,
} from './execution'

class FakeWorker {
  constructor({ onPostMessage } = {}) {
    this.onPostMessage = onPostMessage
    this.terminated = false
    this.messages = []
  }

  postMessage(message, transfer = []) {
    this.messages.push({ message, transfer })
    this.onPostMessage?.(message, this)
  }

  respond(data) {
    this.onmessage?.({ data })
  }

  terminate() {
    this.terminated = true
  }
}

const browserTarget = {
  targetId: 'browser',
  kind: 'browser',
  capabilities: ['worker', 'sha256'],
  dataClassifications: ['public', 'internal', 'sensitive', 'local_only'],
  available: true,
  memoryBytes: null,
  supportsCancellation: true,
  supportsTimeout: true,
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('execution placement', () => {
  it('prefers a capable local target and returns a rationale', () => {
    const selection = selectExecutionTarget({
      requiredCapabilities: ['sha256'],
      dataLocality: 'any',
      dataClassification: 'internal',
      allowedTargets: ['browser', 'backend'],
      preferLocal: true,
    }, [
      { ...browserTarget, targetId: 'backend', kind: 'backend' },
      browserTarget,
    ])

    expect(selection.target.targetId).toBe('browser')
    expect(selection.rationale).toContain('supports the required capabilities')
  })

  it('does not transfer local-only data to backend as a fallback', () => {
    const selection = selectExecutionTarget({
      requiredCapabilities: ['sha256'],
      dataLocality: 'browser',
      dataClassification: 'local_only',
      allowedTargets: ['browser', 'backend'],
      allowDataTransfer: true,
      preferLocal: true,
    }, [{ ...browserTarget, targetId: 'backend', kind: 'backend' }])

    expect(selection.target).toBeNull()
    expect(selection.errorCode).toBe('no_execution_target')
  })

  it('requires measured memory and timeout support when requested', () => {
    const requirements = {
      requiredCapabilities: ['sha256'],
      dataLocality: 'browser',
      dataClassification: 'local_only',
      allowedTargets: ['browser'],
      minimumMemoryBytes: 1024,
    }
    expect(selectExecutionTarget(requirements, [browserTarget]).target).toBeNull()
    expect(selectExecutionTarget({
      ...requirements,
      minimumMemoryBytes: undefined,
      timeoutMs: 100,
    }, [{ ...browserTarget, supportsTimeout: false }]).target).toBeNull()
    expect(selectExecutionTarget({
      ...requirements,
      minimumMemoryBytes: undefined,
      requireCancellation: true,
    }, [{ ...browserTarget, supportsCancellation: false }]).target).toBeNull()
  })

  it('does not move backend-local data to an optional local runtime without consent', () => {
    const selection = selectExecutionTarget({
      requiredCapabilities: ['sha256'],
      dataLocality: 'backend',
      dataClassification: 'internal',
      allowedTargets: ['backend', 'local_runtime'],
      preferLocal: true,
    }, [{
      ...browserTarget,
      targetId: 'runtime',
      kind: 'local_runtime',
    }])

    expect(selection.target).toBeNull()
    expect(selection.errorCode).toBe('no_execution_target')
  })
})

describe('browser execution client', () => {
  it('reports only worker and SHA-256 after worker initialization', async () => {
    const workers = []
    const client = createBrowserExecutionClient({
      workerFactory: () => {
        const worker = new FakeWorker({
          onPostMessage: ({ id, task }, current) => {
            if (task === 'probe') {
              current.respond({ id, type: 'result', result: ['worker', 'sha256'] })
            }
          },
        })
        workers.push(worker)
        return worker
      },
    })

    const capability = await client.discover()
    expect(capability.target.capabilities).toEqual(['worker', 'sha256'])
    expect(capability.target.memoryBytes).toBeNull()
    expect(workers[0].terminated).toBe(true)
  })

  it('executes SHA-256 locally with validated result and visible progress', async () => {
    const workers = []
    const client = createBrowserExecutionClient({
      workerFactory: () => {
        const worker = new FakeWorker({
          onPostMessage: ({ id, task }, current) => {
            if (task === 'probe') {
              current.respond({ id, type: 'result', result: ['worker', 'sha256'] })
              return
            }
            current.respond({ id, type: 'progress', progress: 10 })
            current.respond({
              id,
              type: 'result',
              result: { sha256: 'a'.repeat(64) },
            })
          },
        })
        workers.push(worker)
        return worker
      },
    })
    const progress = []
    const result = await client.executeSha256(
      new TextEncoder().encode('local data'),
      { onProgress: (value) => progress.push(value) },
    )

    expect(result.status).toBe('completed')
    expect(result.targetId).toBe('browser')
    expect(result.result.sha256).toBe('a'.repeat(64))
    expect(progress).toEqual([10])
    expect(workers.at(-1).messages[0].transfer).toHaveLength(1)
    expect(workers.at(-1).terminated).toBe(true)
  })

  it('rejects invalid and oversized inputs before dispatch', async () => {
    const workerFactory = vi.fn()
    const client = createBrowserExecutionClient({ workerFactory })
    await expect(client.executeSha256('not bytes')).rejects.toMatchObject({
      code: 'invalid_task_input',
    })
    await expect(client.executeSha256(new Uint8Array(16 * 1024 * 1024 + 1)))
      .rejects.toMatchObject({ code: 'task_input_too_large' })
    expect(workerFactory).not.toHaveBeenCalled()
  })

  it('terminates work on timeout and cancellation', async () => {
    const workers = []
    const client = createBrowserExecutionClient({
      timeoutMs: 15,
      workerFactory: () => {
        const worker = new FakeWorker({
          onPostMessage: ({ id, task }, current) => {
            if (task === 'probe') {
              current.respond({ id, type: 'result', result: ['worker', 'sha256'] })
            }
          },
        })
        workers.push(worker)
        return worker
      },
    })

    await expect(client.executeSha256(new Uint8Array([1]))).rejects.toMatchObject({
      code: 'execution_timed_out',
      status: 'timed_out',
    })
    expect(workers.at(-1).terminated).toBe(true)

    const controller = new AbortController()
    const promise = client.executeSha256(new Uint8Array([2]), { signal: controller.signal })
    controller.abort()
    await expect(promise).rejects.toMatchObject({
      code: 'execution_cancelled',
      status: 'cancelled',
    })
    expect(workers.at(-1).terminated).toBe(true)
  })

  it('fails closed when worker capability initialization fails', async () => {
    const client = createBrowserExecutionClient({
      workerFactory: () => {
        throw new Error('internal detail')
      },
    })
    const capability = await client.discover()
    expect(capability.target).toBeNull()
    await expect(client.executeSha256(new Uint8Array())).rejects.toBeInstanceOf(ExecutionError)
    await expect(client.executeSha256(new Uint8Array())).rejects.toMatchObject({
      code: 'worker_unavailable',
      status: 'unavailable',
    })
  })
})
