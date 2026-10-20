const MAX_HASH_BYTES = 16 * 1024 * 1024

function sendError(id, code, message) {
  self.postMessage({ id, type: 'error', error: { code, message } })
}

self.onmessage = async (event) => {
  const { id, task, input } = event.data ?? {}
  if (!Number.isSafeInteger(id)) return

  if (task === 'probe') {
    try {
      if (!globalThis.crypto?.subtle) {
        sendError(id, 'crypto_unavailable', 'Browser SHA-256 is unavailable.')
        return
      }
      await globalThis.crypto.subtle.digest('SHA-256', new Uint8Array())
      self.postMessage({
        id,
        type: 'result',
        result: ['worker', 'sha256'],
      })
    } catch {
      sendError(id, 'crypto_unavailable', 'Browser SHA-256 is unavailable.')
    }
    return
  }

  if (task !== 'sha256') {
    sendError(id, 'unsupported_task', 'The requested browser task is unavailable.')
    return
  }
  if (!(input instanceof ArrayBuffer) || input.byteLength > MAX_HASH_BYTES) {
    sendError(id, 'invalid_task_input', 'SHA-256 input is invalid or exceeds its safe limit.')
    return
  }

  try {
    self.postMessage({ id, type: 'progress', progress: 10 })
    const digest = await globalThis.crypto.subtle.digest('SHA-256', input)
    const sha256 = Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, '0'))
      .join('')
    self.postMessage({ id, type: 'progress', progress: 90 })
    self.postMessage({ id, type: 'result', result: { sha256 } })
  } catch {
    sendError(id, 'execution_failed', 'The browser could not hash the provided bytes.')
  }
}
