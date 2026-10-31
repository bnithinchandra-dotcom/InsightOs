import { useEffect, useState } from 'react'
import { getDatasetFiles, getProjectDatasets } from './api'

function tableColumns(table) {
  if (Array.isArray(table?.columns)) {
    return table.columns
      .map((column) => (typeof column === 'string' ? column : column?.name))
      .filter(Boolean)
  }
  if (Array.isArray(table?.schema)) {
    return table.schema
      .map((column) => (Array.isArray(column) ? column[0] : column?.name))
      .filter(Boolean)
  }
  return []
}

function fileTables(file) {
  const normalizedTables = file.normalization_result?.tables
  const profileTables = file.profile_result?.tables
  const tables = Array.isArray(normalizedTables) ? normalizedTables : profileTables
  if (!Array.isArray(tables)) return []
  return tables.map((table, index) => {
    const profileTable = Array.isArray(profileTables) ? profileTables[index] : null
    return {
      id: `${file.id}-${index}`,
      name: table.name || table.table_name || profileTable?.name || profileTable?.table_name || `Table ${index + 1}`,
      index,
      rowCount: table.row_count ?? profileTable?.row_count,
      columnCount: table.column_count ?? tableColumns(table).length,
      columns: tableColumns(table).length ? tableColumns(table) : tableColumns(profileTable),
    }
  })
}

export default function DataExplorer({
  projects,
  datasets,
  projectId,
  datasetId,
  conversationActive = false,
  active = true,
  open = true,
  onProjectChange,
  onDatasetChange,
  selectedTable,
  onSelectTable,
  onClose,
}) {
  const [fileRequest, setFileRequest] = useState({ datasetId: '', files: [], error: '' })
  const [projectRequest, setProjectRequest] = useState({ projectId: '', datasets: [], error: '' })
  const [browseProjectId, setBrowseProjectId] = useState(String(projectId ?? ''))
  const [browseDatasetId, setBrowseDatasetId] = useState(String(datasetId ?? ''))
  const [search, setSearch] = useState('')
  const [openFiles, setOpenFiles] = useState({})
  const activeBrowseProjectId = browseProjectId || String(projects[0]?.id ?? '')

  useEffect(() => {
    if (!active || !activeBrowseProjectId || activeBrowseProjectId === String(projectId ?? '')) {
      return undefined
    }
    const controller = new AbortController()
    getProjectDatasets(activeBrowseProjectId, controller.signal)
      .then((projectDatasets) => setProjectRequest({
        projectId: activeBrowseProjectId,
        datasets: projectDatasets,
        error: '',
      }))
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') {
          setProjectRequest({
            projectId: activeBrowseProjectId,
            datasets: [],
            error: requestError.message,
          })
        }
      })
    return () => controller.abort()
  }, [active, activeBrowseProjectId, projectId])

  useEffect(() => {
    if (!active || !browseDatasetId) return undefined
    const controller = new AbortController()
    getDatasetFiles(browseDatasetId, controller.signal)
      .then((files) => setFileRequest({
        datasetId: browseDatasetId,
        files,
        error: '',
      }))
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') {
          setFileRequest({
            datasetId: browseDatasetId,
            files: [],
            error: requestError.message,
          })
        }
      })
    return () => controller.abort()
  }, [active, browseDatasetId])

  const hasCurrentFileRequest = Boolean(browseDatasetId)
    && fileRequest.datasetId === browseDatasetId
  const files = hasCurrentFileRequest ? fileRequest.files : []
  const loading = active && Boolean(browseDatasetId) && !hasCurrentFileRequest
  const error = hasCurrentFileRequest ? fileRequest.error : ''

  const availableDatasets = activeBrowseProjectId === String(projectId ?? '')
    ? datasets
    : projectRequest.projectId === activeBrowseProjectId
      ? projectRequest.datasets
      : []
  const projectError = projectRequest.projectId === activeBrowseProjectId
    ? projectRequest.error
    : ''
  const query = search.trim().toLocaleLowerCase()
  const filteredDatasets = availableDatasets.filter((dataset) => (
    !query || dataset.name?.toLocaleLowerCase().includes(query)
  ))

  if (!active) return null

  return (
    <aside className={`adaptive-data-explorer ${open ? '' : 'is-closed'}`} aria-label="Data Explorer">
      <header className="adaptive-panel-heading">
        <div>
          <p className="eyebrow">Workspace context</p>
          <h2>Data Explorer</h2>
        </div>
        <button
          className="adaptive-icon-button"
          type="button"
          onClick={onClose}
          aria-label="Close Data Explorer"
          title="Close Data Explorer"
        >
          ‹
        </button>
      </header>

      <div className="adaptive-explorer-selectors">
        <label>
          <span>Project</span>
          <select
            aria-label="Explorer project"
            value={activeBrowseProjectId}
            onChange={(event) => {
              const nextProjectId = event.target.value
              setBrowseProjectId(nextProjectId)
              setBrowseDatasetId('')
              setOpenFiles({})
              if (!conversationActive) onProjectChange(nextProjectId)
            }}
          >
            <option value="">Choose a project</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>{project.name}</option>
            ))}
          </select>
        </label>
        {activeBrowseProjectId && (
          <label>
            <span>Browse dataset</span>
            <select
              aria-label="Explorer active dataset"
              value={browseDatasetId}
              onChange={(event) => {
                const nextDatasetId = event.target.value
                setBrowseDatasetId(nextDatasetId)
                if (!conversationActive) {
                  if (String(projectId ?? '') !== activeBrowseProjectId) {
                    onProjectChange(activeBrowseProjectId)
                  }
                  onDatasetChange(nextDatasetId)
                }
              }}
            >
              <option value="">Choose a dataset</option>
              {availableDatasets.map((dataset) => (
                <option key={dataset.id} value={dataset.id}>{dataset.name}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      {conversationActive && (
        <p className="adaptive-scope-notice" role="note">
          Browsing here is for inspection only. This conversation keeps the data scope it started with.
        </p>
      )}

      <label className="adaptive-explorer-search">
        <span className="visually-hidden">Search datasets</span>
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search datasets"
        />
      </label>

      <div className="adaptive-explorer-content">
        {!activeBrowseProjectId ? (
          <p className="adaptive-panel-empty">
            No projects are available to browse.
          </p>
        ) : projectError ? (
          <p className="adaptive-panel-error" role="alert">{projectError}</p>
        ) : filteredDatasets.length === 0 ? (
          <p className="adaptive-panel-empty">
            {availableDatasets.length ? 'No datasets match your search.' : 'No datasets are available in this project.'}
          </p>
        ) : (
          <ul className="adaptive-dataset-list">
            {filteredDatasets.map((dataset) => (
              <li key={dataset.id}>
                <button
                  type="button"
                  className={`adaptive-dataset-item ${String(dataset.id) === browseDatasetId ? 'is-selected' : ''}`}
                  onClick={() => {
                    setBrowseDatasetId(String(dataset.id))
                    if (!conversationActive) {
                      if (String(projectId ?? '') !== activeBrowseProjectId) {
                        onProjectChange(activeBrowseProjectId)
                      }
                      onDatasetChange(String(dataset.id))
                    }
                  }}
                >
                  <span className="adaptive-dataset-icon" aria-hidden="true">▤</span>
                  <span>{dataset.name}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {browseDatasetId && (
          <section className="adaptive-file-section" aria-label="Dataset files">
            <h3>Files and tables</h3>
            {loading && <p className="adaptive-panel-empty" role="status">Loading dataset files…</p>}
            {error && <p className="adaptive-panel-error" role="alert">{error}</p>}
            {!loading && !error && files.length === 0 && (
              <p className="adaptive-panel-empty">No files are available in this dataset.</p>
            )}
            <ul className="adaptive-file-list">
              {files.map((file) => {
                const tables = fileTables(file)
                const expanded = Boolean(openFiles[file.id])
                return (
                  <li key={file.id}>
                    <button
                      type="button"
                      className="adaptive-file-item"
                      aria-expanded={expanded}
                      onClick={() => setOpenFiles((current) => ({
                        ...current,
                        [file.id]: !current[file.id],
                      }))}
                    >
                      <span aria-hidden="true">{expanded ? '⌄' : '›'}</span>
                      <span className="adaptive-file-name">{file.filename}</span>
                    </button>
                    {expanded && (
                      <ul className="adaptive-table-list">
                        {tables.length ? tables.map((table) => {
                          const isSelected = selectedTable?.fileId === file.id
                            && selectedTable?.tableIndex === table.index
                          return (
                            <li key={table.id}>
                              <button
                                type="button"
                                className={`adaptive-table-item ${isSelected ? 'is-selected' : ''}`}
                                aria-expanded={isSelected}
                                onClick={() => onSelectTable({
                                  datasetId: file.dataset_id,
                                  fileId: file.id,
                                  fileName: file.filename,
                                  tableIndex: table.index,
                                  name: table.name,
                                  rowCount: table.rowCount,
                                  columnCount: table.columnCount,
                                  columns: table.columns,
                                })}
                              >
                                <span>{table.name}</span>
                                <small>
                                  {Number.isFinite(table.rowCount) ? `${table.rowCount.toLocaleString()} rows` : 'Rows unavailable'}
                                  {' · '}
                                  {Number.isFinite(table.columnCount) ? `${table.columnCount} columns` : 'Columns unavailable'}
                                </small>
                              </button>
                              {isSelected && table.columns.length > 0 && (
                                <ul className="adaptive-column-list" aria-label={`${table.name} columns`}>
                                  {table.columns.map((column) => <li key={column}>{column}</li>)}
                                </ul>
                              )}
                            </li>
                          )
                        }) : (
                          <li className="adaptive-panel-empty">
                            Table metadata is not available for this file.
                          </li>
                        )}
                      </ul>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>
        )}
      </div>
    </aside>
  )
}
