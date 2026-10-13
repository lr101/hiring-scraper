type SiteHeaderProps = { active: 'directory' | 'profile' | 'applications' | 'settings' }

function NavigationIcon({ page }: { page: SiteHeaderProps['active'] }) {
  const paths = {
    directory: <><rect x="3" y="7" width="18" height="14" rx="2" /><path d="M8 7V4h8v3M3 12h18M10 12v2h4v-2" /></>,
    settings: <><path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z" /><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.7a8 8 0 0 1-1.6.9l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.6-.9l-1.7.7-1.4-2.4 1.4-1.1a7 7 0 0 1 0-1.9l-1.4-1.1 1.4-2.4 1.7.7a8 8 0 0 1 1.6-.9l.3-1.8h2.8l.3 1.8a8 8 0 0 1 1.6.9l1.7-.7 1.4 2.4-1.4 1.1a7 7 0 0 1-.1 1.8Z" /></>,
    profile: <><circle cx="12" cy="7" r="4" /><path d="M4 21v-2a8 8 0 0 1 16 0v2" /></>,
    applications: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h5" /></>,
    feeds: <><path d="M5 19a2 2 0 1 0 0 .01M4 11a9 9 0 0 1 9 9M4 5a15 15 0 0 1 15 15" /><path d="M4 5h.01" /></>,
  }
  return <svg className="navigation-icon" width="22" height="22" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[page]}</svg>
}

export default function SiteHeader({ active }: SiteHeaderProps) {
  return <header className="topbar">
    <a className="brand" href="/" aria-label="Radius home">
      <span className="brand-mark"><i /><i /><i /><i /></span>
      <span>radius<span className="brand-period">.</span></span>
    </a>
    <nav className="primary-nav" aria-label="Main navigation">
      {([
        { page: 'applications', href: '/applications', label: 'My applications', mobile: 'Applied' },
        { page: 'directory', href: '/', label: 'Companies & jobs', mobile: 'Jobs' },
        { page: 'profile', href: '/profile', label: 'Your profile', mobile: 'Profile' },
        { page: 'settings', href: '/settings', label: 'Settings', mobile: 'Settings' },
      ] as const).map(item => <a key={item.page} href={item.href} aria-label={item.label}
        aria-current={active === item.page ? 'page' : undefined} className={active === item.page ? 'active' : ''}>
        <NavigationIcon page={item.page} /><span className="desktop-nav-label">{item.label}</span><span className="mobile-nav-label">{item.mobile}</span>
      </a>)}
    </nav>
  </header>
}
