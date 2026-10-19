import { useEffect, useMemo, useRef, useState } from 'react'
import {
  createSchemaAnalysis,
  getDatasetFiles,
  getProjectDatasets,
  getProjects,
  getSchemaAnalyses,
  getSchemaAnalysis,
} from './api'

const HISTORY_LIMIT = 10
const MAX_SELECTED_TABLES = 20

function formatCount(value) {
  return Number.isFinite(value) ? value.toLocaleString() : 'Not available'
}

function formatPercent(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : 'Not available'
}

function tableLabel(identity) {
  if (!identity || typeof identity !== 'object') return 'Table'
  const name = identity.table_name
    || identity.filename
    || `Table ${Number(identity.table_index) + 1}`
  return `${name} · file ${identity.file_id}`
}

function SchemaArchitect() {
  const [projects, setProjects] = useState([])
  const [projectId, setProjectId] = useState('')
  const [datasets, setDatasets] = useState([])
  const [datasetId, setDatasetId] = useState('')
  const [files, setFiles] = useState([])
  const [selectedTables, setSelectedTables] = useState(() => new Set())
  const [businessContext, setBusinessContext] = useState('')
  const [history, setHistory] = useState([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [historyOffset, setHistoryOffset] = useState(0)
  const [analysis, setAnalysis] = useState(null)
  const [projectsLoading, setProjectsLoading] = useState(true)
  const [datasetsLoading, setDatasetsLoading] = useState(false)
  const [datasetLoading, setDatasetLoading] = useState(false)
  const [analysisLoading, setAnalysisLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const historyController = useRef(null)

  useEffect(() => {
    const controller = new AbortController()
    getProjects(controller.signal)
      .then((result) => setProjects(Array.isArray(result) ? result : []))
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') setError(requestError.message)
      })
      .finally(() => {
        if (!controller.signal.aborted) setProjectsLoading(false)
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!projectId) return undefined

    const controller = new AbortController()
    getProjectDatasets(projectId, controller.signal)
      .then((result) => setDatasets(Array.isArray(result) ? result : []))
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') setError(requestError.message)
      })
      .finally(() => {
        if (!controller.signal.aborted) setDatasetsLoading(false)
      })
    return () => controller.abort()
  }, [projectId])

  useEffect(() => {
    if (!datasetId) return undefined

    const controller = new AbortController()
    historyController.current = controller
    Promise.all([
      getDatasetFiles(datasetId, controller.signal),
      getSchemaAnalyses(datasetId, { limit: HISTORY_LIMIT, signal: controller.signal }),
    ])
      .then(([fileResult, historyResult]) => {
        setFiles(Array.isArray(fileResult) ? fileResult : [])
        setHistory(Array.isArray(historyResult?.items) ? historyResult.items : [])
        setHistoryTotal(Number.isFinite(historyResult?.total) ? historyResult.total : 0)
      })
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') setError(requestError.message)
      })
      .finally(() => {
        if (!controller.signal.aborted) setDatasetLoading(false)
      })
    return () => controller.abort()
  }, [datasetId])

  const availableTables = useMemo(() => files.flatMap((file) => {
    const normalizedTables = file.normalization_result?.tables
    if (file.normalization_status !== 'Ready' || !Array.isArray(normalizedTables)) return []
    return normalizedTables.map((table, index) => ({
      file,
      index: Number.isInteger(table.table_index) ? table.table_index : index,
      table,
      key: `${file.id}:${Number.isInteger(table.table_index) ? table.table_index : index}`,
    }))
  }), [files])

  const selectedCount = availableTables.filter((item) => selectedTables.has(item.key)).length

  const loadHistory = async (offset = 0) => {
    if (!datasetId) return
    historyController.current?.abort()
    const controller = new AbortController()
    historyController.current = controller
    try {
      const result = await getSchemaAnalyses(datasetId, {
        limit: HISTORY_LIMIT,
        offset,
        signal: controller.signal,
      })
      if (controller.signal.aborted) return
      setHistory(Array.isArray(result?.items) ? result.items : [])
      setHistoryTotal(Number.isFinite(result?.total) ? result.total : 0)
      setHistoryOffset(offset)
    } catch (requestError) {
      if (requestError.name !== 'AbortError') setError(requestError.message)
    }
  }

  const handleProjectChange = (nextProjectId) => {
    setProjectId(nextProjectId)
    setDatasets([])
    setDatasetId('')
    setFiles([])
    setSelectedTables(new Set())
    setHistory([])
    setHistoryTotal(0)
    setHistoryOffset(0)
    setAnalysis(null)
    setBusinessContext('')
    setError('')
    setNotice('')
    setDatasetsLoading(Boolean(nextProjectId))
    setDatasetLoading(false)
    historyController.current?.abort()
  }

  const handleDatasetChange = (nextDatasetId) => {
    setDatasetId(nextDatasetId)
    setFiles([])
    setSelectedTables(new Set())
    setHistory([])
    setHistoryTotal(0)
    setHistoryOffset(0)
    setAnalysis(null)
    setBusinessContext('')
    setError('')
    setNotice('')
    setDatasetLoading(Boolean(nextDatasetId))
    historyController.current?.abort()
  }

  const toggleTable = (key) => {
    setSelectedTables((current) => {
      const next = new Set(current)
      if (next.has(key)) {
        next.delete(key)
      } else if (next.size < MAX_SELECTED_TABLES) {
        next.add(key)
      }
      return next
    })
  }

  const selectAllTables = () => {
    setSelectedTables(new Set(availableTables.slice(0, MAX_SELECTED_TABLES).map((item) => item.key)))
  }

  const runAnalysis = async (recompute) => {
    if (!datasetId || !selectedCount) return
    const selections = new Map()
    for (const item of availableTables) {
      if (!selectedTables.has(item.key)) continue
      if (!selections.has(item.file.id)) selections.set(item.file.id, [])
      selections.get(item.file.id).push(item.index)
    }
    const payload = {
      files: Array.from(selections, ([file_id, table_indices]) => ({ file_id, table_indices })),
      business_context: businessContext.trim() || null,
      recompute,
    }

    setAnalysisLoading(true)
    setError('')
    setNotice('')
    try {
      const result = await createSchemaAnalysis(datasetId, payload)
      setAnalysis(result)
      setNotice(
        result.status === 'Completed'
          ? 'Schema analysis completed. Recommendations remain hypotheses for your review.'
          : `Analysis returned with status: ${result.status}.`,
      )
      await loadHistory(0)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setAnalysisLoading(false)
    }
  }

  const openAnalysis = async (analysisId) => {
    setError('')
    setNotice('')
    try {
      const result = await getSchemaAnalysis(datasetId, analysisId)
      setAnalysis(result)
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  const toggleAll = () => {
    if (selectedCount === Math.min(availableTables.length, MAX_SELECTED_TABLES)) {
      setSelectedTables(new Set())
    } else {
      selectAllTables()
    }
  }

  const result = analysis?.result
  const schemaTables = Array.isArray(result?.schema?.tables) ? result.schema.tables : []
  const relationships = Array.isArray(result?.relationships) ? result.relationships : []
  const recommendations = Array.isArray(result?.model_recommendations) ? result.model_recommendations : []

  return (
    <div className="schema-workspace">
      <section className="panel schema-setup">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Deterministic schema analysis</p>
            <h2>Choose normalized data</h2>
          </div>
          <span className="schema-limit">Up to {MAX_SELECTED_TABLES} tables</span>
        </div>

        {error && <p className="schema-message schema-error" role="alert">{error}</p>}
        {notice && <p className="schema-message schema-notice" role="status">{notice}</p>}

        <div className="schema-selectors">
          <label className="schema-field">
            <span>Project</span>
            <select
              value={projectId}
              onChange={(event) => handleProjectChange(event.target.value)}
              disabled={projectsLoading || projects.length === 0 || analysisLoading}
            >
              <option value="">{projectsLoading ? 'Loading projects…' : 'Select a project'}</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>{project.name}</option>
              ))}
            </select>
          </label>
          <label className="schema-field">
            <span>Dataset</span>
            <select
              value={datasetId}
              onChange={(event) => handleDatasetChange(event.target.value)}
              disabled={!projectId || datasetsLoading || datasets.length === 0 || analysisLoading}
            >
              <option value="">{datasetsLoading ? 'Loading datasets…' : 'Select a dataset'}</option>
              {datasets.map((dataset) => (
                <option key={dataset.id} value={dataset.id}>{dataset.name}</option>
              ))}
            </select>
          </label>
        </div>

        {!projectsLoading && !error && projects.length === 0 && (
          <p className="schema-empty-note">No projects are available. Create a project and add a dataset before analyzing.</p>
        )}
        {projectId && !datasetsLoading && !error && datasets.length === 0 && (
          <p className="schema-empty-note">This project has no datasets yet.</p>
        )}

        {datasetId && (
          <div className="schema-table-picker">
            <div className="schema-picker-heading">
              <div>
                <h3>Tables</h3>
                <p>Select the normalized tables to include. Source artifacts are read-only.</p>
              </div>
              <button
                className="button button-secondary"
                type="button"
                onClick={toggleAll}
                disabled={datasetLoading || availableTables.length === 0}
              >
                {selectedCount === Math.min(availableTables.length, MAX_SELECTED_TABLES) ? 'Clear selection' : 'Select available'}
              </button>
            </div>
            {datasetLoading ? (
              <p className="schema-empty-note">Loading dataset files and analysis history…</p>
            ) : files.length === 0 ? (
              <p className="schema-empty-note">This dataset has no files.</p>
            ) : (
              <div className="schema-file-list">
                {files.map((file) => {
                  const fileTables = availableTables.filter((item) => item.file.id === file.id)
                  const ready = file.normalization_status === 'Ready' && fileTables.length > 0
                  return (
                    <section className="schema-file-card" key={file.id}>
                      <div className="schema-file-heading">
                        <strong>{file.filename}</strong>
                        <span className={`schema-file-status ${ready ? 'ready' : ''}`}>
                          {ready ? 'Normalized' : `Unavailable · ${file.normalization_status || file.status || 'not normalized'}`}
                        </span>
                      </div>
                      {fileTables.length > 0 ? (
                        <div className="schema-table-list">
                          {fileTables.map(({ index, table, key }) => (
                            <label className="schema-table-option" key={key}>
                              <input
                                type="checkbox"
                                checked={selectedTables.has(key)}
                                onChange={() => toggleTable(key)}
                                disabled={!selectedTables.has(key) && selectedTables.size >= MAX_SELECTED_TABLES}
                              />
                              <span className="schema-table-name">{table.table_name || `Table ${index + 1}`}</span>
                              <span className="schema-table-meta">
                                {formatCount(table.row_count)} rows · {formatCount(table.column_count)} columns
                              </span>
                            </label>
                          ))}
                        </div>
                      ) : (
                        <p className="schema-file-hint">Normalize this file before it can be selected for analysis.</p>
                      )}
                    </section>
                  )
                })}
              </div>
            )}
            <p className="schema-selection-count">
              {selectedCount} of {MAX_SELECTED_TABLES} table slots selected
            </p>
            <label className="schema-field schema-context-field">
              <span>Business context <small>Optional · up to 5,000 characters</small></span>
              <textarea
                value={businessContext}
                onChange={(event) => setBusinessContext(event.target.value)}
                maxLength={5000}
                rows={3}
                placeholder="Add context to preserve with the analysis. It does not change the deterministic inference."
              />
            </label>
            <div className="schema-actions">
              <button
                className="button button-primary schema-submit"
                type="button"
                onClick={() => runAnalysis(false)}
                disabled={!selectedCount || analysisLoading || datasetLoading}
              >
                {analysisLoading ? 'Requesting analysis…' : 'Analyze selected tables'}
              </button>
              <button
                className="button button-secondary"
                type="button"
                onClick={() => runAnalysis(true)}
                disabled={!selectedCount || analysisLoading || datasetLoading}
              >
                Recompute
              </button>
              <span className="schema-action-hint">Recompute refreshes the saved result for this exact selection and context.</span>
            </div>
          </div>
        )}
      </section>

      {datasetId && (
        <section className="panel schema-history">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Saved analyses</p>
              <h2>History <span className="schema-count">{historyTotal}</span></h2>
            </div>
            <button
              className="button button-secondary"
              type="button"
              onClick={() => loadHistory(historyOffset)}
              disabled={datasetLoading}
            >
              Refresh
            </button>
          </div>
          {history.length === 0 ? (
            <p className="schema-empty-note">No schema analyses have been saved for this dataset.</p>
          ) : (
            <>
              <div className="schema-history-list">
                {history.map((item) => (
                  <button
                    className={`schema-history-item ${analysis?.id === item.id ? 'selected' : ''}`}
                    key={item.id}
                    type="button"
                    onClick={() => openAnalysis(item.id)}
                  >
                    <span className="schema-history-title">Analysis #{item.id}</span>
                    <span className="schema-history-status">{item.stale ? 'Stale' : item.status}</span>
                    <span className="schema-history-date">{item.created_at ? new Date(item.created_at).toLocaleString() : 'Date unavailable'}</span>
                  </button>
                ))}
              </div>
              <div className="schema-pagination">
                <span>{historyOffset + 1}–{Math.min(historyOffset + history.length, historyTotal)} of {historyTotal}</span>
                <div>
                  <button
                    className="button button-secondary"
                    type="button"
                    disabled={historyOffset === 0}
                    onClick={() => loadHistory(Math.max(0, historyOffset - HISTORY_LIMIT))}
                  >Previous</button>
                  <button
                    className="button button-secondary"
                    type="button"
                    disabled={historyOffset + HISTORY_LIMIT >= historyTotal}
                    onClick={() => loadHistory(historyOffset + HISTORY_LIMIT)}
                  >Next</button>
                </div>
              </div>
            </>
          )}
        </section>
      )}

      {analysis && (
        <section className="schema-result">
          <div className="panel schema-result-heading">
            <div>
              <p className="eyebrow">Analysis #{analysis.id}</p>
              <h2>Proposed analytical model</h2>
              <p className="muted">Recommendations are deterministic structural hypotheses, not verified business relationships.</p>
            </div>
            <span className={`schema-result-status ${analysis.stale ? 'stale' : ''}`}>
              {analysis.stale ? 'Stale input' : analysis.status}
            </span>
          </div>

          {analysis.status === 'Failed' && (
            <p className="schema-message schema-error" role="alert">
              {analysis.error?.message || 'This analysis did not complete. Review the input selection and retry.'}
            </p>
          )}
          {analysis.stale && (
            <p className="schema-message schema-warning" role="status">
              The dataset inputs changed after this result was created. Recompute to review the current inputs.
            </p>
          )}
          {analysis.configuration?.business_context && (
            <section className="panel schema-context-summary">
              <h3>Business context</h3>
              <p>{analysis.configuration.business_context}</p>
            </section>
          )}

          {schemaTables.length > 0 && (
            <section className="panel schema-result-section">
              <div className="panel-heading">
                <div><p className="eyebrow">Inventory</p><h3>Tables and columns</h3></div>
                <span className="schema-count">{formatCount(analysis.result?.schema?.rows_scanned)} rows scanned</span>
              </div>
              <div className="schema-result-table-grid">
                {schemaTables.map((table, tableIndex) => (
                  <article className="schema-result-table" key={`${table.identity?.file_id}:${table.identity?.table_index}:${tableIndex}`}>
                    <div className="schema-result-table-heading">
                      <strong>{tableLabel(table.identity)}</strong>
                      <span>{formatCount(table.row_count)} rows · {formatCount(table.column_count)} columns</span>
                    </div>
                    {table.schema_warnings?.length > 0 && (
                      <p className="schema-inline-warning">Warnings: {table.schema_warnings.join(', ')}</p>
                    )}
                    <div className="schema-column-list">
                      {(Array.isArray(table.columns) ? table.columns : []).map((column) => (
                        <div className="schema-column-row" key={`${column.position}:${column.name}`}>
                          <span className="schema-column-name">{column.name}</span>
                          <span>{column.normalized_type || 'Unknown type'}</span>
                          <span>{column.candidate_key_status === 'candidate' ? 'Key candidate' : 'No key evidence'}</span>
                          <span>Nulls {formatPercent(column.null_rate)}</span>
                          <span className="schema-role-candidates">
                            {Array.isArray(column.role_candidates) && column.role_candidates.length
                              ? `Possible: ${column.role_candidates.join(', ')}`
                              : 'Role uncertain'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
            </section>
          )}

          <section className="panel schema-result-section">
            <div className="panel-heading">
              <div><p className="eyebrow">Observed evidence</p><h3>Relationship candidates</h3></div>
              <span className="schema-count">{relationships.length}</span>
            </div>
            {relationships.length === 0 ? (
              <p className="schema-empty-note">No relationship candidates were produced for the selected tables.</p>
            ) : (
              <div className="schema-relationship-list">
                {relationships.map((relationship, index) => (
                  <details className="schema-relationship" key={`${relationship.source?.column}:${relationship.target?.column}:${index}`}>
                    <summary>
                      <span>
                        {tableLabel(relationship.source?.table)} · {relationship.source?.column}
                        <span className="schema-arrow"> → </span>
                        {tableLabel(relationship.target?.table)} · {relationship.target?.column}
                      </span>
                      <span className="schema-relationship-state">{relationship.status} · {relationship.cardinality_hypothesis}</span>
                    </summary>
                    <div className="schema-evidence-grid">
                      <span>Heuristic evidence rating <strong>{relationship.confidence || 'Not available'}</strong></span>
                      <span>Heuristic score (not probability) <strong>{formatPercent(relationship.confidence_score)}</strong></span>
                      <span>Non-null overlap <strong>{formatCount(relationship.evidence?.non_null_overlap_count)}</strong></span>
                      <span>Left duplicate rows <strong>{formatCount(relationship.evidence?.left_duplicate_non_null_rows)}</strong></span>
                      <span>Right duplicate rows <strong>{formatCount(relationship.evidence?.right_duplicate_non_null_rows)}</strong></span>
                      <span>Type compatibility <strong>{relationship.evidence?.type_compatibility || 'Unknown'}</strong></span>
                    </div>
                    {relationship.risks?.length > 0 && (
                      <p className="schema-detail-copy"><strong>Risks:</strong> {relationship.risks.join(' ')}</p>
                    )}
                    {relationship.unresolved_questions?.length > 0 && (
                      <ul className="schema-detail-list">
                        {relationship.unresolved_questions.map((question) => <li key={question}>{question}</li>)}
                      </ul>
                    )}
                  </details>
                ))}
              </div>
            )}
          </section>

          <section className="panel schema-result-section">
            <div className="panel-heading">
              <div><p className="eyebrow">Conditional proposals</p><h3>Model recommendations</h3></div>
              <span className="schema-count">{recommendations.length}</span>
            </div>
            {recommendations.length === 0 ? (
              <p className="schema-empty-note">The available evidence does not support a specific model recommendation.</p>
            ) : (
              <div className="schema-recommendation-list">
                {recommendations.map((recommendation, index) => (
                  <article className="schema-recommendation" key={`${recommendation.model_type}:${index}`}>
                    <div className="schema-recommendation-heading">
                      <h4>{recommendation.model_type} model</h4>
                      <span>{recommendation.status || 'Conditional'}</span>
                    </div>
                    {recommendation.fact_table_candidate && (
                      <p><strong>Fact-table candidate:</strong> {tableLabel(recommendation.fact_table_candidate)}</p>
                    )}
                    {recommendation.dimension_table_candidates?.length > 0 && (
                      <p><strong>Dimension candidates:</strong> {recommendation.dimension_table_candidates.map(tableLabel).join('; ')}</p>
                    )}
                    {recommendation.candidate_numeric_measures?.length > 0 && (
                      <p><strong>Possible numeric measures:</strong> {recommendation.candidate_numeric_measures.join(', ')}. Semantics unconfirmed.</p>
                    )}
                    {recommendation.risks?.length > 0 && (
                      <ul className="schema-detail-list">
                        {recommendation.risks.map((risk) => <li key={risk}>{risk}</li>)}
                      </ul>
                    )}
                    {recommendation.required_clarifications?.length > 0 && (
                      <details className="schema-clarifications">
                        <summary>Questions to confirm ({recommendation.required_clarifications.length})</summary>
                        <ul className="schema-detail-list">
                          {recommendation.required_clarifications.map((question) => <li key={question}>{question}</li>)}
                        </ul>
                      </details>
                    )}
                  </article>
                ))}
              </div>
            )}
          </section>

          {(result?.warnings?.length > 0 || result?.limitations?.length > 0) && (
            <section className="panel schema-result-section">
              <p className="eyebrow">Interpretation</p>
              <h3>Warnings and limitations</h3>
              {result.warnings?.length > 0 && (
                <p><strong>Warnings:</strong> {result.warnings.join(', ')}</p>
              )}
              {result.limitations?.length > 0 && (
                <ul className="schema-detail-list">
                  {result.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}
                </ul>
              )}
            </section>
          )}
          {analysis.provenance && (
            <p className="schema-provenance">
              Provenance: {analysis.provenance.operation_type} v{analysis.provenance.operation_version}
              {analysis.provenance.verification_status ? ` · ${analysis.provenance.verification_status}` : ''}
            </p>
          )}
        </section>
      )}
    </div>
  )
}

export default SchemaArchitect
