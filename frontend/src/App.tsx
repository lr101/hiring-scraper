import DetailPage from './DetailPage'
import FeedSourcesPage from './FeedSourcesPage'
import LocationsPage from './LocationsPage'
import ProfilesPage from './ProfilesPage'
import ApplicationsPage from './ApplicationsPage'
import SettingsPage from './SettingsPage'
import MatchingStatusPage from './MatchingStatusPage'
import ApplicationPreparationPage from './ApplicationPreparationPage'
import DirectoryPage from './DirectoryPage'

function App() {
  const path = window.location.pathname
  if (path === '/profile' || path.startsWith('/profile/')) return <ProfilesPage />
  if (path === '/applications' || path === '/applications/') return <ApplicationsPage />
  if (path === '/discover' || path === '/discover/') return <DirectoryPage />
  if (path === '/settings/ai-matching' || path === '/settings/ai-matching/') return <MatchingStatusPage />
  if (path === '/settings' || path === '/settings/') return <SettingsPage />
  if (path === '/locations' || path === '/locations/') return <LocationsPage />
  if (path === '/feeds' || path === '/feeds/') return <FeedSourcesPage />
  const applicationPreparationMatch = path.match(/^\/jobs\/(\d+)\/apply\/?$/)
  if (applicationPreparationMatch) return <ApplicationPreparationPage jobId={Number(applicationPreparationMatch[1])} />
  const companyMatch = path.match(/^\/companies\/(\d+)\/?$/)
  if (companyMatch) return <DetailPage kind="company" id={Number(companyMatch[1])} />
  const jobMatch = path.match(/^\/jobs\/(\d+)\/?$/)
  if (jobMatch) return <DetailPage kind="job" id={Number(jobMatch[1])} />
  if (path === '/' || path === '') return <ApplicationsPage />
  return <DirectoryPage />
}

export default App
