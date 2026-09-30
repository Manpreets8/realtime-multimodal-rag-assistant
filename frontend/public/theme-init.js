// Applies the saved theme before the app renders, so the page never flashes the wrong theme.
// A separate file rather than an inline script: the Content-Security-Policy allows only
// same-origin script files. Keep in step with src/services/theme.ts.
;(function () {
  var choice = null
  try {
    choice = localStorage.getItem('mindora-theme')
  } catch {
    choice = null
  }
  var dark =
    choice === 'dark' ||
    (choice !== 'light' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
})()
