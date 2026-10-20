import { useEffect, useRef, useState } from 'react'
import { createBrowserExecutionClient } from './execution'

const browserExecutionClient = createBrowserExecutionClient()
const SAMPLE_INPUT = Uint8Array.from(
  'InsightOS browser execution check',
  (character) => character.charCodeAt(0),
)

function BrowserExecution({ client = browserExecutionClient } = {}) {
  const [capability, setCapability] = useState(null)
  const [running, setRunning] = useState(false)
  const [execution, setExecution] = useState(null)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState(0)
  const executionController = useRef(null)

  useEffect(() => {
    let active = true
    client.discover()
      .then((result) => {
        if (active) setCapability(result)
      })
    return () => {
      active = false
      executionController.current?.abort()
    }
  }, [client])

  async function runLocalCheck() {
    if (executionController.current) return
    const controller = new AbortController()
    executionController.current = controller
    setRunning(true)
    setError('')
    setExecution(null)
    setProgress(0)
    try {
      const result = await client.executeSha256(SAMPLE_INPUT, {
        signal: controller.signal,
        onProgress: setProgress,
      })
      setExecution(result)
    } catch (executionError) {
      setError(executionError.status === 'cancelled'
        ? 'The browser task was cancelled.'
        : executionError.message)
    } finally {
      if (executionController.current === controller) {
        executionController.current = null
        setRunning(false)
      }
    }
  }

  return (
    <section className="panel browser-execution-panel" aria-label="Browser execution">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Capability-aware execution</p>
          <h2>Browser capabilities</h2>
        </div>
        {capability === null && <span className="muted">Checking</span>}
        {capability?.target && <span className="provider-status provider-configured">Available</span>}
        {capability && !capability.target && <span className="provider-status provider-not_configured">Unavailable</span>}
      </div>
      <p className="muted">
        {capability?.target
          ? 'A Web Worker and browser SHA-256 were initialized successfully. No GPU capability is assumed.'
          : capability?.error?.message ?? 'Checking whether a Web Worker can run a bounded local task.'}
      </p>
      <button
        className="button button-secondary"
        type="button"
        onClick={runLocalCheck}
        disabled={!capability?.target || running}
      >
        {running ? 'Running locally…' : 'Run local execution check'}
      </button>
      {running && (
        <>
          <progress aria-label="Local task progress" max="100" value={progress} />
          <button
            className="text-button"
            type="button"
            onClick={() => executionController.current?.abort()}
          >
            Cancel task
          </button>
        </>
      )}
      {execution && (
        <p className="execution-result" role="status">
          Completed on {execution.targetId}: SHA-256 {execution.result.sha256}
        </p>
      )}
      {error && <p className="conversation-error" role="alert">{error}</p>}
      <p className="execution-note">
        The demonstration hashes a fixed sample in this browser. It does not upload data or
        execute existing InsightOS analyses.
      </p>
    </section>
  )
}

export default BrowserExecution
