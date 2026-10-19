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
    try {
      const payload = await response.json()
      const detail = payload?.detail
      if (typeof detail === 'string' && detail.trim()) {
        message = detail
      } else if (typeof detail?.message === 'string' && detail.message.trim()) {
        message = detail.message
      }
    } catch {
      // Keep the status-based message when the server does not return JSON.
    }
    throw new Error(message)
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

export function getProjectDatasets(projectId, signal) {
  return get(`/api/v1/projects/${projectId}/datasets`, signal)
    .then((value) => expectArray(value, 'project datasets'))
}

export function getDatasetFiles(datasetId, signal) {
  return get(`/api/v1/datasets/${datasetId}/files`, signal)
    .then((value) => expectArray(value, 'dataset files'))
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
