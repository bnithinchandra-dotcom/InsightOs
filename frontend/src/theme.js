export const THEME_STORAGE_KEY = 'insightos-theme'

export function getThemePreference(storage) {
  try {
    const preference = (storage ?? window.localStorage).getItem(THEME_STORAGE_KEY)
    return preference === 'light' || preference === 'dark' ? preference : 'system'
  } catch (error) {
    console.warn('Unable to read the saved theme preference.', error)
    return 'system'
  }
}

export function resolveTheme(preference, prefersDark = false) {
  return preference === 'system' ? (prefersDark ? 'dark' : 'light') : preference
}

export function saveThemePreference(preference, storage) {
  try {
    const target = storage ?? window.localStorage
    if (preference === 'system') {
      target.removeItem(THEME_STORAGE_KEY)
    } else {
      target.setItem(THEME_STORAGE_KEY, preference)
    }
  } catch (error) {
    console.warn('Unable to save the theme preference.', error)
  }
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme
  const themeColor = document.querySelector('meta[name="theme-color"]')
  if (themeColor) themeColor.content = theme === 'dark' ? '#080B14' : '#F5F7FC'
}
