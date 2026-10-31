// Guided stepper for the Data workspace (presentation only).
// State comes from deriveJourney() in journeyState.js.
import { scrollToSection } from './journeyState'

function StepMark({ state, index }) {
  if (state === 'done') {
    return (
      <span className="journey-mark" aria-hidden="true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
          <path d="m5 12 5 5 9-10" />
        </svg>
      </span>
    )
  }
  return <span className="journey-mark" aria-hidden="true">{index + 1}</span>
}

function ActionButton({ action }) {
  if (action.href) {
    return <a className="journey-cta" href={action.href}>{action.label} <span aria-hidden="true">→</span></a>
  }
  return (
    <button className="journey-cta" type="button" onClick={() => scrollToSection(action.target)}>
      {action.label} <span aria-hidden="true">↓</span>
    </button>
  )
}

export default function DataJourney({ journey }) {
  const { steps, currentIndex, complete, hasFailure } = journey
  const current = steps[currentIndex]
  const doneCount = steps.filter((step) => step.done).length

  return (
    <section className="panel journey" aria-label="Guided analytics journey">
      <div className="journey-head">
        <div>
          <p className="eyebrow">Guided workflow</p>
          <h2>{complete ? 'Your data is analyzed' : 'From source data to evidence'}</h2>
        </div>
        <span className="journey-progress">{doneCount} of {steps.length} steps</span>
      </div>

      <ol className="journey-track">
        {steps.map((step, index) => {
          const state = step.done ? 'done' : index === currentIndex ? 'current' : 'upcoming'
          return (
            <li
              key={step.id}
              className={`journey-step is-${state}`}
              aria-current={state === 'current' ? 'step' : undefined}
            >
              <StepMark state={state} index={index} />
              <span className="journey-title">
                {step.optional ? `${step.title} (optional)` : step.title}
              </span>
              {step.done && step.resultTarget && (
                <button className="journey-result-link" type="button" onClick={() => scrollToSection(step.resultTarget)}>
                  View result
                </button>
              )}
            </li>
          )
        })}
      </ol>

      <div className="journey-next" aria-live="polite">
        {complete ? (
          <>
            <div>
              <p className="journey-next-label">All steps complete</p>
              <p className="journey-next-text">
                Save the verified EDA or forecast result to a dashboard, or ask the Assistant questions about this data.
              </p>
            </div>
            <div className="journey-next-actions">
              <a className="journey-cta" href="#dashboards">Open dashboards <span aria-hidden="true">→</span></a>
              <a className="journey-cta journey-cta-quiet" href="#chat">Ask the Assistant</a>
            </div>
          </>
        ) : (
          <>
            <div>
              <p className="journey-next-label">Next step · {current.title}</p>
              <p className="journey-next-text">{current.hint}</p>
              {hasFailure && (
                <p className="journey-next-warn">One of your files failed. Check its message in the file list.</p>
              )}
            </div>
            <div className="journey-next-actions">
              <ActionButton action={current.action} />
            </div>
          </>
        )}
      </div>
      <a className="text-link journey-builder-link" href="#dashboards">Open dashboard builder</a>
    </section>
  )
}

export function FileTrack({ file, profiled }) {
  const items = [
    { label: 'Uploaded', done: file.status === 'Ready', failed: file.status === 'Failed' },
    { label: 'Normalized', done: file.normalization_status === 'Ready', failed: file.normalization_status === 'Failed' },
    { label: 'Profiled', done: Boolean(profiled) },
  ]
  return (
    <span className="file-track" aria-label="File progress">
      {items.map((item) => (
        <span
          key={item.label}
          className={`file-track-chip ${item.done ? 'is-done' : item.failed ? 'is-failed' : ''}`}
        >
          {item.label}
        </span>
      ))}
    </span>
  )
}
