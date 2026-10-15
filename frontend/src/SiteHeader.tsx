type SiteHeaderProps = { active: 'directory' | 'applications' | 'settings' }

function NavigationIcon({ page }: { page: SiteHeaderProps['active'] }) {
  const paths = {
    directory: <><circle cx="10.8" cy="10.8" r="6.3" /><path d="m15.5 15.5 4.8 4.8M8.4 10.8h4.8M10.8 8.4v4.8" /></>,
    settings: <><path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z" /><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.7a8 8 0 0 1-1.6.9l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.6-.9l-1.7.7-1.4-2.4 1.4-1.1a7 7 0 0 1 0-1.9l-1.4-1.1 1.4-2.4 1.7.7a8 8 0 0 1 1.6-.9l.3-1.8h2.8l.3 1.8a8 8 0 0 1 1.6.9l1.7-.7 1.4 2.4-1.4 1.1a7 7 0 0 1-.1 1.8Z" /></>,
    applications: <><path d="M4 20V5" /><path d="M5 6h13l-3 4 3 4H5" /><path d="M9 9h4" /></>,
  }
  return <svg className="navigation-icon" width="22" height="22" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[page]}</svg>
}

export default function SiteHeader({ active }: SiteHeaderProps) {
  return <header className="topbar">
    <a className="brand" href="/" aria-label="Radius application journey">
      <span className="brand-mark"><i /><i /><i /><i /></span>
      <span>radius<span className="brand-period">.</span></span>
    </a>
    <nav className="primary-nav" aria-label="Main navigation">
      {([
        { page: 'applications', href: '/', label: 'Application journey', mobile: 'Journey' },
        { page: 'settings', href: '/settings', label: 'Settings', mobile: 'Settings' },
      ] as const).map(item => <a key={item.page} href={item.href} aria-label={item.label}
        aria-current={active === item.page ? 'page' : undefined}
        className={active === item.page ? 'active' : ''}>
        <NavigationIcon page={item.page} /><span className="desktop-nav-label">{item.label}</span><span className="mobile-nav-label">{item.mobile}</span>
      </a>)}
    </nav>
  </header>
}
