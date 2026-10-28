import { useEffect, useRef, useState } from 'react'
import {
  createDataset,
  createDatasetEda,
  createDashboard,
  createLocalWorkspaceProfile,
  createProject,
  createMonthlyForecast,
  createSupervisedEvaluation,
  executeAnalyticsPlan,
  getMlTaskSuitability,
  getLatestSupervisedEvaluation,
  getDatasetFiles,
  getDashboards,
  getLocalWorkspaceProfile,
  getMonthlyForecast,
  getProjects,
  getProjectDatasets,
  normalizeDatasetFile,
  saveAnalyticsResultToDashboard,
  saveForecastToDashboard,
  uploadDatasetFile,
} from './api'
import { ChartCard } from './Dashboards'

const supportedFormats = '.csv, .tsv, .xlsx, .json, .parquet, .xml'
const CREATE_FORECAST_DASHBOARD = '__create_forecast_dashboard__'
const EDA_REPORT_OPERATION_ID = 'eda-evidence-report'

function errorMessage(error) {
  return error instanceof Error ? error.message : 'The request could not be completed.'
}

function statusLabel(status) {
  if (!status) return 'Unavailable'
  return status.replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ')
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
  const [loadedFilesDatasetId, setLoadedFilesDatasetId] = useState('')
  const [filesError, setFilesError] = useState('')
  const [projectName, setProjectName] = useState('')
  const [datasetName, setDatasetName] = useState('')
  const [newDatasetFile, setNewDatasetFile] = useState(null)
  const [additionalFile, setAdditionalFile] = useState(null)
  const [selectedColumns, setSelectedColumns] = useState({})
  const [analysis, setAnalysis] = useState(null)
  const [mlSuitability, setMlSuitability] = useState({})
  const [anomalySelections, setAnomalySelections] = useState({})
  const [anomalyRun, setAnomalyRun] = useState(null)
  const [supervisedSelections, setSupervisedSelections] = useState({})
  const [supervisedRuns, setSupervisedRuns] = useState({})
  const [edaReport, setEdaReport] = useState(null)
  const [edaReportReference, setEdaReportReference] = useState(null)
  const [edaDashboardId, setEdaDashboardId] = useState(CREATE_FORECAST_DASHBOARD)
  const [newEdaDashboardTitle, setNewEdaDashboardTitle] = useState('')
  const [forecastSelections, setForecastSelections] = useState({})
  const [forecastRun, setForecastRun] = useState(null)
  const [forecastDashboards, setForecastDashboards] = useState([])
  const [forecastDashboardId, setForecastDashboardId] = useState(CREATE_FORECAST_DASHBOARD)
  const [newForecastDashboardTitle, setNewForecastDashboardTitle] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [savedDashboardHref, setSavedDashboardHref] = useState('')
  const selectedDatasetId = useRef('')
  const filesLoading = Boolean(datasetId) && loadedFilesDatasetId !== datasetId

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      getLocalWorkspaceProfile(controller.signal),
      getProjects(controller.signal),
    ])
      .then(([workspace, projectList]) => {
        if (controller.signal.aborted) return
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
  }, [])

  useEffect(() => {
    if (!projectId) {
      return undefined
    }
    const controller = new AbortController()
    getProjectDatasets(projectId, controller.signal)
      .then((datasetList) => {
        if (controller.signal.aborted) return
        setDatasets(datasetList)
        const nextDatasetId = datasetList.some(
          (dataset) => String(dataset.id) === selectedDatasetId.current,
        )
          ? selectedDatasetId.current
          : String(datasetList[0]?.id ?? '')
        selectedDatasetId.current = nextDatasetId
        setDatasetId(nextDatasetId)
      })
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') setError(errorMessage(requestError))
      })
    return () => controller.abort()
  }, [projectId])

  useEffect(() => {
    if (!datasetId) {
      return undefined
    }
    const controller = new AbortController()
    getDatasetFiles(datasetId, controller.signal)
      .then((fileList) => {
        if (controller.signal.aborted) return
        setFiles(fileList)
        setLoadedFilesDatasetId(datasetId)
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
        if (requestError.name !== 'AbortError') {
          setFilesError(errorMessage(requestError))
          setError(errorMessage(requestError))
          setLoadedFilesDatasetId(datasetId)
        }
      })
    return () => controller.abort()
  }, [datasetId])

  useEffect(() => {
    if (!datasetId || loadedFilesDatasetId !== datasetId) return undefined
    const controller = new AbortController()
    const readyFiles = files.filter(
      (file) => file.status === 'Ready' && file.normalization_status === 'Ready',
    )
    Promise.all(readyFiles.map(async (file) => {
      try {
        const saved = await getLatestSupervisedEvaluation(
          datasetId,
          file.id,
          controller.signal,
        )
        if (!controller.signal.aborted && selectedDatasetId.current === String(datasetId)) {
          setSupervisedRuns((current) => ({ ...current, [file.id]: saved }))
        }
      } catch (requestError) {
        if (
          requestError.name !== 'AbortError'
          && requestError.status !== 404
          && !controller.signal.aborted
          && selectedDatasetId.current === String(datasetId)
        ) {
          setSupervisedRuns((current) => ({
            ...current,
            [file.id]: { status: 'unavailable', message: errorMessage(requestError) },
          }))
        }
      }
    }))
    return () => controller.abort()
  }, [datasetId, files, loadedFilesDatasetId])

  async function refreshDatasetFiles(targetDatasetId, action) {
    try {
      const fileList = await getDatasetFiles(targetDatasetId)
      if (selectedDatasetId.current === String(targetDatasetId)) {
        setFiles(fileList)
        setLoadedFilesDatasetId(String(targetDatasetId))
        setFilesError('')
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
      }
    } catch (requestError) {
      if (requestError.name !== 'AbortError') {
        setFilesError(errorMessage(requestError))
        setError(`File status could not be refreshed after ${action.toLowerCase()}: ${errorMessage(requestError)}`)
      }
    }
  }

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
      try {
        const projectList = await getProjects()
        setProjects(projectList)
        setProjectId((current) => (
          projectList.some((project) => String(project.id) === current)
            ? current
            : String(projectList[0]?.id ?? '')
        ))
      } catch (requestError) {
        setError(`Profile created, but projects could not be loaded: ${errorMessage(requestError)}`)
      }
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
    setSavedDashboardHref('')
    setFilesError('')
    const response = await uploadDatasetFile(targetDatasetId, file)
    selectedDatasetId.current = String(targetDatasetId)
    setDatasetId(String(targetDatasetId))
    if (response.file.status === 'Ready') {
      setNotice(`Uploaded ${response.file.filename}; ingestion is ready for normalization.`)
    } else if (response.file.status === 'Failed') {
      setError(
        `Ingestion failed for ${response.file.filename}: ` +
        `${response.file.error_message || 'check the file and retry the upload.'}`,
      )
    } else {
      setNotice(
        `Upload accepted for ${response.file.filename}; ingestion status: ${statusLabel(response.file.status)}.`,
      )
    }
    await refreshDatasetFiles(targetDatasetId, 'Upload')
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
      selectedDatasetId.current = String(created.id)
      setDatasets((current) => [
        ...current.filter((dataset) => dataset.id !== created.id),
        created,
      ])
      setDatasetId(String(created.id))
      setDatasetName('')
      try {
        await uploadToDataset(created.id, newDatasetFile)
        setNewDatasetFile(null)
      } catch (uploadError) {
        await refreshDatasetFiles(created.id, 'Upload')
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
      await refreshDatasetFiles(datasetId, 'Upload')
    } finally {
      setBusy('')
    }
  }

  async function handleNormalize(file) {
    const targetDatasetId = file.dataset_id ?? Number(datasetId)
    setSavedDashboardHref('')
    setFilesError('')
    setAnalysis(null)
    setEdaReport(null)
    setEdaReportReference(null)
    setAnomalyRun(null)
    setMlSuitability((current) => {
      const next = { ...current }
      delete next[file.id]
      return next
    })
    setSupervisedRuns((current) => {
      const next = { ...current }
      delete next[file.id]
      return next
    })
    setBusy(`normalize-${file.id}`)
    setError('')
    setNotice('')
    try {
      const result = await normalizeDatasetFile(targetDatasetId, file.id)
      if (result.status === 'Ready') {
        setNotice(`Normalized ${file.filename}; the verified artifact is ready for analysis.`)
      } else if (result.status === 'Failed') {
        setError(
          `Normalization failed for ${file.filename}: ` +
          `${result.error?.message || 'check the source and retry normalization.'}`,
        )
      } else {
        setNotice(
          `Normalization status for ${file.filename}: ${statusLabel(result.status)}.`,
        )
      }
      await refreshDatasetFiles(targetDatasetId, 'Normalization')
    } catch (requestError) {
      setError(errorMessage(requestError))
      await refreshDatasetFiles(targetDatasetId, 'Normalization')
    } finally {
      setBusy('')
    }
  }

  async function handleAssessMlSuitability(file) {
    const targetDatasetId = file.dataset_id ?? Number(datasetId)
    setBusy(`ml-suitability-${file.id}`)
    setError('')
    setNotice('')
    try {
      const result = await getMlTaskSuitability(targetDatasetId, file.id)
      if (selectedDatasetId.current !== String(targetDatasetId)) return
      setMlSuitability((current) => ({ ...current, [file.id]: result }))
      const anomalyTask = result.tasks?.find((task) => task.task_type === 'anomaly_detection')
      setAnomalySelections((current) => ({
        ...current,
        [file.id]: current[file.id] ?? anomalyTask?.candidate_columns?.[0] ?? '',
      }))
      const regression = result.tasks?.find(
        (task) => task.task_type === 'supervised_regression',
      )
      setSupervisedSelections((current) => ({
        ...current,
        [file.id]: current[file.id] ?? {
          taskType: 'regression',
          target: regression?.candidate_target_columns?.[0] ?? '',
          features: [],
          timeColumn: '',
          groupColumn: '',
          confirmations: [],
        },
      }))
      setNotice(`ML task suitability assessed for ${file.filename}; targets were not inferred.`)
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setBusy('')
    }
  }

  async function handleSupervisedEvaluation(file) {
    const selection = supervisedSelections[file.id] ?? {}
    if (!selection.target || !selection.features?.length) {
      setError('Choose a target and at least one feature before evaluating.')
      return
    }
    if (selection.features.includes(selection.target)) {
      setError('The target cannot also be selected as a feature.')
      return
    }
    setBusy(`supervised-${file.id}`)
    setError('')
    setNotice('')
    try {
      const result = await createSupervisedEvaluation({
        dataset_id: Number(datasetId),
        file_id: file.id,
        table_index: 0,
        task_type: selection.taskType,
        target_column: selection.target,
        feature_columns: selection.features,
        time_column: selection.timeColumn || null,
        group_column: selection.groupColumn || null,
        confirmed_suspicious_features: selection.confirmations ?? [],
      })
      if (selectedDatasetId.current !== String(datasetId)) return
      setSupervisedRuns((current) => ({ ...current, [file.id]: result }))
      if (result.status === 'completed' && result.result) {
        setNotice(`Supervised evaluation completed for ${file.filename}; test metrics were held out from candidate selection.`)
      } else {
        setError(result.message || result.error_code || `Evaluation status: ${statusLabel(result.status)}.`)
      }
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setBusy('')
    }
  }

  async function handleAnomalyScreen(file) {
    const selected = anomalySelections[file.id]
    if (!selected) {
      setError('Choose an available numeric column before screening for anomalies.')
      return
    }
    setBusy(`anomaly-${file.id}`)
    setError('')
    setNotice('')
    setAnalysis(null)
    setEdaReport(null)
    setEdaReportReference(null)
    try {
      const result = await createDatasetEda(datasetId, file.id, {
        analysis_type: 'anomaly_detection',
        selected_columns: [selected],
      })
      if (selectedDatasetId.current !== String(datasetId)) return
      setAnomalyRun(result)
      setNotice(`Robust anomaly screening completed for ${file.filename}. Flags indicate unusual values, not proven errors.`)
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
    setSavedDashboardHref('')
    setError('')
    setNotice('')
    setAnalysis(null)
    setMlSuitability({})
    setAnomalyRun(null)
    setEdaReport(null)
    setEdaReportReference(null)
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

  async function handleEdaReport() {
    if (!analysis?.id || !analysis.file_id) return
    setBusy('eda-report')
    setSavedDashboardHref('')
    setError('')
    try {
      const plan = await executeAnalyticsPlan({
        operations: [{
          operation_id: EDA_REPORT_OPERATION_ID,
          operation: 'generate_eda_evidence_report',
          parameters: {
            dataset_id: Number(datasetId),
            file_id: analysis.file_id,
            analysis_id: analysis.id,
          },
          depends_on: [],
        }],
      })
      const operation = plan.operations?.find(
        (item) => item.operation_id === EDA_REPORT_OPERATION_ID,
      )
      if (
        plan.status !== 'completed'
        || operation?.status !== 'completed'
        || !operation.result
        || !operation.result_reference?.workflow_id
        || !operation.result_reference?.task_id
      ) {
        throw new Error(
          operation?.reason
            ?? `EDA evidence report status: ${operation?.status ?? plan.status ?? 'unavailable'}.`,
        )
      }
      setEdaReport(operation.result)
      setEdaReportReference(operation.result_reference)
      const savedDashboards = await getDashboards()
      setForecastDashboards(savedDashboards)
      setEdaDashboardId(savedDashboards[0]?.id ?? CREATE_FORECAST_DASHBOARD)
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setBusy('')
    }
  }

  async function handleSaveEdaReportToDashboard() {
    if (
      !edaReportReference?.workflow_id
      || !edaReportReference?.task_id
      || !edaReport
    ) {
      setError('Only a completed, verified EDA evidence report can be saved to a dashboard.')
      return
    }
    if (edaDashboardId === CREATE_FORECAST_DASHBOARD && !newEdaDashboardTitle.trim()) {
      setError('Enter a name for the new dashboard.')
      return
    }
    setBusy('eda-dashboard-save')
    setError('')
    setNotice('')
    try {
      let dashboardId = edaDashboardId
      let dashboardTitle = forecastDashboards.find(
        (dashboard) => dashboard.id === dashboardId,
      )?.title
      if (dashboardId === CREATE_FORECAST_DASHBOARD) {
        const created = await createDashboard({
          title: newEdaDashboardTitle.trim(),
          description: null,
          analysis_request: null,
          chart_requests: [],
        })
        dashboardId = created.id
        dashboardTitle = created.title
        setForecastDashboards((current) => [created, ...current])
        setEdaDashboardId(created.id)
        setNewEdaDashboardTitle('')
      }
      await saveAnalyticsResultToDashboard(dashboardId, {
        workflow_id: edaReportReference.workflow_id,
        task_id: edaReportReference.task_id,
      })
      setSavedDashboardHref(`#dashboards?dashboardId=${encodeURIComponent(dashboardId)}`)
      setNotice(`Saved this EDA evidence report to dashboard "${dashboardTitle}".`)
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setBusy('')
    }
  }

  async function handleMonthlyForecast(file) {
    const selection = forecastSelections[file.id] ?? {}
    if (!selection.dateColumn || !selection.targetColumn) {
      setError('Choose both a time column and a numeric target before forecasting.')
      return
    }
    if (selection.dateColumn === selection.targetColumn) {
      setError('The time column and target column must be different.')
      return
    }
    setBusy(`forecast-${file.id}`)
    setSavedDashboardHref('')
    setError('')
    setNotice('')
    setForecastRun(null)
    try {
      const created = await createMonthlyForecast({
        dataset_id: Number(datasetId),
        file_id: file.id,
        table_index: 0,
        date_column: selection.dateColumn,
        target_column: selection.targetColumn,
        aggregation: 'sum',
        horizon: Number(selection.horizon ?? 3),
      })
      if (created.status !== 'completed') {
        throw new Error(
          created.result?.summary
            ?? `Forecast execution status: ${created.status ?? 'unavailable'}.`,
        )
      }
      const persisted = await getMonthlyForecast(created.workflow_id)
      if (
        persisted.task?.status !== 'completed'
        || !persisted.task.result
      ) {
        throw new Error(
          persisted.task?.result?.summary
            ?? `Persisted forecast status: ${persisted.task?.status ?? 'unavailable'}.`,
        )
      }
      setForecastRun({
        workflow_id: persisted.workflow_id,
        status: persisted.status,
        created_at: persisted.created_at,
        completed_at: persisted.completed_at,
        result: persisted.task.result,
      })
      setNotice(`Monthly forecast for ${file.filename} completed and its source was revalidated.`)
      const savedDashboards = await getDashboards()
      setForecastDashboards(savedDashboards)
      setForecastDashboardId(
        savedDashboards[0]?.id ?? CREATE_FORECAST_DASHBOARD,
      )
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setBusy('')
    }
  }

  async function handleSaveForecastToDashboard() {
    if (
      forecastRun?.status !== 'completed'
      || !forecastRun.result
      || !forecastRun.workflow_id
    ) {
      setError('Only a completed, verified forecast can be saved to a dashboard.')
      return
    }
    if (
      forecastDashboardId === CREATE_FORECAST_DASHBOARD
      && !newForecastDashboardTitle.trim()
    ) {
      setError('Enter a name for the new dashboard.')
      return
    }
    setBusy('forecast-dashboard-save')
    setError('')
    setNotice('')
    try {
      let dashboardId = forecastDashboardId
      let dashboardTitle = forecastDashboards.find(
        (dashboard) => dashboard.id === dashboardId,
      )?.title
      if (dashboardId === CREATE_FORECAST_DASHBOARD) {
        const created = await createDashboard({
          title: newForecastDashboardTitle.trim(),
          description: null,
          analysis_request: null,
          chart_requests: [],
        })
        dashboardId = created.id
        dashboardTitle = created.title
        setForecastDashboards((current) => [created, ...current])
        setForecastDashboardId(created.id)
        setNewForecastDashboardTitle('')
      }
      await saveForecastToDashboard(dashboardId, forecastRun.workflow_id)
      setSavedDashboardHref(`#dashboards?dashboardId=${encodeURIComponent(dashboardId)}`)
      setNotice(`Saved this forecast to dashboard "${dashboardTitle}".`)
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
      {notice && (
        <p className="data-alert data-alert-success" role="status">
          {notice}
          {savedDashboardHref && <> {' '}<a className="text-link" href={savedDashboardHref}>Open saved dashboard</a> to revisit the original result.</>}
        </p>
      )}

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

      {page === 'datasets' && profileConfigured && (
        <section className="panel data-panel" aria-label="Guided analytics journey">
          <p className="eyebrow">Guided analytics journey</p>
          <h2>From source data to reusable evidence</h2>
          <p className="muted">
            Upload a file, normalize it, assess the available ML task types, then choose a
            single-file analysis or a multi-source report. EDA evidence reports reference a stored EDA run; forecasts use their own
            persisted workflow. These operations do not pass results between each other.
          </p>
          <ol className="data-journey-steps">
            <li><strong>Ingest and prepare</strong><span>Upload files below and normalize each source before analysis.</span></li>
            <li><strong>Analyze</strong><span>Assess task suitability, run EDA or an explicitly selected anomaly screen, or use the existing forecasting path.</span></li>
            <li><strong>Report across sources</strong><span>Choose exact sources, join keys, and metrics in the dashboard builder.</span></li>
            <li><strong>Save and revisit</strong><span>Save verified EDA evidence or a completed forecast to a dashboard, then reload its original result.</span></li>
          </ol>
          <a className="text-link" href="#dashboards">Open dashboard builder</a>
        </section>
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
                    disabled={Boolean(busy)}
                    onChange={(event) => {
                      setError('')
                      setNotice('')
                      setSavedDashboardHref('')
                      selectedDatasetId.current = ''
                      setDatasetId('')
                      setDatasets([])
                      setFiles([])
                      setFilesError('')
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
                      disabled={Boolean(busy)}
                      onChange={(event) => {
                        setError('')
                        setNotice('')
                        setSavedDashboardHref('')
                        selectedDatasetId.current = event.target.value
                        setFiles([])
                        setFilesError('')
                        setAnalysis(null)
                        setEdaReport(null)
                        setEdaReportReference(null)
                        setAnomalyRun(null)
                        setSupervisedRuns({})
                        setForecastRun(null)
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
                      const forecastSelection = forecastSelections[file.id] ?? {}
                      const forecastHorizon = Number(forecastSelection.horizon ?? 3)
                      const suitability = mlSuitability[file.id]
                      const anomalyTask = suitability?.tasks?.find(
                        (task) => task.task_type === 'anomaly_detection',
                      )
                      const supervisedSelection = supervisedSelections[file.id] ?? {}
                      const supervisedTask = suitability?.tasks?.find(
                        (task) => task.task_type === `supervised_${supervisedSelection.taskType ?? 'regression'}`,
                      )
                      const supervisedTargetOptions = supervisedTask?.candidate_target_columns ?? []
                      const supervisedFeatureOptions = (suitability?.columns ?? []).filter(
                        (column) => ['numeric', 'categorical'].includes(column.kind)
                          && !column.constant
                          && column.name !== supervisedSelection.target
                          && column.name !== supervisedSelection.timeColumn
                          && column.name !== supervisedSelection.groupColumn,
                      )
                      return (
                        <li key={file.id} className="data-file-row">
                          <div className="data-file-summary">
                            <strong>{file.filename}</strong>
                            <span>
                              Ingestion: {statusLabel(file.status)} · Normalization: {statusLabel(file.normalization_status)}
                              {' '}· {file.detected_format?.toUpperCase() ?? 'format pending'}
                              {Number.isInteger(file.parsing_result?.row_count)
                                ? ` · ${file.parsing_result.row_count} rows`
                                : ''}
                            </span>
                          </div>
                          {file.status !== 'Ready' && (
                            <span className="data-error-note">
                              {file.error_message || 'Analysis is unavailable until ingestion succeeds.'}
                            </span>
                          )}
                          {file.status === 'Ready' && file.normalization_status !== 'Ready' && (
                            <>
                              <span className="data-error-note">
                                {file.normalization_status === 'Processing'
                                  ? 'Normalization is in progress.'
                                  : file.normalization_status === 'Failed'
                                    ? file.normalization_error_message || 'Normalization failed; retry after checking the source.'
                                    : 'Normalize this file to create the verified artifact required for analysis.'}
                              </span>
                              <button
                                className="button button-secondary"
                                type="button"
                                onClick={() => handleNormalize(file)}
                                disabled={Boolean(busy) || file.normalization_status === 'Processing'}
                              >
                                {busy === `normalize-${file.id}`
                                  ? 'Normalizing…'
                                  : file.normalization_status === 'Failed'
                                    ? 'Retry normalization'
                                    : file.normalization_status === 'Processing'
                                      ? 'Normalization in progress…'
                                      : 'Normalize'}
                              </button>
                            </>
                          )}
                          {file.status === 'Ready' && file.normalization_status === 'Ready' && columns.length > 0 && (
                            <>
                              <button
                                className="button button-secondary"
                                type="button"
                                onClick={() => handleAssessMlSuitability(file)}
                                disabled={Boolean(busy)}
                              >
                                {busy === `ml-suitability-${file.id}`
                                  ? 'Assessing ML tasks…'
                                  : 'Assess ML task suitability'}
                              </button>
                              {suitability && (
                                <div className="ml-suitability" aria-live="polite">
                                  <p className="muted">
                                    {suitability.row_count} rows · {suitability.column_count} columns ·
                                    {' '}source fingerprint {suitability.source?.source_fingerprint?.slice(0, 12) ?? 'unavailable'}
                                  </p>
                                  {suitability.tasks.map((task) => (
                                    <p className="muted" key={task.task_type}>
                                      <strong>{task.task_type.replaceAll('_', ' ')} — {statusLabel(task.status)}.</strong>
                                      {' '}{task.reason}
                                      {task.missing_prerequisites?.length
                                        ? ` Missing: ${task.missing_prerequisites.join(', ')}.`
                                        : ''}
                                    </p>
                                  ))}
                                  {suitability.limitations.map((limitation) => (
                                    <p className="muted" key={limitation}>{limitation}</p>
                                  ))}
                                  {anomalyTask?.status === 'needs_user_input' && (
                                    <div className="data-analysis-controls">
                                      <label>
                                        Numeric column for robust anomaly screening
                                        <select
                                          value={anomalySelections[file.id] ?? ''}
                                          onChange={(event) => setAnomalySelections((current) => ({
                                            ...current,
                                            [file.id]: event.target.value,
                                          }))}
                                          disabled={Boolean(busy)}
                                        >
                                          <option value="">Choose a numeric column</option>
                                          {anomalyTask.candidate_columns.map((column) => (
                                            <option key={column} value={column}>{column}</option>
                                          ))}
                                        </select>
                                      </label>
                                      <button
                                        className="button button-secondary"
                                        type="button"
                                        onClick={() => handleAnomalyScreen(file)}
                                        disabled={Boolean(busy) || !anomalySelections[file.id]}
                                      >
                                        {busy === `anomaly-${file.id}` ? 'Screening…' : 'Run anomaly screening'}
                                      </button>
                                    </div>
                                  )}
                                  {supervisedTask && (
                                    <section className="data-forecast-controls" aria-label="Supervised evaluation setup">
                                      <h3>Supervised evaluation</h3>
                                      <div className="data-analysis-controls">
                                        <label>
                                          Task type
                                          <select
                                            value={supervisedSelection.taskType ?? 'regression'}
                                            onChange={(event) => {
                                              const taskType = event.target.value
                                              const nextTask = suitability.tasks.find(
                                                (task) => task.task_type === `supervised_${taskType}`,
                                              )
                                              setSupervisedSelections((current) => ({
                                                ...current,
                                                [file.id]: {
                                                  ...current[file.id],
                                                  taskType,
                                                  target: nextTask?.candidate_target_columns?.[0] ?? '',
                                                  features: (current[file.id]?.features ?? [])
                                                    .filter((name) => name !== nextTask?.candidate_target_columns?.[0]),
                                                },
                                              }))
                                            }}
                                            disabled={Boolean(busy)}
                                          >
                                            <option value="regression">Regression</option>
                                            <option value="classification">Classification</option>
                                          </select>
                                        </label>
                                        <label>
                                          Explicit target
                                          <select
                                            value={supervisedSelection.target ?? ''}
                                            onChange={(event) => setSupervisedSelections((current) => ({
                                              ...current,
                                              [file.id]: {
                                                ...current[file.id],
                                                target: event.target.value,
                                                features: (current[file.id]?.features ?? [])
                                                  .filter((name) => name !== event.target.value
                                                    && name !== current[file.id]?.timeColumn
                                                    && name !== current[file.id]?.groupColumn),
                                              },
                                            }))}
                                            disabled={Boolean(busy) || !supervisedTargetOptions.length}
                                          >
                                            <option value="">Choose a target</option>
                                            {supervisedTargetOptions.map((column) => (
                                              <option key={column} value={column}>{column}</option>
                                            ))}
                                          </select>
                                        </label>
                                        <label>
                                          Explicit features (multi-select)
                                          <select
                                            multiple
                                            value={supervisedSelection.features ?? []}
                                            onChange={(event) => setSupervisedSelections((current) => ({
                                              ...current,
                                              [file.id]: {
                                                ...current[file.id],
                                                features: Array.from(
                                                  event.target.selectedOptions,
                                                  (option) => option.value,
                                                ),
                                              },
                                            }))}
                                            disabled={Boolean(busy)}
                                          >
                                            {supervisedFeatureOptions.map((column) => (
                                              <option key={column.name} value={column.name}>
                                                {column.name}{column.likely_identifier ? ' (likely identifier)' : ''}
                                              </option>
                                            ))}
                                          </select>
                                        </label>
                                        <label>
                                          Chronological time column (optional)
                                          <select
                                            value={supervisedSelection.timeColumn ?? ''}
                                            onChange={(event) => setSupervisedSelections((current) => ({
                                              ...current,
                                              [file.id]: {
                                                ...current[file.id],
                                                timeColumn: event.target.value,
                                                groupColumn: '',
                                                features: (current[file.id]?.features ?? [])
                                                  .filter((name) => name !== event.target.value),
                                              },
                                            }))}
                                            disabled={Boolean(busy)}
                                          >
                                            <option value="">Deterministic random split</option>
                                            {(suitability.columns ?? []).filter((column) => column.kind === 'datetime')
                                              .map((column) => <option key={column.name} value={column.name}>{column.name}</option>)}
                                          </select>
                                        </label>
                                        {!supervisedSelection.timeColumn && (
                                          <label>
                                            Group column (optional)
                                            <select
                                              value={supervisedSelection.groupColumn ?? ''}
                                              onChange={(event) => setSupervisedSelections((current) => ({
                                                ...current,
                                                [file.id]: {
                                                  ...current[file.id],
                                                  groupColumn: event.target.value,
                                                  features: (current[file.id]?.features ?? [])
                                                    .filter((name) => name !== event.target.value),
                                                },
                                              }))}
                                              disabled={Boolean(busy)}
                                            >
                                              <option value="">No group split</option>
                                              {(suitability.columns ?? []).filter((column) => (
                                                ['numeric', 'categorical'].includes(column.kind)
                                                && !column.constant
                                                && column.name !== supervisedSelection.target
                                                && column.name !== supervisedSelection.timeColumn
                                              ))
                                                .filter((column) => column.name !== supervisedSelection.target)
                                                .map((column) => <option key={column.name} value={column.name}>{column.name}</option>)}
                                            </select>
                                          </label>
                                        )}
                                      </div>
                                      {(supervisedSelection.features ?? []).map((feature) => (
                                        <label className="data-field" key={feature}>
                                          <input
                                            type="checkbox"
                                            checked={(supervisedSelection.confirmations ?? []).includes(feature)}
                                            onChange={(event) => setSupervisedSelections((current) => {
                                              const selection = current[file.id] ?? {}
                                              const confirmations = new Set(selection.confirmations ?? [])
                                              if (event.target.checked) confirmations.add(feature)
                                              else confirmations.delete(feature)
                                              return {
                                                ...current,
                                                [file.id]: {
                                                  ...selection,
                                                  confirmations: [...confirmations],
                                                },
                                              }
                                            })}
                                            disabled={Boolean(busy)}
                                          />
                                          Confirm {feature} is not target-derived or future-looking
                                        </label>
                                      ))}
                                      <p className="muted">
                                        Preprocessing is fitted on training data. A separate training-only validation split selects the candidate; the held-out test split is reserved for final metrics. Exact target copies are rejected.
                                      </p>
                                      <button
                                        className="button button-secondary"
                                        type="button"
                                        onClick={() => handleSupervisedEvaluation(file)}
                                        disabled={Boolean(busy)
                                          || !supervisedSelection.target
                                          || !supervisedSelection.features?.length}
                                      >
                                        {busy === `supervised-${file.id}` ? 'Evaluating…' : 'Run supervised evaluation'}
                                      </button>
                                    </section>
                                  )}
                                </div>
                              )}
                              {supervisedRuns[file.id] && (
                                <section className="panel data-panel" aria-live="polite">
                                  <p className="eyebrow">Saved supervised evaluation</p>
                                  <h3>{statusLabel(supervisedRuns[file.id].status)}</h3>
                                  {supervisedRuns[file.id].result ? (
                                    <>
                                      <p className="muted">
                                        {supervisedRuns[file.id].result.task_type} · target {supervisedRuns[file.id].result.target_column} ·
                                        {' '}features {supervisedRuns[file.id].result.feature_columns.join(', ')} ·
                                        {' '}held-out test n={supervisedRuns[file.id].result.evaluation.test_sample_count} ·
                                        {' '}selected {supervisedRuns[file.id].result.evaluation.test_result.selected_candidate}
                                      </p>
                                      <p className="muted">
                                        Model metrics {JSON.stringify(supervisedRuns[file.id].result.evaluation.test_result.model)} ·
                                        {' '}baseline metrics {JSON.stringify(supervisedRuns[file.id].result.evaluation.test_result.baseline)}
                                      </p>
                                      <p className="muted">
                                        Split: {supervisedRuns[file.id].result.evaluation.split_strategy.reason}
                                      </p>
                                      {supervisedRuns[file.id].result.warnings.map((warning) => (
                                        <p className="dashboard-warning" key={warning}>{warning}</p>
                                      ))}
                                      {supervisedRuns[file.id].result.limitations.map((limitation) => (
                                        <p className="muted" key={limitation}>{limitation}</p>
                                      ))}
                                    </>
                                  ) : (
                                    <p className="data-error-note">
                                      {supervisedRuns[file.id].message
                                        || supervisedRuns[file.id].error_code
                                        || 'The saved evaluation is stale or unavailable; rerun only after reviewing the source and configuration.'}
                                    </p>
                                  )}
                                </section>
                              )}
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
                                    {columns.map((column, index) => (
                                      <option
                                        key={column.id ?? `${column.name}-${index}`}
                                        value={column.name}
                                      >
                                        {column.name}
                                      </option>
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
                              <details className="data-forecast-controls">
                                <summary>Monthly forecasting</summary>
                                <div className="data-analysis-controls">
                                  <label>
                                    Time column
                                    <select
                                      required
                                      value={forecastSelection.dateColumn ?? ''}
                                      onChange={(event) => setForecastSelections((current) => ({
                                        ...current,
                                        [file.id]: {
                                          ...current[file.id],
                                          dateColumn: event.target.value,
                                        },
                                      }))}
                                    >
                                      <option value="">Choose a time column</option>
                                      {columns.map((column, index) => (
                                        <option
                                          key={column.id ?? `${column.name}-${index}`}
                                          value={column.name}
                                        >
                                          {column.name}
                                        </option>
                                      ))}
                                    </select>
                                  </label>
                                  <label>
                                    Numeric target
                                    <select
                                      required
                                      value={forecastSelection.targetColumn ?? ''}
                                      onChange={(event) => setForecastSelections((current) => ({
                                        ...current,
                                        [file.id]: {
                                          ...current[file.id],
                                          targetColumn: event.target.value,
                                        },
                                      }))}
                                    >
                                      <option value="">Choose a target column</option>
                                      {columns.map((column, index) => (
                                        <option
                                          key={column.id ?? `${column.name}-${index}`}
                                          value={column.name}
                                        >
                                          {column.name}
                                        </option>
                                      ))}
                                    </select>
                                  </label>
                                  <label>
                                    Horizon (months)
                                    <input
                                      type="number"
                                      required
                                      min="1"
                                      max="24"
                                      value={forecastSelection.horizon ?? 3}
                                      onChange={(event) => setForecastSelections((current) => ({
                                        ...current,
                                        [file.id]: {
                                          ...current[file.id],
                                          horizon: event.target.value,
                                        },
                                      }))}
                                    />
                                  </label>
                                  <button
                                    className="button button-secondary"
                                    type="button"
                                    onClick={() => handleMonthlyForecast(file)}
                                    disabled={Boolean(busy)
                                      || !forecastSelection.dateColumn
                                      || !forecastSelection.targetColumn
                                      || forecastSelection.dateColumn === forecastSelection.targetColumn
                                      || !Number.isInteger(forecastHorizon)
                                      || forecastHorizon < 1
                                      || forecastHorizon > 24}
                                  >
                                    {busy === `forecast-${file.id}` ? 'Forecasting…' : 'Run monthly forecast'}
                                  </button>
                                </div>
                                <p className="muted">
                                  Uses sum aggregation on the verified normalized artifact.
                                  Choose distinct columns. The backend validates date continuity
                                  and confirms that the target values are numeric.
                                </p>
                              </details>
                            </>
                          )}
                          {file.status === 'Ready'
                            && file.normalization_status === 'Ready'
                            && columns.length === 0 && (
                              <span className="data-error-note">
                                Normalization is ready, but no profile columns are available; EDA and forecasting are unavailable for this file.
                              </span>
                          )}
                        </li>
                      )
                    })}
                    {filesLoading && <li className="muted" role="status">Loading files…</li>}
                    {!filesLoading && filesError && (
                      <li className="data-error-note">File status unavailable: {filesError}</li>
                    )}
                    {!filesLoading && !filesError && files.length === 0 && (
                      <li className="muted">No files in this dataset yet.</li>
                    )}
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

          {forecastRun?.result && (
            <section className="panel data-panel" aria-live="polite">
              <p className="eyebrow">Persisted forecast result</p>
              <h2>{forecastRun.result.target_column} · next {forecastRun.result.horizon} months</h2>
              <p className="muted">
                Dataset {forecastRun.result.dataset_id} / file {forecastRun.result.file_id} ·
                {' '}{forecastRun.result.date_column} · sum aggregation ·
                {' '}source {forecastRun.result.source_fingerprint.slice(0, 12)} ·
                {' '}completed {forecastRun.completed_at ?? 'time unavailable'}
              </p>
              <p className="muted">
                {forecastRun.result.evaluation.method ?? 'Chronological holdout'} ·
                {' '}{forecastRun.result.evaluation.sample_count} evaluation period(s)
                {' '}({forecastRun.result.evaluation.period_start}–{forecastRun.result.evaluation.period_end})
              </p>
              <p className="muted">
                Measured holdout MAE {forecastRun.result.evaluation.candidate.mae.toFixed(2)} vs
                {' '}{forecastRun.result.evaluation.baseline_name} baseline MAE
                {' '}{forecastRun.result.evaluation.baseline.mae.toFixed(2)}
                {' '}· comparison {forecastRun.result.comparison?.outcome ?? 'unavailable'}
              </p>
              <p className="muted">
                Holdout RMSE {forecastRun.result.evaluation.candidate.rmse.toFixed(2)} vs
                {' '}baseline {forecastRun.result.evaluation.baseline.rmse.toFixed(2)} ·
                {' '}WAPE {!Number.isFinite(forecastRun.result.evaluation.candidate.wape)
                  ? `unavailable${forecastRun.result.evaluation.candidate.wape_reason
                    ? `: ${forecastRun.result.evaluation.candidate.wape_reason}`
                    : ''}`
                  : `${forecastRun.result.evaluation.candidate.wape.toFixed(2)}%`}
              </p>
              <ChartCard chart={forecastRun.result.chart} />
              <h3>Forecasted monthly totals</h3>
              <ol>
                {forecastRun.result.forecast.map((point) => (
                  <li key={point.period}>
                    {point.period}: {point.predicted.toFixed(2)}
                    {point.lower_bound !== null && point.lower_bound !== undefined
                      && point.upper_bound !== null && point.upper_bound !== undefined
                      ? ` · empirical error envelope ${point.lower_bound.toFixed(2)}–${point.upper_bound.toFixed(2)}`
                      : ' · uncertainty bounds unavailable'}
                  </li>
                ))}
              </ol>
              <p className="muted">
                {forecastRun.result.uncertainty.status === 'available'
                  ? 'Uncertainty is a rolling-origin empirical absolute-error envelope, not a calibrated prediction or confidence interval.'
                  : `Uncertainty unavailable: ${forecastRun.result.uncertainty.reason}`}
              </p>
              {(forecastRun.result.suitability.findings ?? []).map((finding, index) => (
                <p
                  className={finding.severity === 'warning' || finding.severity === 'blocked'
                    ? 'dashboard-warning'
                    : 'muted'}
                  key={`${finding.code}-${index}`}
                >
                  {finding.severity}: {finding.message}
                </p>
              ))}
              {forecastRun.result.warnings.map((warning, index) => (
                <p className="dashboard-warning" key={`${warning}-${index}`}>{warning}</p>
              ))}
              {forecastRun.result.limitations.map((limitation, index) => (
                <p className="muted" key={`${limitation}-${index}`}>{limitation}</p>
              ))}
              <div className="data-analysis-controls">
                <label>
                  Save this run to dashboard
                  <select
                    value={forecastDashboardId}
                    onChange={(event) => setForecastDashboardId(event.target.value)}
                    disabled={Boolean(busy)}
                  >
                    {forecastDashboards.map((dashboard) => (
                      <option key={dashboard.id} value={dashboard.id}>
                        {dashboard.title}
                      </option>
                    ))}
                    <option value={CREATE_FORECAST_DASHBOARD}>
                      Create a new dashboard…
                    </option>
                  </select>
                </label>
                {forecastDashboardId === CREATE_FORECAST_DASHBOARD && (
                  <label>
                    New dashboard name
                    <input
                      value={newForecastDashboardTitle}
                      onChange={(event) => setNewForecastDashboardTitle(event.target.value)}
                      maxLength={120}
                      required
                      disabled={Boolean(busy)}
                    />
                  </label>
                )}
                <button
                  className="button button-secondary"
                  type="button"
                  onClick={handleSaveForecastToDashboard}
                  disabled={Boolean(busy)}
                >
                  {busy === 'forecast-dashboard-save' ? 'Saving…' : 'Save forecast to dashboard'}
                </button>
              </div>
            </section>
          )}

          {anomalyRun?.result?.anomaly_summary && (
            <section className="panel data-panel" aria-live="polite">
              <p className="eyebrow">Persisted anomaly screening result</p>
              <h2>{statusLabel(anomalyRun.status)} · {anomalyRun.result.anomaly_summary.anomaly_count ?? 'Not evaluated'} flagged observations</h2>
              <p className="muted">
                Dataset {anomalyRun.dataset_id} / file {anomalyRun.file_id} ·
                {' '}{anomalyRun.result.analyzed_columns?.join(', ') ?? 'selected numeric column'} ·
                {' '}method {anomalyRun.result.anomaly_summary.method} ·
                {' '}threshold {anomalyRun.result.anomaly_summary.threshold}
              </p>
              {anomalyRun.result.anomaly_summary.warnings?.map((warning) => (
                <p className="dashboard-warning" key={warning}>{warning}</p>
              ))}
              {anomalyRun.result.anomaly_summary.limitations?.map((limitation) => (
                <p className="muted" key={limitation}>{limitation}</p>
              ))}
              <pre className="data-result-json">
                {JSON.stringify(anomalyRun.result.anomaly_summary.evidence_rows, null, 2)}
              </pre>
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
              {analysis.result.analysis_type === 'anomaly_detection' ? (
                <p className="muted">
                  Anomaly screening uses the robust method shown below; it does not prove that a value is erroneous.
                </p>
              ) : (
                <>
                  <button
                    className="button button-secondary"
                    type="button"
                    onClick={handleEdaReport}
                    disabled={Boolean(busy)}
                  >
                    {busy === 'eda-report' ? 'Building report…' : 'Build evidence report'}
                  </button>
                  <pre className="data-result-json">{JSON.stringify(analysis.result.columns, null, 2)}</pre>
                </>
              )}
              {edaReport && (
                <section className="eda-evidence-report" aria-live="polite">
                  <p className="eyebrow">Stored EDA evidence</p>
                  <h3>{edaReport.summary.findings_count} measured findings</h3>
                  <p className="muted">
                    {edaReport.summary.rows_analyzed} rows analyzed · provenance {edaReport.provenance.operation_id.slice(0, 12)}
                  </p>
                  <p className="muted">
                    Source EDA analysis {analysis.id} · report workflow {edaReportReference.workflow_id} ·
                    {' '}task {edaReportReference.task_id}
                  </p>
                  {edaReport.warnings.map((warning, index) => (
                    <p className="dashboard-warning" key={`${warning}-${index}`}>{warning}</p>
                  ))}
                  <div className="dashboard-chart-grid">
                    {edaReport.charts.map((chart) => (
                      <div
                        className="dashboard-chart-placement"
                        key={chart.title}
                        style={{ gridColumn: 'span 12', gridRow: 'span 6' }}
                      >
                        <ChartCard chart={chart} />
                      </div>
                    ))}
                  </div>
                  <ul className="dashboard-findings">
                    {edaReport.findings.map((finding) => (
                      <li key={finding.id}>
                        <strong>{finding.statement}</strong>
                        <span>{finding.supporting_metric.valid_count} valid observations · {finding.source_references[0].dataset_id}/{finding.source_references[0].file_id}</span>
                      </li>
                    ))}
                  </ul>
                  <div className="data-dashboard-save">
                    <label className="data-field">
                      Save this report to dashboard
                      <select
                        value={edaDashboardId}
                        onChange={(event) => setEdaDashboardId(event.target.value)}
                        disabled={Boolean(busy)}
                      >
                        {forecastDashboards.map((dashboard) => (
                          <option key={dashboard.id} value={dashboard.id}>
                            {dashboard.title}
                          </option>
                        ))}
                        <option value={CREATE_FORECAST_DASHBOARD}>Create a new dashboard…</option>
                      </select>
                    </label>
                    {edaDashboardId === CREATE_FORECAST_DASHBOARD && (
                      <label className="data-field">
                        New dashboard name
                        <input
                          value={newEdaDashboardTitle}
                          onChange={(event) => setNewEdaDashboardTitle(event.target.value)}
                          maxLength={120}
                          required
                          disabled={Boolean(busy)}
                        />
                      </label>
                    )}
                    <button
                      className="button button-secondary"
                      type="button"
                      onClick={handleSaveEdaReportToDashboard}
                      disabled={Boolean(busy)}
                    >
                      {busy === 'eda-dashboard-save' ? 'Saving…' : 'Save EDA report to dashboard'}
                    </button>
                  </div>
                </section>
              )}

            </section>
          )}
        </>
      )}
    </div>
  )
}

export default DataWorkspace
