// State derivation for the Data workspace stepper.
// Everything is computed from data the workspace already holds
// (files, suitability runs, EDA and forecast results). Nothing is simulated.

export function scrollToSection(id) {
  const target = document.getElementById(id)
  target?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

export function deriveJourney({ hasProject, files, mlSuitability, analysis, forecastRun }) {
  const ready = files.filter((file) => file.status === 'Ready')
  const normalized = ready.filter((file) => file.normalization_status === 'Ready')
  const hasFailure = files.some(
    (file) => file.status === 'Failed' || file.normalization_status === 'Failed',
  )
  const hasProfile = files.some((file) => Boolean(mlSuitability?.[file.id]))

  const steps = [
    {
      id: 'upload',
      title: 'Upload',
      done: ready.length > 0,
      hint: 'Add a CSV, TSV, XLSX, JSON, Parquet or XML file.',
      action: hasProject
        ? { label: 'Go to upload', target: 'ws-upload' }
        : { label: 'Create a project first', href: '#projects' },
    },
    {
      id: 'normalize',
      title: 'Normalize',
      done: normalized.length > 0,
      hint: 'Creates the verified Parquet table that every analysis reads.',
      action: { label: 'Go to your files', target: 'ws-files' },
    },
    {
      id: 'profile',
      title: 'Profile',
      done: hasProfile,
      hint: 'Check which analysis types your data supports.',
      action: { label: 'Go to your files', target: 'ws-files' },
    },
    {
      id: 'eda',
      title: 'EDA',
      done: Boolean(analysis?.result),
      hint: 'Choose a column and run exploratory analysis.',
      action: { label: 'Go to your files', target: 'ws-files' },
      resultTarget: 'ws-eda-result',
    },
    {
      id: 'forecast',
      title: 'Forecast',
      optional: true,
      done: Boolean(forecastRun?.result),
      hint: 'Optional. Open Monthly forecasting and pick a date and a numeric column.',
      action: { label: 'Go to your files', target: 'ws-files' },
      resultTarget: 'ws-forecast-result',
    },
  ]

  const currentIndex = steps.findIndex((step) => !step.done)
  return {
    steps,
    currentIndex: currentIndex === -1 ? steps.length : currentIndex,
    complete: currentIndex === -1,
    hasFailure,
  }
}
