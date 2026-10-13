import { useState } from 'react'
import SiteHeader from './SiteHeader'
import { getThemePreference, setThemePreference } from './theme'
import type { ThemePreference } from './theme'

const themeOptions: { value: ThemePreference; label: string; detail: string }[] = [
  { value: 'system', label: 'System', detail: 'Follow device settings' },
  { value: 'light', label: 'Light', detail: 'Always use light mode' },
  { value: 'dark', label: 'Dark', detail: 'Always use dark mode' },
]

const settingsLinks = [
  { href: '/locations', symbol: '⌖', label: 'Search areas', detail: 'Configure the places used to find local jobs.' },
  { href: '/feeds', symbol: '≋', label: 'Job feeds', detail: 'Manage employer websites and other job sources.' },
  { href: '/profile', symbol: '◎', label: 'Job profiles', detail: 'Edit the skills and preferences used for matching.' },
]

export default function SettingsPage() {
  const [theme, setTheme] = useState<ThemePreference>(getThemePreference)

  function chooseTheme(value: ThemePreference) {
    setThemePreference(value)
    setTheme(value)
  }

  return <div className="app-shell">
    <SiteHeader active="settings" />
    <main className="content settings-page">
      <section className="settings-page-heading">
        <p className="eyebrow">PREFERENCES</p>
        <h1>Settings</h1>
      </section>

      <section className="detail-panel settings-card" aria-labelledby="appearance-heading">
        <div className="settings-section-heading">
          <div><p className="eyebrow">APPEARANCE</p><h2 id="appearance-heading">Color theme</h2></div>
          <p>Choose how Radius looks on this device.</p>
        </div>
        <div className="theme-options" role="radiogroup" aria-label="Color theme">
          {themeOptions.map(option => <button type="button" role="radio" aria-checked={theme === option.value}
            className={`theme-option ${theme === option.value ? 'selected' : ''}`} key={option.value}
            onClick={() => chooseTheme(option.value)}>
            <span className={`theme-preview theme-preview-${option.value}`} aria-hidden="true"><i /><i /><i /></span>
            <span className="theme-option-copy"><strong>{option.label}</strong><small>{option.detail}</small></span>
            <span className="theme-option-check" aria-hidden="true">{theme === option.value ? '✓' : ''}</span>
          </button>)}
        </div>
      </section>

      <section className="detail-panel settings-card" aria-labelledby="search-settings-heading">
        <div className="settings-section-heading">
          <div><p className="eyebrow">JOB SEARCH</p><h2 id="search-settings-heading">Search setup</h2></div>
          <p>Manage the locations and sources used to discover jobs.</p>
        </div>
        <div className="settings-link-list">
          {settingsLinks.map(item => <a className="settings-link-card" href={item.href} key={item.href}>
            <span className="settings-link-symbol" aria-hidden="true">{item.symbol}</span>
            <span className="settings-link-copy"><strong>{item.label}</strong><small>{item.detail}</small></span>
            <span className="settings-link-arrow" aria-hidden="true">→</span>
          </a>)}
        </div>
      </section>
    </main>
  </div>
}
