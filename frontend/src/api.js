const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

async function get(path, signal) {
  const response = await fetch(`${apiBaseUrl}${path}`, { signal })
  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`)
  }

  return response.json()
}

export function getHealth(signal) {
  return get('/health', signal)
}

export function getSystemInfo(signal) {
  return get('/api/v1/system/info', signal)
}
