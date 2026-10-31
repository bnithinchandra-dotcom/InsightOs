import { useRef, useState } from 'react'

const ACCEPTED_FILES = '.csv,.tsv,.xlsx,.json,.parquet,.xml'
const ACCEPTED_EXTENSIONS = new Set(ACCEPTED_FILES.split(','))

export default function ConversationComposer({
  draft,
  sending,
  onDraftChange,
  onSubmit,
  contextControls,
  inputRef,
}) {
  const fileInput = useRef(null)
  const [localFile, setLocalFile] = useState(null)
  const [fileError, setFileError] = useState('')
  const [dragging, setDragging] = useState(false)

  function chooseFile(file) {
    if (file) {
      const extension = `.${file.name.split('.').pop().toLowerCase()}`
      if (ACCEPTED_EXTENSIONS.has(extension)) {
        setLocalFile(file)
        setFileError('')
      } else {
        setLocalFile(null)
        setFileError('Choose a CSV, TSV, XLSX, JSON, Parquet, or XML file.')
      }
    }
    if (fileInput.current) fileInput.current.value = ''
  }

  function handleDrop(event) {
    event.preventDefault()
    setDragging(false)
    chooseFile(event.dataTransfer.files?.[0])
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  return (
    <form
      className={`conversation-composer ${dragging ? 'is-dragging' : ''}`}
      onSubmit={onSubmit}
      onDragEnter={(event) => { event.preventDefault(); setDragging(true) }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false)
      }}
      onDrop={handleDrop}
      aria-label="Message the assistant"
    >
      <label className="visually-hidden" htmlFor="conversation-message">Your message</label>
      <textarea
        ref={inputRef}
        id="conversation-message"
        value={draft}
        onChange={(event) => onDraftChange(event.target.value)}
        onKeyDown={handleKeyDown}
        maxLength={8000}
        placeholder="Ask anything about your business data…"
        disabled={sending}
        rows={2}
      />
      {localFile && (
        <div className="local-file-notice" role="status">
          <span><strong>{localFile.name}</strong> — selected on this device only. It has not been uploaded or attached to this message.</span>
          <button type="button" className="text-button" onClick={() => setLocalFile(null)}>Remove</button>
          <a href="#datasets">Upload in My Data</a>
        </div>
      )}
      {fileError && <p className="composer-file-error" role="alert">{fileError}</p>}
      {dragging && <span className="composer-drop-hint" aria-live="polite">Drop to select a local file</span>}
      <div className="composer-actions">
        <input
          ref={fileInput}
          className="visually-hidden"
          type="file"
          accept={ACCEPTED_FILES}
          aria-hidden="true"
          tabIndex={-1}
          onChange={(event) => chooseFile(event.target.files?.[0])}
        />
        <div className="composer-context-actions">
          <button
            className="composer-attach"
            type="button"
            aria-label="Choose a local data file"
            onClick={() => fileInput.current?.click()}
          >
            <span aria-hidden="true">＋</span> Add data
          </button>
          {contextControls}
        </div>
        <div className="composer-submit-actions">
          <span className="composer-hint">Enter to send · Shift + Enter for a new line</span>
          <button
            className="composer-send"
            type="submit"
            aria-label={sending ? 'Sending message' : 'Send message'}
            title={sending ? 'Sending message' : 'Send message'}
            disabled={!draft.trim() || sending}
          >
            {sending ? (
              <span className="composer-send-spinner" aria-hidden="true" />
            ) : (
              <svg aria-hidden="true" viewBox="0 0 20 20" fill="none">
                <path d="M10 15V5m0 0L6 9m4-4 4 4" />
              </svg>
            )}
          </button>
        </div>
      </div>
    </form>
  )
}
