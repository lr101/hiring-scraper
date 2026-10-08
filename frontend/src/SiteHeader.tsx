type SiteHeaderProps = { active: 'directory' | 'locations' | 'profile' | 'applications' }

function NavigationIcon({ page }: { page: SiteHeaderProps['active'] }) {
  const paths = {
    directory: <><rect x="3" y="7" width="18" height="14" rx="2" /><path d="M8 7V4h8v3M3 12h18M10 12v2h4v-2" /></>,
    locations: <><path d="M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z" /><circle cx="12" cy="10" r="2.5" /></>,
    profile: <><circle cx="12" cy="7" r="4" /><path d="M4 21v-2a8 8 0 0 1 16 0v2" /></>,
    applications: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h5" /></>,
  }
  return <svg className="navigation-icon" width="22" height="22" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[page]}</svg>
}

export default function SiteHeader({ active }: SiteHeaderProps) {
  return <header className="topbar">
    <a className="brand" href="/" aria-label="Radius home">
      <span className="brand-mark"><i /><i /><i /><i /></span>
      <span>radius<span className="brand-period">.</span></span>
      <span className="brand-divider" />
      <span className="brand-caption">LOCAL JOBS</span>
    </a>
    <nav className="primary-nav" aria-label="Main navigation">
      {([
        { page: 'directory', href: '/', label: 'Companies & jobs', mobile: 'Jobs' },
        { page: 'locations', href: '/locations', label: 'Search areas', mobile: 'Areas' },
        { page: 'profile', href: '/profile', label: 'Your profile', mobile: 'Profile' },
        { page: 'applications', href: '/applications', label: 'My applications', mobile: 'Applications' },
      ] as const).map(item => <a key={item.page} href={item.href} aria-label={item.label}
        aria-current={active === item.page ? 'page' : undefined} className={active === item.page ? 'active' : ''}>
        <NavigationIcon page={item.page} /><span className="desktop-nav-label">{item.label}</span><span className="mobile-nav-label">{item.mobile}</span>
      </a>)}
    </nav>
    <div className="topbar-right">
      <span className="snapshot-status"><span className="status-dot" /> Germany</span>
      <span className="avatar">DE</span>
    </div>
  </header>
}
