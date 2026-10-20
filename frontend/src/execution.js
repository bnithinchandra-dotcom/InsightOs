const MAX_HASH_BYTES = 16 * 1024 * 1024
const DEFAULT_TIMEOUT_MS = 10_000
let nextRequestId = 1

export class ExecutionError extends Error {
  constructor(code, message, status = 'failed') {
    super(message)
    this.name = 'ExecutionError'
    this.code = code
    this.status = status
  }
}

export function selectExecutionTarget(requirements, targets) {
  if (
    requirements.dataClassification === 'local_only'
    && requirements.allowDataTransfer
  ) {
    return {
      target: null,
      rationale: 'Local-only data cannot be transferred to another execution target.',
      errorCode: 'no_execution_target',
    }
  }
  const compatible = targets
    .filter((target) => target.available)
    .filter((target) => requirements.allowedTargets.includes(target.kind))
    .filter((target) => requirements.requiredCapabilities.every(
      (capability) => target.capabilities.includes(capability),
    ))
    .filter((target) => target.dataClassifications.includes(requirements.dataClassification))
    .filter((target) => (
      requirements.dataClassification !== 'local_only' || target.kind !== 'backend'
    ))
    .filter((target) => (
      requirements.dataLocality === 'any'
      || target.kind === requirements.dataLocality
      || requirements.allowDataTransfer
    ))
    .filter((target) => (
      requirements.minimumMemoryBytes === undefined
      || (
        Number.isSafeInteger(target.memoryBytes)
        && target.memoryBytes >= requirements.minimumMemoryBytes
      )
    ))
    .filter((target) => (
      requirements.timeoutMs === undefined || target.supportsTimeout
    ))
    .filter((target) => (
      requirements.requireCancellation !== true || target.supportsCancellation
    ))
    .sort((left, right) => {
      const localityRank = (target) => (
        requirements.dataLocality === 'any'
        || target.kind === requirements.dataLocality
        ? 0
        : 1
      )
      const localRank = (target) => (
        requirements.preferLocal && target.kind === 'backend' ? 1 : 0
      )
      return localityRank(left) - localityRank(right)
        || localRank(left) - localRank(right)
        || left.targetId.localeCompare(right.targetId)
    })

  if (compatible.length === 0) {
    return {
      target: null,
      rationale: 'No available target satisfies the task capabilities, locality, privacy, resource, and timeout constraints.',
      errorCode: 'no_execution_target',
    }
  }

  const target = compatible[0]
  const reasons = [
    `${target.targetId} supports the required capabilities`,
    'the target is available',
    `the target permits ${requirements.dataClassification} data`,
  ]
  if (requirements.dataLocality !== 'any') {
    reasons.push(target.kind === requirements.dataLocality
      ? 'the target matches data locality'
      : 'data transfer is explicitly permitted')
  }
  if (requirements.minimumMemoryBytes !== undefined) {
    reasons.push('the measured target memory meets the minimum')
  }
  if (requirements.timeoutMs !== undefined) {
    reasons.push('the target supports enforced timeouts')
  }
  if (requirements.requireCancellation === true) {
    reasons.push('the target supports cancellation')
  }
  return { target, rationale: `${reasons.join('; ')}.` }
}

function createWorker() {
  if (typeof Worker !== 'function') {
    throw new ExecutionError(
      'worker_unavailable',
      'This browser does not support Web Workers.',
      'unavailable',
    )
  }
  return new Worker(new URL('./executionWorker.js', import.meta.url), { type: 'module' })
}

function sendWorkerRequest({
  workerFactory,
  task,
  input,
  timeoutMs,
  signal,
  onProgress,
}) {
  return new Promise((resolve, reject) => {
    let worker
    let settled = false
    const requestId = nextRequestId++
    const finish = (callback, value) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      if (worker) {
        worker.onmessage = null
        worker.onerror = null
        worker.terminate()
      }
      callback(value)
    }
    const onAbort = () => finish(
      reject,
      new ExecutionError('execution_cancelled', 'The browser task was cancelled.', 'cancelled'),
    )
    const timer = setTimeout(() => finish(
      reject,
      new ExecutionError('execution_timed_out', 'The browser task exceeded its time limit.', 'timed_out'),
    ), timeoutMs)

    if (signal?.aborted) {
      onAbort()
      return
    }
    signal?.addEventListener('abort', onAbort, { once: true })

    try {
      worker = workerFactory()
      worker.onmessage = (event) => {
        if (event.data?.id !== requestId) return
        if (event.data.type === 'progress') {
          onProgress?.(event.data.progress)
        } else if (event.data.type === 'result') {
          finish(resolve, event.data.result)
        } else if (event.data.type === 'error') {
          finish(
            reject,
            new ExecutionError(
              event.data.error?.code || 'execution_failed',
              event.data.error?.message || 'The browser task could not be completed.',
            ),
          )
        }
      }
      worker.onerror = () => finish(
        reject,
        new ExecutionError('worker_failed', 'The browser worker stopped unexpectedly.'),
      )
      worker.postMessage({ id: requestId, task, input }, input instanceof ArrayBuffer ? [input] : [])
    } catch (error) {
      finish(
        reject,
        error instanceof ExecutionError
          ? error
          : new ExecutionError(
            'worker_unavailable',
            'The browser worker could not be started.',
            'unavailable',
          ),
      )
    }
  })
}

export function createBrowserExecutionClient({
  workerFactory = createWorker,
  timeoutMs = DEFAULT_TIMEOUT_MS,
} = {}) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 300_000) {
    throw new RangeError('Browser task timeout must be greater than 0 and at most 300000 ms.')
  }
  let capabilityPromise

  async function discover() {
    if (!capabilityPromise) {
      capabilityPromise = sendWorkerRequest({
        workerFactory,
        task: 'probe',
        input: null,
        timeoutMs: Math.min(timeoutMs, 2_000),
      })
        .then((capabilities) => ({
          target: {
            targetId: 'browser',
            kind: 'browser',
            capabilities,
            dataClassifications: ['public', 'internal', 'sensitive', 'local_only'],
            available: true,
            memoryBytes: null,
            supportsCancellation: true,
            supportsTimeout: true,
          },
          error: null,
        }))
        .catch((error) => {
          capabilityPromise = null
          return {
            target: null,
            error: {
              code: error.code || 'worker_unavailable',
              message: error.message || 'Browser execution is unavailable.',
            },
          }
        })
    }
    return capabilityPromise
  }

  async function executeSha256(value, { signal, onProgress } = {}) {
    const isArrayBuffer = value instanceof ArrayBuffer
      || Object.prototype.toString.call(value) === '[object ArrayBuffer]'
    const isByteView = ArrayBuffer.isView(value) && value.BYTES_PER_ELEMENT === 1
    const inputLength = isArrayBuffer || isByteView ? value.byteLength : -1
    if (inputLength > MAX_HASH_BYTES) {
      throw new ExecutionError(
        'task_input_too_large',
        'SHA-256 input exceeds the 16 MiB browser-task limit.',
      )
    }
    const bytes = isArrayBuffer
      ? new Uint8Array(value.slice(0))
      : isByteView
        ? Uint8Array.from(value)
        : null
    if (!bytes) {
      throw new ExecutionError('invalid_task_input', 'SHA-256 input must be bytes.')
    }
    const discovery = await discover()
    if (!discovery.target) {
      throw new ExecutionError(
        discovery.error.code,
        discovery.error.message,
        'unavailable',
      )
    }
    const requirements = {
      requiredCapabilities: ['sha256'],
      dataLocality: 'browser',
      dataClassification: 'local_only',
      allowedTargets: ['browser'],
      preferLocal: true,
      minimumMemoryBytes: undefined,
      timeoutMs,
    }
    const placement = selectExecutionTarget(requirements, [discovery.target])
    if (!placement.target) {
      throw new ExecutionError(
        placement.errorCode,
        placement.rationale,
        'unavailable',
      )
    }
    const result = await sendWorkerRequest({
      workerFactory,
      task: 'sha256',
      input: bytes.buffer,
      timeoutMs,
      signal,
      onProgress,
    })
    if (!result || !/^[0-9a-f]{64}$/.test(result.sha256)) {
      throw new ExecutionError(
        'invalid_execution_result',
        'The browser worker returned an invalid SHA-256 result.',
      )
    }
    return {
      status: 'completed',
      targetId: placement.target.targetId,
      rationale: placement.rationale,
      progress: 100,
      result,
    }
  }

  return { discover, executeSha256 }
}
