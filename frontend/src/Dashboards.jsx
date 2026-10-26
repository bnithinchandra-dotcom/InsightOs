import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  createDashboard,
  deleteDashboard,
  generateAnalyticsReport,
  getDashboard,
  getDashboards,
  getDatasetFiles,
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
  const [selectedId, setSelectedId] = useState('')
  const [report, setReport] = useState(null)
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
      setSelectedId((current) => saved.some((dashboard) => dashboard.id === current) ? current : saved[0]?.id ?? '')
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
      setReport(await generateAnalyticsReport({ ...analysis, charts }))
      setNotice('Report refreshed from current source data. Dashboards refresh only when requested.')
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
              <select value={selectedId} onChange={(event) => { setSelectedId(event.target.value); setReport(null); setNotice('') }}>
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
              {busy ? 'Refreshing…' : 'Refresh report'}
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
