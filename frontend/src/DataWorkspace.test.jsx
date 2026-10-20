import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import DataWorkspace from './DataWorkspace'

const project = { id: 17, name: 'Sample project' }
const dataset = { id: 29, project_id: project.id, name: 'Sample sales' }

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => cleanup())

describe('data workspace upload flow', () => {
  it('creates a local profile and project through the existing APIs', async () => {
    const projects = []
    let profileCreated = false
    globalThis.fetch = vi.fn(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/workspace/profile' && options.method === 'POST') {
        profileCreated = true
        return jsonResponse({
          id: 1,
          display_name: 'Local analyst',
          email: 'analyst@example.invalid',
        }, 201)
      }
      if (url.pathname === '/api/v1/workspace/profile') {
        return jsonResponse({
          configured: profileCreated,
          profile: profileCreated
            ? { id: 1, display_name: 'Local analyst', email: 'analyst@example.invalid' }
            : null,
        })
      }
      if (url.pathname === '/api/v1/projects' && options.method === 'POST') {
        const created = { ...project, name: JSON.parse(options.body).name }
        projects.push(created)
        return jsonResponse(created, 201)
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse(projects)
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<DataWorkspace page="projects" />)
    fireEvent.change(await screen.findByLabelText('Display name'), {
      target: { value: 'Local analyst' },
    })
    fireEvent.change(screen.getByLabelText('Email identity'), {
      target: { value: 'analyst@example.invalid' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create local profile' }))

    await screen.findByText('Local analyst')
    fireEvent.change(screen.getByLabelText('Project name'), {
      target: { value: 'Sample project' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create project' }))

    expect(await screen.findByText('Sample project')).toBeTruthy()
    const createCall = globalThis.fetch.mock.calls.find(([url, options]) => (
      String(url).includes('/api/v1/projects') && options?.method === 'POST'
    ))
    expect(JSON.parse(createCall[1].body)).toEqual({ name: 'Sample project' })
  })

  it('creates a dataset, multipart uploads a file, normalizes it, and displays EDA results', async () => {
    let datasetCreated = false
    let uploaded = false
    let normalized = false
    globalThis.fetch = vi.fn(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/workspace/profile') {
        return jsonResponse({
          configured: true,
          profile: { id: 1, display_name: 'Local analyst', email: 'analyst@example.invalid' },
        })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([project])
      if (url.pathname === `/api/v1/projects/${project.id}/datasets` && options.method === 'POST') {
        datasetCreated = true
        return jsonResponse(dataset, 201)
      }
      if (url.pathname === `/api/v1/projects/${project.id}/datasets`) {
        return jsonResponse(datasetCreated ? [dataset] : [])
      }
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files` && options.method === 'POST') {
        uploaded = true
        expect(options.body).toBeInstanceOf(FormData)
        expect(options.headers).toBeUndefined()
        return jsonResponse({ file: { id: 39, filename: 'sales.csv' } }, 201)
      }
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files`) {
        return jsonResponse(uploaded ? [{
          id: 39,
          dataset_id: dataset.id,
          filename: 'sales.csv',
          status: 'Ready',
          detected_format: 'csv',
          normalization_status: normalized ? 'Ready' : 'NotStarted',
          parsing_result: { row_count: 3 },
          profile_result: {
            tables: [{ columns: [{ name: 'revenue' }, { name: 'region' }] }],
          },
        }] : [])
      }
      if (url.pathname.endsWith('/files/39/normalize')) {
        normalized = true
        return jsonResponse({ status: 'Ready' })
      }
      if (url.pathname.endsWith('/files/39/eda')) {
        expect(JSON.parse(options.body)).toEqual({
          analysis_type: 'univariate',
          selected_columns: ['revenue'],
        })
        return jsonResponse({
          result: {
            rows_analyzed: 3,
            source: { artifact_sha256: 'a'.repeat(64) },
            columns: [{ name: 'revenue', statistics: { mean: 12.5 } }],
          },
          execution_placement: { target_id: 'backend' },
        })
      }
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<DataWorkspace page="datasets" />)
    await screen.findByRole('option', { name: /Sample project/ })
    fireEvent.change(screen.getByLabelText('New dataset name'), {
      target: { value: 'Sample sales' },
    })
    fireEvent.change(screen.getByLabelText(/File \(/), {
      target: { files: [new File(['order_id,revenue\\n1,10\\n2,15\\n3,12.5\\n'], 'sales.csv', { type: 'text/csv' })] },
    })
    expect(screen.getByLabelText(/File \(/).files[0].name).toBe('sales.csv')
    fireEvent.submit(screen.getByRole('button', { name: 'Create dataset and upload' }).closest('form'))

    expect(await screen.findByText(/Uploaded sales.csv and completed ingestion/)).toBeTruthy()
    fireEvent.click(await screen.findByRole('button', { name: 'Normalize' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Run EDA' }))

    expect(await screen.findByRole('heading', { name: 'EDA result' })).toBeTruthy()
    expect(screen.getByText(/3 rows analyzed/)).toBeTruthy()
    expect(screen.getByText(/"mean": 12.5/)).toBeTruthy()
    await waitFor(() => {
      expect(globalThis.fetch.mock.calls.some(([url]) => (
        String(url).endsWith('/files/39/eda')
      ))).toBe(true)
    })
  })

  it('shows an actionable upload error returned by the API', async () => {
    globalThis.fetch = vi.fn(async (input, options = {}) => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/workspace/profile') {
        return jsonResponse({
          configured: true,
          profile: { id: 1, display_name: 'Local analyst', email: 'analyst@example.invalid' },
        })
      }
      if (url.pathname === '/api/v1/projects') return jsonResponse([project])
      if (url.pathname === `/api/v1/projects/${project.id}/datasets` && options.method === 'POST') {
        return jsonResponse(dataset, 201)
      }
      if (url.pathname === `/api/v1/projects/${project.id}/datasets`) return jsonResponse([dataset])
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files` && options.method === 'POST') {
        return jsonResponse({
          detail: { code: 'unsupported_file_type', message: 'Unsupported file extension.' },
        }, 415)
      }
      if (url.pathname === `/api/v1/datasets/${dataset.id}/files`) return jsonResponse([])
      return jsonResponse({ detail: `Unexpected request: ${url.pathname}` }, 500)
    })

    render(<DataWorkspace page="datasets" />)
    await screen.findByRole('option', { name: /Sample project/ })
    fireEvent.change(screen.getByLabelText('New dataset name'), {
      target: { value: 'Invalid upload' },
    })
    fireEvent.change(screen.getByLabelText(/File \(/), {
      target: { files: [new File(['x'], 'unsupported.exe', { type: 'application/octet-stream' })] },
    })
    expect(screen.getByLabelText(/File \(/).files[0].name).toBe('unsupported.exe')
    fireEvent.submit(screen.getByRole('button', { name: 'Create dataset and upload' }).closest('form'))

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByRole('alert').textContent).toContain('Unsupported file extension.')
    expect(screen.getByRole('alert').textContent).toContain('dataset is retained')
  })
})
