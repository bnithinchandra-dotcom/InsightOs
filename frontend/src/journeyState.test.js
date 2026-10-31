import { describe, expect, it } from 'vitest'
import { deriveJourney } from './journeyState'

const base = { hasProject: true, files: [], mlSuitability: {}, analysis: null, forecastRun: null }
const ready = { id: 'f1', status: 'Ready', normalization_status: 'Ready' }

describe('deriveJourney', () => {
  it('starts at Upload with no files', () => {
    const journey = deriveJourney(base)
    expect(journey.steps[journey.currentIndex].id).toBe('upload')
    expect(journey.complete).toBe(false)
  })

  it('points to project creation when no project exists', () => {
    const journey = deriveJourney({ ...base, hasProject: false })
    expect(journey.steps[0].action.href).toBe('#projects')
  })

  it('moves to Normalize after a ready upload', () => {
    const journey = deriveJourney({ ...base, files: [{ id: 'f1', status: 'Ready', normalization_status: 'Pending' }] })
    expect(journey.steps[journey.currentIndex].id).toBe('normalize')
  })

  it('flags failed files', () => {
    const journey = deriveJourney({ ...base, files: [{ id: 'f1', status: 'Ready', normalization_status: 'Failed' }] })
    expect(journey.hasFailure).toBe(true)
  })

  it('advances through Profile and EDA from real results', () => {
    let journey = deriveJourney({ ...base, files: [ready] })
    expect(journey.steps[journey.currentIndex].id).toBe('profile')
    journey = deriveJourney({ ...base, files: [ready], mlSuitability: { f1: {} } })
    expect(journey.steps[journey.currentIndex].id).toBe('eda')
    journey = deriveJourney({ ...base, files: [ready], mlSuitability: { f1: {} }, analysis: { result: {} } })
    expect(journey.steps[journey.currentIndex].id).toBe('forecast')
  })

  it('does not reuse profile progress from a previously selected dataset', () => {
    const journey = deriveJourney({
      ...base,
      files: [{ ...ready, id: 'current-file' }],
      mlSuitability: { 'previous-file': {} },
    })
    expect(journey.steps[journey.currentIndex].id).toBe('profile')
  })

  it('is complete once a forecast result exists', () => {
    const journey = deriveJourney({
      ...base, files: [ready], mlSuitability: { f1: {} }, analysis: { result: {} }, forecastRun: { result: {} },
    })
    expect(journey.complete).toBe(true)
  })
})
