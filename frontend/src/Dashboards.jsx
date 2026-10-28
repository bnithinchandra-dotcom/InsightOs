import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  createDashboard,
  deleteDashboard,
  generateAnalyticsReport,
  getAnalyticsWorkflowResult,
  getDashboard,
  getDashboards,
  getDatasetFiles,
  getMonthlyForecast,
  getProjectDatasets,
  getProjects,
} from './api'

const palette = ['#8e9dff', '#56c594', '#e5b85c', '#e67777', '#62b7d8', '#c58de8', '#e88db5', '#a6c96a']

function fileColumns(file) {
  const profileColumns = file.profile_result?.tables?.[0]?.columns
  if (Array.isArray(profileColumns)) return profileColumns.map((column) => column.name).filter(Boolean)
  const schema = file.normalization_result?.tables?.[0]?.schema
  if (!Array.isArray(schema)) return []
  return schema.map((column) => (Array.isArray(column) ? column[0] : column?.name)).filter(Boolean)
}

function chartPoints(chart) {
  if (chart.chart_type === 'scatter') {
    const points = chart.data?.points ?? []
    if (!points.length) return null
    const minX = Math.min(...points.map((point) => point.x))
    const maxX = Math.max(...points.map((point) => point.x))
    const minY = Math.min(...points.map((point) => point.y))
    const maxY = Math.max(...points.map((point) => point.y))
    const x = (value) => 36 + ((value - minX) / (maxX - minX || 1)) * 580
    const y = (value) => 190 - ((value - minY) / (maxY - minY || 1)) * 160
    return (
      <svg className="dashboard-chart-svg" viewBox="0 0 640 220" role="img" aria-label={chart.title}>
        <title>{chart.title}</title>
        <line x1="36" y1="190" x2="616" y2="190" />
        <line x1="36" y1="20" x2="36" y2="190" />
        {points.map((point, index) => (
          <circle key={`${point.x}-${point.y}-${index}`} cx={x(point.x)} cy={y(point.y)} r="4" fill={palette[index % palette.length]} />
        ))}
      </svg>
    )
  }

  const labels = chart.data?.labels ?? []
  const series = chart.data?.series ?? []
  if (!labels.length || !series.length) return null
  if (chart.chart_type === 'pie') {
    const values = series[0]?.values ?? []
    if (values.some((value) => typeof value !== 'number' || value < 0)) return null
    const total = values.reduce((sum, value) => sum + value, 0)
    if (total <= 0) return null
    let angle = -Math.PI / 2
    const arcs = values.map((value, index) => {
      const end = angle + (value / total) * Math.PI * 2
      const startPoint = [150 + 78 * Math.cos(angle), 105 + 78 * Math.sin(angle)]
      const endPoint = [150 + 78 * Math.cos(end), 105 + 78 * Math.sin(end)]
      const path = `M 150 105 L ${startPoint.join(' ')} A 78 78 0 ${end - angle > Math.PI ? 1 : 0} 1 ${endPoint.join(' ')} Z`
      angle = end
      return <path key={labels[index]} d={path} fill={palette[index % palette.length]} />
    })
    return (
      <div className="dashboard-pie-layout">
        <svg className="dashboard-pie-svg" viewBox="0 0 300 210" role="img" aria-label={chart.title}>
          <title>{chart.title}</title>{arcs}
        </svg>
        <ul className="dashboard-chart-legend">
          {labels.map((label, index) => (
            <li key={`${label}-${index}`}><span style={{ backgroundColor: palette[index % palette.length] }} />{label}: {values[index]}</li>
          ))}
        </ul>
      </div>
    )
  }

  const values = series.flatMap((item) => item.values.filter((value) => typeof value === 'number'))
  const low = Math.min(0, ...values)
  const high = Math.max(0, ...values)
  const span = high - low || 1
  const y = (value) => 190 - ((value - low) / span) * 160
  const baseline = y(0)
  const groupWidth = 580 / labels.length
  const seriesWidth = groupWidth / series.length
  const xValues = chart.data?.x_values ?? labels
  const xNumbers = chart.chart_type === 'line'
    ? xValues.map((value) => (typeof value === 'number' ? value : Date.parse(value)))
    : labels.map((_label, index) => index)
  const minX = Math.min(...xNumbers)
  const maxX = Math.max(...xNumbers)
  const lineX = (index) => 36 + ((xNumbers[index] - minX) / (maxX - minX || 1)) * 580
  return (
    <>
      <svg className="dashboard-chart-svg" viewBox="0 0 640 220" role="img" aria-label={chart.title}>
        <title>{chart.title}</title>
        <line x1="36" y1={baseline} x2="616" y2={baseline} />
        {chart.chart_type === 'line' ? series.map((item, seriesIndex) => {
          const points = item.values.map((value, index) => (
            typeof value === 'number' ? `${lineX(index)},${y(value)}` : null
          )).filter(Boolean).join(' ')
          return <polyline key={item.name} points={points} fill="none" stroke={palette[seriesIndex % palette.length]} strokeWidth="3" />
        }) : series.flatMap((item, seriesIndex) => item.values.map((value, index) => {
          if (typeof value !== 'number') return null
          const x = 36 + groupWidth * index + seriesWidth * seriesIndex + 3
          return (
            <rect
              key={`${item.name}-${index}`}
              x={x}
              y={Math.min(y(value), baseline)}
              width={Math.max(2, seriesWidth - 6)}
              height={Math.max(1, Math.abs(baseline - y(value)))}
              fill={palette[seriesIndex % palette.length]}
            />
          )
        }))}
        {labels.map((label, index) => (
          <text key={`${label}-${index}`} x={36 + groupWidth * index + groupWidth / 2} y="210" textAnchor="middle">{label.slice(0, 14)}</text>
        ))}
      </svg>
      {chart.chart_type === 'line' && (
        <ul className="dashboard-chart-legend" aria-label={`${chart.title} series`}>
          {series.map((item, index) => (
            <li key={item.name}>
              <span style={{ backgroundColor: palette[index % palette.length] }} />
              {item.name}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

export function ChartCard({ chart }) {
  return (
    <article className="dashboard-chart-card">
      <h3>{chart.title}</h3>
      <p className="muted">{chart.chart_type} · {chart.x_field} / {chart.y_field}</p>
      {chartPoints(chart) ?? <p className="muted">No chartable values are available.</p>}
      {chart.warnings?.map((warning) => <p className="dashboard-warning" key={warning}>{warning}</p>)}
    </article>
  )
}

function SavedForecast({ entry }) {
  const { component } = entry
  if (entry.error) {
    return (
      <article className="dashboard-chart-card" role="status">
        <h3>{component.chart_specification?.title ?? 'Saved monthly forecast'}</h3>
        <p className="dashboard-warning">
          This saved forecast is unavailable or stale: {entry.error}
        </p>
        <p className="muted">
          Original run {component.workflow_id} · dataset {component.dataset_id} /
          {' '}file {component.file_id} · source {component.source_fingerprint}
        </p>
      </article>
    )
  }
  const { result, completedAt } = entry
  return (
    <article className="dashboard-chart-card">
      <h3>Original persisted forecast run</h3>
      <p className="muted">
        Persisted run {component.workflow_id} · dataset {component.dataset_id} /
        {' '}file {component.file_id} · source {component.source_fingerprint.slice(0, 12)}
      </p>
      <p className="muted">
        {component.horizon}-month horizon · {component.aggregation} aggregation ·
        {' '}generated {completedAt ?? component.forecast_completed_at ?? 'time unavailable'}
      </p>
      <p className="muted">
        {result.evaluation.method ?? 'Chronological holdout'} ·
        {' '}{result.evaluation.sample_count ?? 'unknown'} evaluation period(s)
        {result.evaluation.period_start && ` (${result.evaluation.period_start}–${result.evaluation.period_end})`}
      </p>
      <p className="muted">
        Measured holdout MAE {result.evaluation.candidate.mae.toFixed(2)} vs
        {' '}{result.evaluation.baseline_name} baseline MAE
        {' '}{result.evaluation.baseline.mae.toFixed(2)} · RMSE
        {' '}{result.evaluation.candidate.rmse.toFixed(2)} · WAPE
        {' '}{!Number.isFinite(result.evaluation.candidate.wape)
          ? `unavailable${result.evaluation.candidate.wape_reason
            ? `: ${result.evaluation.candidate.wape_reason}`
            : ''}`
          : `${result.evaluation.candidate.wape.toFixed(2)}%`}
        {' '}· comparison {result.evaluation.comparison?.outcome ?? 'legacy result; comparison unavailable'}
      </p>
      <p className="muted">
        {result.uncertainty.status === 'available'
          ? 'Empirical rolling-origin error envelope; not a calibrated prediction interval.'
          : `Uncertainty unavailable: ${result.uncertainty.reason}`}
      </p>
      {result.forecast.map((point) => (
        <p className="muted" key={point.period}>
          Forecast {point.period}: {point.predicted.toFixed(2)}
          {point.lower_bound !== null && point.lower_bound !== undefined
            && point.upper_bound !== null && point.upper_bound !== undefined
            ? ` · empirical error envelope ${point.lower_bound.toFixed(2)}–${point.upper_bound.toFixed(2)}`
            : ' · uncertainty bounds unavailable'}
        </p>
      ))}
      {(result.suitability?.findings ?? []).map((finding, index) => (
        <p
          className={finding.severity === 'warning' || finding.severity === 'blocked'
            ? 'dashboard-warning'
            : 'muted'}
          key={`${finding.code}-${index}`}
        >
          {finding.severity}: {finding.message}
        </p>
      ))}
      <ChartCard chart={result.chart} />
    </article>
  )
}

function SavedAnalyticsResult({ entry }) {
  const { component } = entry
  if (entry.error) {
    return (
      <article className="dashboard-chart-card" role="status">
        <h3>Saved analytics output</h3>
        <p className="dashboard-warning">
          This saved analytics result is unavailable or stale: {entry.error}
        </p>
        <p className="muted">
          Original workflow {component.workflow_id} · task {component.task_id}
        </p>
      </article>
    )
  }
  const { result } = entry
  const summary = result.result.summary
  const rowLabel = summary?.joined_row_count !== undefined
    ? `${summary.joined_row_count} joined row(s)`
    : summary?.rows_analyzed !== undefined
      ? `${summary.rows_analyzed} source row(s) analyzed`
      : null
  const groupCount = summary?.group_count
  return (
    <article className="dashboard-chart-card">
      <h3>Original persisted analytics output</h3>
      <p className="muted">
        {result.operation.replaceAll('_', ' ')} · workflow {component.workflow_id} ·
        {' '}task {component.task_id} · workflow status {result.workflow_status} ·
        {' '}completed {result.completed_at ?? 'time unavailable'}
      </p>
      <p className="muted">
        {rowLabel && `${rowLabel} · `}
        {groupCount !== undefined && `${groupCount} result group(s) · `}
        {summary?.findings_count ?? result.result.findings.length} verified finding(s) ·
        {' '}provenance {result.provenance.operation_id}
      </p>
      {result.dependency_task_ids?.length > 0 && (
        <p className="muted">
          Executed after persisted plan task(s): {result.dependency_task_ids.join(', ')}
        </p>
      )}
      {result.result.charts.map((chart) => (
        <ChartCard chart={chart} key={`${chart.title}-${chart.x_field}-${chart.y_field}`} />
      ))}
      {result.result.findings.slice(0, 20).map((finding) => (
        <p className="muted" key={finding.id}>
          {finding.statement}
        </p>
      ))}
      {result.result.findings.length > 20 && (
        <p className="muted">
          Showing 20 of {result.result.findings.length} findings. Open the source workflow for the complete bounded report.
        </p>
      )}
      {result.warnings.map((warning, index) => (
        <p className="dashboard-warning" key={`${warning}-${index}`}>{warning}</p>
      ))}
    </article>
  )
}

function DashboardForm({ sources, onCreate, busy }) {
  const readySources = sources.filter((source) => source.columns.length > 0)
  const [title, setTitle] = useState('')
  const [leftId, setLeftId] = useState('')
  const [rightId, setRightId] = useState('')
  const [leftKey, setLeftKey] = useState('')
  const [rightKey, setRightKey] = useState('')
  const [dimension, setDimension] = useState('')
  const [metricColumn, setMetricColumn] = useState('')
  const [operation, setOperation] = useState('sum')
  const [chartType, setChartType] = useState('bar')
  const selectedLeftId = leftId || readySources[0]?.key || ''
  const selectedRightId = rightId || readySources.find((source) => source.key !== selectedLeftId)?.key || ''
  const left = readySources.find((source) => source.key === selectedLeftId)
  const right = readySources.find((source) => source.key === selectedRightId)

  async function submit(event) {
    event.preventDefault()
    if (!left || !right || left.key === right.key || !leftKey || !rightKey || !dimension || !metricColumn) return
    const reportRequest = {
      sources: [
        { alias: 'left', dataset_id: left.datasetId, file_id: left.fileId, table_index: 0 },
        { alias: 'right', dataset_id: right.datasetId, file_id: right.fileId, table_index: 0 },
      ],
      joins: [{
        left_key: { alias: 'left', column: leftKey },
        right_key: { alias: 'right', column: rightKey },
        join_type: 'inner',
        allow_many_to_many: false,
      }],
      dimensions: [{ alias: 'left', column: dimension }],
      metrics: [{
        name: 'metric',
        operation,
        column: { alias: 'right', column: metricColumn },
      }],
    }
    await onCreate({
      title,
      analysis_request: reportRequest,
      chart_requests: [{
        title: `${operation} of ${metricColumn} by ${dimension}`,
        chart_type: chartType,
        x_field: `left.${dimension}`,
        y_field: 'metric',
      }],
    })
  }

  if (readySources.length < 2) {
    return <p className="muted">Add at least two ready, normalized dataset files before creating a dashboard.</p>
  }
  const sourceSelect = (label, value, onChange, excluded) => (
    <label className="dashboard-field">{label}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Select a file</option>
        {readySources.filter((source) => source.key !== excluded).map((source) => (
          <option key={source.key} value={source.key}>{source.datasetName} · {source.filename}</option>
        ))}
      </select>
    </label>
  )
  return (
    <form className="dashboard-builder" onSubmit={submit}>
      <label className="dashboard-field">Dashboard name
        <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} required />
      </label>
      <div className="dashboard-builder-grid">
        {sourceSelect('First source', selectedLeftId, (value) => {
          setLeftId(value)
          setLeftKey('')
          setDimension('')
        }, selectedRightId)}
        {sourceSelect('Second source', selectedRightId, (value) => {
          setRightId(value)
          setRightKey('')
          setMetricColumn('')
        }, selectedLeftId)}
      </div>
      <p className="muted">Choose exact join keys explicitly. Recommendations are not automatically approved.</p>
      {left && right && (
        <div className="dashboard-builder-grid">
          <label className="dashboard-field">First join key
            <select value={leftKey} onChange={(event) => setLeftKey(event.target.value)} required>
              <option value="">Select a column</option>{left.columns.map((name) => <option key={name}>{name}</option>)}
            </select>
          </label>
          <label className="dashboard-field">Second join key
            <select value={rightKey} onChange={(event) => setRightKey(event.target.value)} required>
              <option value="">Select a column</option>{right.columns.map((name) => <option key={name}>{name}</option>)}
            </select>
          </label>
          <label className="dashboard-field">Group by
            <select value={dimension} onChange={(event) => setDimension(event.target.value)} required>
              <option value="">Select a dimension</option>{left.columns.map((name) => <option key={name}>{name}</option>)}
            </select>
          </label>
          <label className="dashboard-field">Metric column
            <select value={metricColumn} onChange={(event) => setMetricColumn(event.target.value)} required>
              <option value="">Select a column</option>{right.columns.map((name) => <option key={name}>{name}</option>)}
            </select>
          </label>
          <label className="dashboard-field">Aggregation
            <select value={operation} onChange={(event) => setOperation(event.target.value)}>
              {['sum', 'average', 'minimum', 'maximum', 'count', 'distinct_count'].map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label className="dashboard-field">Chart type
            <select value={chartType} onChange={(event) => setChartType(event.target.value)}>
              {['bar', 'line', 'scatter', 'pie'].map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
        </div>
      )}
      <button className="button button-primary" type="submit" disabled={busy || !title.trim()}>
        {busy ? 'Validating and saving…' : 'Create dashboard'}
      </button>
    </form>
  )
}

function Dashboards() {
  const [dashboards, setDashboards] = useState([])
  const [sources, setSources] = useState([])
  const [selectedId, setSelectedId] = useState(() => (
    new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('dashboardId') ?? ''
  ))
  const [report, setReport] = useState(null)
  const [forecastComponents, setForecastComponents] = useState([])
  const [analyticsComponents, setAnalyticsComponents] = useState([])
  const [layout, setLayout] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const loadData = useCallback(async (signal) => {
    try {
      const [saved, projects] = await Promise.all([getDashboards(signal), getProjects(signal)])
      const datasets = (await Promise.all(projects.map((project) => getProjectDatasets(project.id, signal))))
        .flat()
      const fileGroups = await Promise.all(datasets.map(async (dataset) => {
        const files = await getDatasetFiles(dataset.id, signal)
        return files
          .filter((file) => file.status === 'Ready' && file.normalization_status === 'Ready')
          .map((file) => ({
            key: `${dataset.id}:${file.id}`,
            datasetId: dataset.id,
            fileId: file.id,
            datasetName: dataset.name,
            filename: file.filename,
            columns: fileColumns(file),
          }))
      }))
      setDashboards(saved)
      setSources(fileGroups.flat())
      setSelectedId((current) => saved.some((dashboard) => dashboard.id === current)
        ? current
        : saved[0]?.id ?? '')
    } catch (requestError) {
      if (requestError.name !== 'AbortError') setError(requestError.message)
    } finally {
      if (!signal.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    // oxlint-disable-next-line react/set-state-in-effect
    loadData(controller.signal)
    return () => controller.abort()
  }, [loadData])

  const selected = useMemo(
    () => dashboards.find((dashboard) => dashboard.id === selectedId),
    [dashboards, selectedId],
  )

  const loadSavedForecasts = useCallback(async (dashboard, signal) => {
    const components = dashboard.definition?.forecast_components ?? []
    const loaded = await Promise.all(components.map(async (component) => {
      try {
        const run = await getMonthlyForecast(component.workflow_id, signal)
        const result = run.task?.result
        const matchesSavedReference = (
          run.status === 'completed'
          && run.task?.status === 'completed'
          && result
          && result.dataset_id === component.dataset_id
          && result.file_id === component.file_id
          && result.table_index === component.table_index
          && result.source_fingerprint === component.source_fingerprint
          && result.normalized_artifact_sha256 === component.normalized_artifact_sha256
          && result.date_column === component.date_column
          && result.target_column === component.target_column
          && result.aggregation === component.aggregation
          && result.horizon === component.horizon
          && result.result_sha256 === component.result_sha256
          && result.model_artifact_sha256 === component.model_artifact_sha256
          && result.chart?.chart_type === component.chart_specification?.chart_type
          && result.chart?.title === component.chart_specification?.title
          && result.chart?.x_field === component.chart_specification?.x_field
          && result.chart?.y_field === component.chart_specification?.y_field
          && result.chart?.series_field === component.chart_specification?.series_field
          && result.chart?.data?.series?.some((series) => series.name === 'Observed history')
          && result.chart?.data?.series?.some((series) => series.name === 'Forecast')
          && result.evaluation?.candidate
          && result.evaluation?.baseline
        )
        if (!matchesSavedReference) {
          throw new Error('The saved forecast reference does not match its persisted result.')
        }
        return {
          component,
          result,
          completedAt: run.completed_at,
        }
      } catch (requestError) {
        if (requestError.name === 'AbortError') throw requestError
        return { component, error: requestError.message }
      }
    }))
    if (!signal?.aborted) setForecastComponents(loaded)
    return loaded
  }, [])

  const loadSavedAnalytics = useCallback(async (dashboard, signal) => {
    const components = dashboard.definition?.analytics_components ?? []
    const loaded = await Promise.all(components.map(async (component) => {
      try {
        const result = await getAnalyticsWorkflowResult(
          component.workflow_id,
          component.task_id,
          signal,
        )
        const sameSourceReferences = (
          Array.isArray(component.source_references)
          && Array.isArray(result.source_references)
          && component.source_references.length === result.source_references.length
          && component.source_references.every((savedSource) => (
            result.source_references.some((currentSource) => (
              currentSource.alias === savedSource.alias
              && currentSource.dataset_id === savedSource.dataset_id
              && currentSource.file_id === savedSource.file_id
              && currentSource.table_index === savedSource.table_index
              && currentSource.source_fingerprint === savedSource.source_fingerprint
              && currentSource.normalized_artifact_sha256
                === savedSource.normalized_artifact_sha256
            ))
          ))
        )
        if (
          result.workflow_id !== component.workflow_id
          || result.task_id !== component.task_id
          || result.operation !== component.operation
          || result.result_sha256 !== component.result_sha256
          || !sameSourceReferences
          || !result.provenance
          || !Array.isArray(result.result?.charts)
          || !Array.isArray(result.result?.findings)
        ) {
          throw new Error('The saved analytics reference does not match its verified workflow result.')
        }
        return { component, result }
      } catch (requestError) {
        if (requestError.name === 'AbortError') throw requestError
        return { component, error: requestError.message }
      }
    }))
    if (!signal?.aborted) setAnalyticsComponents(loaded)
    return loaded
  }, [])

  useEffect(() => {
    if (!selectedId) return undefined
    const controller = new AbortController()
    getDashboard(selectedId, controller.signal)
      .then((dashboard) => Promise.all([
        loadSavedForecasts(dashboard, controller.signal),
        loadSavedAnalytics(dashboard, controller.signal),
      ]))
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') setError(requestError.message)
      })
    return () => controller.abort()
  }, [loadSavedAnalytics, loadSavedForecasts, selectedId])

  async function refreshDashboard(dashboard = selected) {
    if (!dashboard) return
    setBusy(true)
    setError('')
    setNotice('')
    setReport(null)
    try {
      const definition = await getDashboard(dashboard.id)
      const { analysis_request: analysis, chart_requests: charts } = definition.definition
      setLayout(definition.definition.layout ?? [])
      await Promise.all([
        loadSavedForecasts(definition),
        loadSavedAnalytics(definition),
      ])
      if (analysis) {
        setReport(await generateAnalyticsReport({ ...analysis, charts }))
        setNotice(
          'Dashboard report refreshed from current source data. Saved workflow results and forecasts were revalidated and reloaded without recomputation or retraining.',
        )
      } else {
        setReport(null)
        setNotice('Saved workflow results and forecasts were revalidated and reloaded without recomputation or retraining.')
      }
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  async function handleCreate(payload) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const dashboard = await createDashboard(payload)
      setDashboards((current) => [dashboard, ...current])
      setSelectedId(dashboard.id)
      await refreshDashboard(dashboard)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete() {
    if (!selected) return
    setBusy(true)
    setError('')
    try {
      await deleteDashboard(selected.id)
      const remaining = dashboards.filter((dashboard) => dashboard.id !== selected.id)
      setDashboards(remaining)
      setSelectedId(remaining[0]?.id ?? '')
      setReport(null)
      setForecastComponents([])
      setAnalyticsComponents([])
      setLayout([])
      setNotice('Dashboard deleted.')
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="dashboard-workspace">
      <section className="panel dashboard-panel">
        <div className="dashboard-toolbar">
          <div>
            <p className="eyebrow">Saved analytical views</p>
            <h2>Dashboards</h2>
          </div>
          {dashboards.length > 0 && (
            <label className="dashboard-select-label">Dashboard
              <select value={selectedId} onChange={(event) => { setSelectedId(event.target.value); setReport(null); setForecastComponents([]); setAnalyticsComponents([]); setNotice('') }}>
                {dashboards.map((dashboard) => <option key={dashboard.id} value={dashboard.id}>{dashboard.title}</option>)}
              </select>
            </label>
          )}
        </div>
        {loading && <p className="muted" role="status">Loading dashboards and available files…</p>}
        {!loading && dashboards.length === 0 && <p className="muted">No dashboards saved yet. Build one from two authorized, normalized files below.</p>}
        {selected && (
          <div className="dashboard-actions">
            <p className="muted">{selected.description || 'Saved analysis definition'} · refreshes on demand</p>
            <button className="button button-secondary" type="button" onClick={() => refreshDashboard()} disabled={busy}>
              {busy
                ? 'Refreshing…'
                : selected.definition?.analysis_request
                  ? 'Refresh report'
                  : 'Reload saved forecasts'}
            </button>
            <button className="button button-secondary" type="button" onClick={handleDelete} disabled={busy}>Delete</button>
          </div>
        )}
        {notice && <p className="dashboard-notice" role="status">{notice}</p>}
        {error && <p className="dashboard-error" role="alert">{error}</p>}
        {report && (
          <div className="dashboard-report">
            <p className="muted">
              {report.summary.joined_row_count} joined rows · {report.summary.group_count} groups ·
              {' '}{report.summary.findings_count} measured findings · provenance {report.provenance.operation_id.slice(0, 12)}
            </p>
            <p className="muted">Computed findings are factual measurements; no causal interpretation is generated.</p>
            {report.warnings.map((warning, index) => <p className="dashboard-warning" key={`${warning}-${index}`}>{warning}</p>)}
            <div className="dashboard-chart-grid">
              {report.charts.map((chart, index) => {
                const item = layout.find((position) => position.chart_index === index)
                return (
                  <div
                    key={`${chart.title}-${chart.chart_type}`}
                    className="dashboard-chart-placement"
                    style={item ? {
                      gridColumn: `${item.x + 1} / span ${item.width}`,
                      gridRow: `${item.y + 1} / span ${item.height}`,
                    } : undefined}
                  >
                    <ChartCard chart={chart} />
                  </div>
                )
              })}
            </div>
            <ul className="dashboard-findings">
              {report.findings.map((finding) => (
                <li key={finding.id}>
                  <strong>{finding.statement}</strong>
                  <span>{finding.supporting_metric.valid_count} valid of {finding.supporting_metric.group_row_count} rows · sources {finding.source_references.map((source) => `${source.dataset_id}/${source.file_id}`).join(', ')}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {forecastComponents.length > 0 && (
          <section className="dashboard-report">
            <h3>Saved forecasts · original persisted runs</h3>
            <div className="dashboard-chart-grid">
              {forecastComponents.map((entry) => (
                <SavedForecast key={entry.component.workflow_id} entry={entry} />
              ))}
            </div>
          </section>
        )}
        {analyticsComponents.length > 0 && (
          <section className="dashboard-report">
            <h3>Saved analytics outputs · original workflows</h3>
            <div className="dashboard-chart-grid">
              {analyticsComponents.map((entry) => (
                <SavedAnalyticsResult
                  entry={entry}
                  key={`${entry.component.workflow_id}-${entry.component.task_id}`}
                />
              ))}
            </div>
          </section>
        )}
      </section>
      <section className="panel dashboard-panel">
        <p className="eyebrow">Definition builder</p>
        <h2>Create a dashboard</h2>
        {loading ? <p className="muted">Loading available sources…</p> : (
          <DashboardForm sources={sources} onCreate={handleCreate} busy={busy} />
        )}
      </section>
    </div>
  )
}

export default Dashboards
