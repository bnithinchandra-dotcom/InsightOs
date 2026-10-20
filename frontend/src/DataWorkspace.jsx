import { useEffect, useState } from 'react'
import {
  createDataset,
  createDatasetEda,
  createLocalWorkspaceProfile,
  createProject,
  getDatasetFiles,
  getLocalWorkspaceProfile,
  getProjects,
  getProjectDatasets,
  normalizeDatasetFile,
  uploadDatasetFile,
} from './api'

const supportedFormats = '.csv, .tsv, .xlsx, .json, .parquet, .xml'

function errorMessage(error) {
  return error instanceof Error ? error.message : 'The request could not be completed.'
}

function DataWorkspace({ page }) {
  const [profile, setProfile] = useState(null)
  const [profileConfigured, setProfileConfigured] = useState(false)
  const [profileMessage, setProfileMessage] = useState('')
  const [profileName, setProfileName] = useState('')
  const [profileEmail, setProfileEmail] = useState('')
  const [projects, setProjects] = useState([])
  const [projectId, setProjectId] = useState('')
  const [datasets, setDatasets] = useState([])
  const [datasetId, setDatasetId] = useState('')
  const [files, setFiles] = useState([])
  const [projectName, setProjectName] = useState('')
  const [datasetName, setDatasetName] = useState('')
  const [newDatasetFile, setNewDatasetFile] = useState(null)
  const [additionalFile, setAdditionalFile] = useState(null)
  const [selectedColumns, setSelectedColumns] = useState({})
  const [analysis, setAnalysis] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      getLocalWorkspaceProfile(controller.signal),
      getProjects(controller.signal),
    ])
      .then(([workspace, projectList]) => {
        setProfileConfigured(workspace.configured)
        setProfile(workspace.profile)
        setProfileMessage(workspace.message ?? '')
        setProjects(projectList)
        setProjectId((current) => (
          projectList.some((project) => String(project.id) === current)
            ? current
            : String(projectList[0]?.id ?? '')
        ))
        setError('')
      })
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') setError(errorMessage(requestError))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [reload])

  useEffect(() => {
    if (!projectId) {
      return undefined
    }
    const controller = new AbortController()
    getProjectDatasets(projectId, controller.signal)
      .then((datasetList) => {
        setDatasets(datasetList)
        setDatasetId((current) => (
          datasetList.some((dataset) => String(dataset.id) === current)
            ? current
            : String(datasetList[0]?.id ?? '')
        ))
      })
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') setError(errorMessage(requestError))
      })
    return () => controller.abort()
  }, [projectId, reload])

  useEffect(() => {
    if (!datasetId) {
      return undefined
    }
    const controller = new AbortController()
    getDatasetFiles(datasetId, controller.signal)
      .then((fileList) => {
        setFiles(fileList)
        setSelectedColumns((current) => {
          const next = { ...current }
          for (const item of fileList) {
            const columns = item.profile_result?.tables?.[0]?.columns ?? []
            if (!columns.some((column) => column.name === next[item.id])) {
              next[item.id] = columns[0]?.name ?? ''
            }
          }
          return next
        })
      })
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') setError(errorMessage(requestError))
      })
    return () => controller.abort()
  }, [datasetId, reload])

  async function handleCreateProfile(event) {
    event.preventDefault()
    setBusy('profile')
    setError('')
    setNotice('')
    try {
      const created = await createLocalWorkspaceProfile({
        display_name: profileName,
        email: profileEmail,
      })
      setProfile(created)
      setProfileConfigured(true)
      setNotice('Local workspace profile created. This is not an authenticated account.')
      setReload((value) => value + 1)
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setBusy('')
    }
  }

  async function handleCreateProject(event) {
    event.preventDefault()
    setBusy('project')
    setError('')
    setNotice('')
    try {
      const created = await createProject({ name: projectName.trim() })
      setProjectName('')
      setProjects((current) => [...current, created])
      setProjectId(String(created.id))
      setNotice(`Project "${created.name}" created.`)
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setBusy('')
    }
  }

  async function uploadToDataset(targetDatasetId, file) {
    const response = await uploadDatasetFile(targetDatasetId, file)
    setDatasetId(String(targetDatasetId))
    setReload((value) => value + 1)
    setNotice(`Uploaded ${response.file.filename} and completed ingestion.`)
  }

  async function handleCreateDataset(event) {
    event.preventDefault()
    if (!projectId || !newDatasetFile) {
      setError('Choose a project and a supported file before creating a dataset.')
      return
    }
    setBusy('new-dataset')
    setError('')
    setNotice('')
    try {
      const created = await createDataset(projectId, { name: datasetName.trim() })
      setDatasets((current) => [...current, created])
      setDatasetId(String(created.id))
      setDatasetName('')
      try {
        await uploadToDataset(created.id, newDatasetFile)
        setNewDatasetFile(null)
      } catch (uploadError) {
        setError(
          `Dataset created, but its file was not uploaded: ${errorMessage(uploadError)} ` +
          'The dataset is retained; select it below and retry with Add a file.',
        )
      }
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setBusy('')
    }
  }

  async function handleAdditionalUpload(event) {
    event.preventDefault()
    if (!datasetId || !additionalFile) {
      setError('Choose a dataset and a supported file to upload.')
      return
    }
    setBusy('upload')
    setError('')
    setNotice('')
    try {
      await uploadToDataset(datasetId, additionalFile)
      setAdditionalFile(null)
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setBusy('')
    }
  }

  async function handleNormalize(file) {
    setBusy(`normalize-${file.id}`)
    setError('')
    setNotice('')
    try {
      const result = await normalizeDatasetFile(datasetId, file.id)
      setNotice(
        result.status === 'Ready'
          ? `Normalized ${file.filename}; the verified artifact is ready for analysis.`
          : `Normalization status for ${file.filename}: ${result.status}.`,
      )
      setReload((value) => value + 1)
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setBusy('')
    }
  }

  async function handleAnalyze(file) {
    const column = selectedColumns[file.id]
    if (!column) {
      setError('Choose a column before running EDA.')
      return
    }
    setBusy(`eda-${file.id}`)
    setError('')
    setNotice('')
    setAnalysis(null)
    try {
      const result = await createDatasetEda(datasetId, file.id, {
        analysis_type: 'univariate',
        selected_columns: [column],
      })
      setAnalysis(result)
      setNotice(`EDA completed on ${file.filename} using the backend execution target.`)
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setBusy('')
    }
  }

  if (loading) {
    return <section className="panel data-workspace"><p role="status">Loading workspace data…</p></section>
  }

  return (
    <div className="data-workspace">
      {error && <p className="data-alert data-alert-error" role="alert">{error}</p>}
      {notice && <p className="data-alert data-alert-success" role="status">{notice}</p>}

      {!profileConfigured && (
        <section className="panel data-panel">
          <p className="eyebrow">First-time setup</p>
          <h2>Create a local workspace profile</h2>
          <p className="muted">
            This local-only foundation has no sign-in or authentication. The profile is stored
            in the existing InsightOS database and owns projects created in this workspace.
          </p>
          <form className="data-form" onSubmit={handleCreateProfile}>
            <label>
              Display name
              <input
                required
                maxLength={255}
                value={profileName}
                onChange={(event) => setProfileName(event.target.value)}
                autoComplete="name"
              />
            </label>
            <label>
              Email identity
              <input
                required
                type="email"
                maxLength={255}
                value={profileEmail}
                onChange={(event) => setProfileEmail(event.target.value)}
                autoComplete="email"
              />
            </label>
            <button className="button button-primary" type="submit" disabled={busy === 'profile'}>
              {busy === 'profile' ? 'Creating profile…' : 'Create local profile'}
            </button>
          </form>
        </section>
      )}

      {profileMessage && <p className="data-alert data-alert-error" role="alert">{profileMessage}</p>}

      {profileConfigured && profile && (
        <p className="data-profile-note">
          Local workspace owner: <strong>{profile.display_name || profile.email}</strong>
          <span>No authentication is configured.</span>
        </p>
      )}

      {page === 'projects' ? (
        <>
          <section className="panel data-panel">
            <div className="data-panel-heading">
              <div>
                <p className="eyebrow">Workspace</p>
                <h2>Projects</h2>
              </div>
            </div>
            {projects.length > 0 ? (
              <ul className="data-list">
                {projects.map((project) => (
                  <li key={project.id}>
                    <strong>{project.name}</strong>
                    <span>{datasets.length && String(project.id) === projectId ? `${datasets.length} dataset(s)` : 'Project'}</span>
                  </li>
                ))}
              </ul>
            ) : profileConfigured ? (
              <p className="muted">No projects yet. Create one to organize uploaded datasets.</p>
            ) : (
              <p className="muted">Create a local profile above before creating your first project.</p>
            )}
            <form className="data-form data-inline-form" onSubmit={handleCreateProject}>
              <label>
                Project name
                <input
                  required
                  maxLength={255}
                  value={projectName}
                  onChange={(event) => setProjectName(event.target.value)}
                  disabled={!profileConfigured || busy === 'project'}
                />
              </label>
              <button className="button button-primary" type="submit" disabled={!profile || busy === 'project'}>
                {busy === 'project' ? 'Creating…' : 'Create project'}
              </button>
            </form>
          </section>
        </>
      ) : (
        <>
          <section className="panel data-panel">
            <div className="data-panel-heading">
              <div>
                <p className="eyebrow">Data</p>
                <h2>Upload a dataset</h2>
              </div>
              {projects.length === 0 && (
                <a className="text-link" href="#projects">Create a project first</a>
              )}
            </div>
            {projects.length > 0 ? (
              <>
                <label className="data-field">
                  Project
                  <select
                    value={projectId}
                    onChange={(event) => {
                      setError('')
                      setProjectId(event.target.value)
                    }}
                  >
                    {projects.map((project) => (
                      <option key={project.id} value={project.id}>{project.name}</option>
                    ))}
                  </select>
                </label>
                <form className="data-form upload-form" onSubmit={handleCreateDataset}>
                  <label>
                    New dataset name
                    <input
                      required
                      maxLength={255}
                      value={datasetName}
                      onChange={(event) => setDatasetName(event.target.value)}
                      disabled={busy === 'new-dataset'}
                    />
                  </label>
                  <label>
                    File ({supportedFormats})
                    <input
                      required
                      type="file"
                      accept=".csv,.tsv,.xlsx,.json,.parquet,.xml"
                      onChange={(event) => setNewDatasetFile(event.target.files?.[0] ?? null)}
                      disabled={busy === 'new-dataset'}
                    />
                  </label>
                  <button className="button button-primary" type="submit" disabled={busy === 'new-dataset' || !profile}>
                    {busy === 'new-dataset' ? 'Creating and uploading…' : 'Create dataset and upload'}
                  </button>
                </form>
              </>
            ) : (
              <p className="muted">
                {profileConfigured
                  ? 'Create a project before uploading a dataset.'
                  : 'Set up the local profile above, then create a project to upload data.'}
              </p>
            )}
          </section>

          {projects.length > 0 && (
            <section className="panel data-panel">
              <div className="data-panel-heading">
                <div><p className="eyebrow">Stored data</p><h2>Datasets and files</h2></div>
              </div>
              {datasets.length === 0 ? (
                <p className="muted">This project has no datasets yet.</p>
              ) : (
                <>
                  <label className="data-field">
                    Dataset
                    <select
                      value={datasetId}
                      onChange={(event) => {
                        setError('')
                        setAnalysis(null)
                        setDatasetId(event.target.value)
                      }}
                    >
                      {datasets.map((dataset) => (
                        <option key={dataset.id} value={dataset.id}>{dataset.name} · {dataset.status}</option>
                      ))}
                    </select>
                  </label>
                  <ul className="data-list file-list">
                    {files.map((file) => {
                      const columns = file.profile_result?.tables?.[0]?.columns ?? []
                      return (
                        <li key={file.id} className="data-file-row">
                          <div className="data-file-summary">
                            <strong>{file.filename}</strong>
                            <span>
                              {file.status} · {file.detected_format?.toUpperCase() ?? 'format pending'}
                              {Number.isInteger(file.parsing_result?.row_count)
                                ? ` · ${file.parsing_result.row_count} rows`
                                : ''}
                            </span>
                          </div>
                          {file.status === 'Ready' && file.normalization_status !== 'Ready' && (
                            <button
                              className="button button-secondary"
                              type="button"
                              onClick={() => handleNormalize(file)}
                              disabled={Boolean(busy)}
                            >
                              {busy === `normalize-${file.id}` ? 'Normalizing…' : 'Normalize'}
                            </button>
                          )}
                          {file.status === 'Ready' && file.normalization_status === 'Ready' && columns.length > 0 && (
                            <div className="data-analysis-controls">
                              <label>
                                Column
                                <select
                                  value={selectedColumns[file.id] ?? ''}
                                  onChange={(event) => setSelectedColumns((current) => ({
                                    ...current,
                                    [file.id]: event.target.value,
                                  }))}
                                >
                                  {columns.map((column) => (
                                    <option key={column.name} value={column.name}>{column.name}</option>
                                  ))}
                                </select>
                              </label>
                              <button
                                className="button button-secondary"
                                type="button"
                                onClick={() => handleAnalyze(file)}
                                disabled={Boolean(busy)}
                              >
                                {busy === `eda-${file.id}` ? 'Analyzing…' : 'Run EDA'}
                              </button>
                            </div>
                          )}
                          {file.status !== 'Ready' && file.error_message && (
                            <span className="data-error-note">{file.error_message}</span>
                          )}
                        </li>
                      )
                    })}
                    {files.length === 0 && <li className="muted">No files in this dataset yet.</li>}
                  </ul>
                  <form className="data-form data-inline-form" onSubmit={handleAdditionalUpload}>
                    <label>
                      Add a file ({supportedFormats})
                      <input
                        type="file"
                        accept=".csv,.tsv,.xlsx,.json,.parquet,.xml"
                        onChange={(event) => setAdditionalFile(event.target.files?.[0] ?? null)}
                        disabled={Boolean(busy)}
                      />
                    </label>
                    <button className="button button-secondary" type="submit" disabled={Boolean(busy) || !additionalFile}>
                      {busy === 'upload' ? 'Uploading…' : 'Upload file'}
                    </button>
                  </form>
                </>
              )}
            </section>
          )}

          {analysis?.result && (
            <section className="panel data-panel" aria-live="polite">
              <p className="eyebrow">Verified analysis response</p>
              <h2>EDA result</h2>
              <p className="muted">
                {analysis.result.rows_analyzed} rows analyzed ·
                {' '}{analysis.result.source?.artifact_sha256 ? 'normalized artifact checksum recorded' : 'artifact checksum unavailable'}
                {' '}· execution target: {analysis.execution_placement?.target_id ?? 'unavailable'}
              </p>
              <pre className="data-result-json">{JSON.stringify(analysis.result.columns, null, 2)}</pre>
            </section>
          )}
        </>
      )}
    </div>
  )
}

export default DataWorkspace
