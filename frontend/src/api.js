const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

async function request(path, { signal, method = 'GET', body } = {}) {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method,
    signal,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    let message = `Request failed (${response.status}).`
    let code
    try {
      const payload = await response.json()
      const detail = payload?.detail
      if (typeof detail === 'string' && detail.trim()) {
        message = detail
      } else if (typeof detail?.message === 'string' && detail.message.trim()) {
        message = detail.message
      }
      if (typeof detail?.code === 'string') code = detail.code
    } catch {
      // Keep the status-based message when the server does not return JSON.
    }
    const error = new Error(message)
    error.code = code
    error.status = response.status
    throw error
  }

  return response.json()
}

function get(path, signal) {
  return request(path, { signal })
}

function expectArray(value, description) {
  if (!Array.isArray(value)) throw new Error(`Unexpected ${description} response.`)
  return value
}

function expectObject(value, description) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`Unexpected ${description} response.`)
  }
  return value
}

export function getHealth(signal) {
  return get('/health', signal)
}

export function getSystemInfo(signal) {
  return get('/api/v1/system/info', signal)
}

export function getProjects(signal) {
  return get('/api/v1/projects', signal).then((value) => expectArray(value, 'projects'))
}

export function getLocalWorkspaceProfile(signal) {
  return get('/api/v1/workspace/profile', signal)
    .then((value) => expectObject(value, 'workspace profile'))
}

export function createLocalWorkspaceProfile(payload, signal) {
  return request('/api/v1/workspace/profile', {
    signal,
    method: 'POST',
    body: payload,
  }).then((value) => expectObject(value, 'workspace profile'))
}

export function createProject(payload, signal) {
  return request('/api/v1/projects', {
    signal,
    method: 'POST',
    body: payload,
  }).then((value) => expectObject(value, 'project'))
}

export function getProjectDatasets(projectId, signal) {
  return get(`/api/v1/projects/${projectId}/datasets`, signal)
    .then((value) => expectArray(value, 'project datasets'))
}

export function createDataset(projectId, payload, signal) {
  return request(`/api/v1/projects/${projectId}/datasets`, {
    signal,
    method: 'POST',
    body: payload,
  }).then((value) => expectObject(value, 'dataset'))
}

export function getDatasetFiles(datasetId, signal) {
  return get(`/api/v1/datasets/${datasetId}/files`, signal)
    .then((value) => expectArray(value, 'dataset files'))
}

export async function uploadDatasetFile(datasetId, file, signal) {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch(`${apiBaseUrl}/api/v1/datasets/${datasetId}/files`, {
    method: 'POST',
    signal,
    body: form,
  })
  return expectObject(await readResponse(response), 'uploaded file')
}

async function readResponse(response) {
  if (!response.ok) {
    let message = `Request failed (${response.status}).`
    let code
    try {
      const payload = await response.json()
      const detail = payload?.detail
      if (typeof detail === 'string' && detail.trim()) message = detail
      else if (typeof detail?.message === 'string' && detail.message.trim()) message = detail.message
      if (typeof detail?.code === 'string') code = detail.code
    } catch {
      // Preserve the status message for non-JSON server responses.
    }
    const error = new Error(message)
    error.code = code
    error.status = response.status
    throw error
  }
  return response.json()
}

export function normalizeDatasetFile(datasetId, fileId, signal) {
  return request(`/api/v1/datasets/${datasetId}/files/${fileId}/normalize`, {
    signal,
    method: 'POST',
    body: {},
  }).then((value) => expectObject(value, 'normalization'))
}

export function createDatasetEda(datasetId, fileId, payload, signal) {
  return request(`/api/v1/datasets/${datasetId}/files/${fileId}/eda`, {
    signal,
    method: 'POST',
    body: payload,
  }).then((value) => expectObject(value, 'EDA analysis'))
}

export function getMlTaskSuitability(datasetId, fileId, tableIndex = 0, signal) {
  return request('/api/v1/analytics/ml/suitability', {
    signal,
    method: 'POST',
    body: {
      dataset_id: Number(datasetId),
      file_id: Number(fileId),
      table_index: tableIndex,
    },
  }).then((value) => expectObject(value, 'ML task suitability'))
}

export function createSupervisedEvaluation(payload, signal) {
  return request('/api/v1/analytics/ml/supervised', {
    signal,
    method: 'POST',
    body: payload,
  }).then((value) => expectObject(value, 'supervised ML evaluation'))
}

export function getSupervisedEvaluation(workflowId, signal) {
  return get(`/api/v1/analytics/ml/supervised/${encodeURIComponent(workflowId)}`, signal)
    .then((value) => expectObject(value, 'supervised ML evaluation'))
}

export function getLatestSupervisedEvaluation(datasetId, fileId, signal) {
  const query = new URLSearchParams({
    dataset_id: String(datasetId),
    file_id: String(fileId),
  })
  return get(`/api/v1/analytics/ml/supervised?${query}`, signal)
    .then((value) => expectObject(value, 'supervised ML evaluation'))
}

export function getSchemaAnalyses(datasetId, { limit = 10, offset = 0, signal } = {}) {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return get(`/api/v1/datasets/${datasetId}/schema-analyses?${query}`, signal)
    .then((value) => {
      const result = expectObject(value, 'schema analyses')
      if (!Array.isArray(result.items) || !Number.isFinite(result.total)) {
        throw new Error('Unexpected schema analyses response.')
      }
      return result
    })
}

export function getSchemaAnalysis(datasetId, analysisId, signal) {
  return get(`/api/v1/datasets/${datasetId}/schema-analyses/${analysisId}`, signal)
    .then((value) => expectObject(value, 'schema analysis'))
}

export function createSchemaAnalysis(datasetId, payload, signal) {
  return request(`/api/v1/datasets/${datasetId}/schema-analyses`, {
    signal,
    method: 'POST',
    body: payload,
  }).then((value) => expectObject(value, 'schema analysis'))
}

export function getChatProviderStatus(signal) {
  return get('/api/v1/chat/provider-status', signal)
    .then((value) => expectObject(value, 'chat provider status'))
}

export function getAnalyticsWorkflowResult(workflowId, taskId, signal) {
  return get(
    `/api/v1/chat/workflows/${workflowId}/tasks/${encodeURIComponent(taskId)}/analytics-result`,
    signal,
  ).then((value) => expectObject(value, 'analytics workflow result'))
}

export function getAnalyticsCapabilities(signal) {
  return get('/api/v1/chat/analytics/capabilities', signal)
    .then((value) => expectObject(value, 'analytics capabilities'))
    .catch((error) => {
      if (error.status === 404 || error.message === 'Not Found') {
        const enhanced = new Error(
          'Analytics capabilities endpoint not found. Verify the backend is running and the route /api/v1/chat/analytics/capabilities is registered.',
        )
        enhanced.status = 404
        enhanced.code = 'endpoint_not_found'
        throw enhanced
      }
      if (error.status === 409 && error.code === 'workspace_profile_required') {
        const enhanced = new Error(
          'No workspace profile found. Create a local workspace profile before using analytics capabilities.',
        )
        enhanced.status = 409
        enhanced.code = 'workspace_profile_required'
        throw enhanced
      }
      throw error
    })
}

export function executeAnalyticsJourney(payload, signal) {
  return request('/api/v1/chat/analytics/executions', {
    signal,
    method: 'POST',
    body: payload,
  }).then((value) => expectObject(value, 'analytics workflow execution'))
}

export function executeAnalyticsPlan(payload, signal) {
  return request('/api/v1/chat/analytics/plans', {
    signal,
    method: 'POST',
    body: payload,
  }).then((value) => expectObject(value, 'analytics plan execution'))
}

export function getConversations({ limit = 25, offset = 0, signal } = {}) {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return get(`/api/v1/chat/conversations?${query}`, signal)
    .then((value) => expectArray(value, 'conversations'))
}

export function getConversation(conversationId, { limit = 100, offset = 0, signal } = {}) {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return get(`/api/v1/chat/conversations/${conversationId}?${query}`, signal)
    .then((value) => expectObject(value, 'conversation'))
}

export function createConversation(payload = {}, signal) {
  return request('/api/v1/chat/conversations', {
    signal,
    method: 'POST',
    body: payload,
  }).then((value) => expectObject(value, 'conversation'))
}

export function sendConversationMessage(conversationId, content, signal) {
  return request(`/api/v1/chat/conversations/${conversationId}/messages`, {
    signal,
    method: 'POST',
    body: { content },
  }).then((value) => expectObject(value, 'chat response'))
}

export function getDashboards(signal) {
  return get('/api/v1/dashboards', signal)
    .then((value) => expectArray(value, 'dashboards'))
}

export function getDashboard(dashboardId, signal) {
  return get(`/api/v1/dashboards/${dashboardId}`, signal)
    .then((value) => expectObject(value, 'dashboard'))
}

export function createDashboard(payload, signal) {
  return request('/api/v1/dashboards', {
    signal,
    method: 'POST',
    body: payload,
  }).then((value) => expectObject(value, 'dashboard'))
}

export function saveAnalyticsResultToDashboard(dashboardId, payload, signal) {
  return request(`/api/v1/dashboards/${dashboardId}/analytics-results`, {
    signal,
    method: 'POST',
    body: payload,
  }).then((value) => expectObject(value, 'saved analytics result'))
}

export function updateDashboard(dashboardId, payload, signal) {
  return request(`/api/v1/dashboards/${dashboardId}`, {
    signal,
    method: 'PUT',
    body: payload,
  }).then((value) => expectObject(value, 'dashboard'))
}

export function deleteDashboard(dashboardId, signal) {
  return request(`/api/v1/dashboards/${dashboardId}`, {
    signal,
    method: 'DELETE',
  }).then((value) => expectObject(value, 'dashboard deletion'))
}

export function saveForecastToDashboard(dashboardId, workflowId, signal) {
  return request(`/api/v1/dashboards/${dashboardId}/forecasts`, {
    signal,
    method: 'POST',
    body: { workflow_id: workflowId },
  }).then((value) => expectObject(value, 'saved forecast component'))
}

export function generateAnalyticsReport(payload, signal) {
  return request('/api/v1/analytics/reports', {
    signal,
    method: 'POST',
    body: payload,
  }).then((value) => expectObject(value, 'analytics report'))
}

export function generateEdaEvidenceReport(payload, signal) {
  return request('/api/v1/analytics/eda-reports', {
    signal,
    method: 'POST',
    body: payload,
  }).then((value) => expectObject(value, 'EDA evidence report'))
}

export function createMonthlyForecast(payload, signal) {
  return request('/api/v1/analytics/forecasts/monthly', {
    signal,
    method: 'POST',
    body: payload,
  }).then((value) => expectObject(value, 'monthly forecast'))
}

export function getMonthlyForecast(workflowId, signal) {
  return get(`/api/v1/analytics/forecasts/monthly/${workflowId}`, signal)
    .then((value) => expectObject(value, 'monthly forecast result'))
}
