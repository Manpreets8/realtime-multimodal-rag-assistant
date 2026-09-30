/**
 * Light, dark or follow the system. The choice is a per-browser preference (localStorage);
 * public/theme-init.js applies it before the app renders so the page never flashes the
 * wrong theme. The resolved theme is set as <html data-theme="light|dark">, which the
 * Tailwind `dark:` variant keys on (see index.css).
 */

export type ThemeChoice = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'mindora-theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

export function getThemeChoice(): ThemeChoice {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  } catch {
    return 'system' // storage blocked (private mode, site data disabled)
  }
}

function systemPrefersDark(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia(DARK_QUERY).matches
}

function apply(choice: ThemeChoice) {
  const dark = choice === 'dark' || (choice === 'system' && systemPrefersDark())
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

export function setThemeChoice(choice: ThemeChoice) {
  try {
    if (choice === 'system') localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, choice)
  } catch {
    // Not saved, but still applied for this page.
  }
  apply(choice)
}

/** Keep "system" in step with the operating system while the app is open. */
export function followSystemTheme(): () => void {
  apply(getThemeChoice())
  if (typeof window.matchMedia !== 'function') return () => {}
  const query = window.matchMedia(DARK_QUERY)
  const onChange = () => {
    if (getThemeChoice() === 'system') apply('system')
  }
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}
