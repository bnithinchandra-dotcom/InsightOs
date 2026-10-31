import { describe, expect, it } from 'vitest'
import { getThemePreference, resolveTheme, saveThemePreference, THEME_STORAGE_KEY } from './theme'

describe('theme preference', () => {
  it('follows the operating system until a manual choice is saved', () => {
    const storage = {
      getItem: () => null,
      removeItem: () => {},
      setItem: () => {},
    }
    expect(getThemePreference(storage)).toBe('system')
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
  })

  it('persists explicit themes and clears preference when returning to system', () => {
    const values = new Map()
    const storage = {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
      removeItem: (key) => values.delete(key),
    }
    saveThemePreference('light', storage)
    expect(getThemePreference(storage)).toBe('light')
    saveThemePreference('system', storage)
    expect(values.has(THEME_STORAGE_KEY)).toBe(false)
  })
})
