type SiteHeaderProps = { active: 'directory' | 'locations' | 'profile' }

export default function SiteHeader({ active }: SiteHeaderProps) {
  return <header className="topbar">
    <a className="brand" href="/" aria-label="Radius home">
      <span className="brand-mark"><i /><i /><i /><i /></span>
      <span>radius<span className="brand-period">.</span></span>
      <span className="brand-divider" />
      <span className="brand-caption">LOCAL JOBS</span>
    </a>
    <nav className="primary-nav" aria-label="Main navigation">
      <a href="/" className={active === 'directory' ? 'active' : ''}>Companies &amp; jobs</a>
      <a href="/locations" className={active === 'locations' ? 'active' : ''}>Search areas</a>
      <a href="/profile" className={active === 'profile' ? 'active' : ''}>Your profile</a>
    </nav>
    <div className="topbar-right">
      <span className="snapshot-status"><span className="status-dot" /> Germany</span>
      <span className="avatar">DE</span>
    </div>
  </header>
}
