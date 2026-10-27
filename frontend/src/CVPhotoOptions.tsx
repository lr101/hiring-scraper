import { useEffect, useRef } from 'react'
import { readCVPhoto } from './applicationDocumentExport'
import type { Profile } from './profile'

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export default function CVPhotoOptions({ profile, photoDataURL, onPhotoChange, useProfilePhoto, onUseProfilePhotoChange, busy, reading, onReadingChange, onError, onDownloadBundle }: {
  profile: Pick<Profile, 'id' | 'photo'>; photoDataURL: string; onPhotoChange: (value: string) => void
  useProfilePhoto: boolean; onUseProfilePhotoChange: (value: boolean) => void; busy: boolean
  reading: boolean; onReadingChange: (value: boolean) => void
  onError: (message: string) => void; onDownloadBundle: () => void
}) {
  const version = useRef(0)
  useEffect(() => () => { version.current++; onReadingChange(false) }, [onReadingChange])
  const savedPhoto = profile.photo && useProfilePhoto
  const photoURL = photoDataURL || (savedPhoto ? `${API}/api/v1/profiles/${profile.id}/photo?v=${encodeURIComponent(profile.photo!.updated_at)}` : '')

  return <details className="finish-options"><summary>CV photo (optional)</summary>
    {profile.photo && <label className="cv-photo-toggle"><input type="checkbox" checked={useProfilePhoto} disabled={busy || reading || Boolean(photoDataURL)} onChange={event => onUseProfilePhotoChange(event.target.checked)} />Use saved profile photo</label>}
    <p><a href={`/profile/${profile.id}/edit#profile-documents`}>{profile.photo ? 'Manage saved profile photo' : 'Save a photo to your profile'}</a></p>
    <label>{profile.photo ? 'Different photo for this application' : 'Photo for CV rendering'}<input type="file" accept="image/png,.png" disabled={busy || reading} onChange={event => {
      const file = event.target.files?.[0], current = ++version.current
      event.target.value = ''
      if (!file) return
      onReadingChange(true); onError('')
      void readCVPhoto(file).then(value => { if (current === version.current) onPhotoChange(value) })
        .catch(reason => { if (current === version.current) onError(reason.message) })
        .finally(() => { if (current === version.current) onReadingChange(false) })
    }} /></label>
    <p className="finish-help">PNG up to 2 MiB and 4096 pixels per side. Your saved photo is used automatically for CV PDFs and source + photo ZIPs in either language. A different photo stays only in this tab. Photos are not sent to ChatGPT. Custom templates use their existing portrait position in the ZIP for local compilation.</p>
    {reading && <p role="status">Reading photo…</p>}
    {photoURL && <><img src={photoURL} alt="Selected CV photo" width={80} style={{ maxHeight: 110, objectFit: 'contain' }} /><div className="finish-download-actions cv-photo-actions">
      <button type="button" disabled={busy || reading} onClick={onDownloadBundle}>Download source + photo ZIP</button>
      {photoDataURL && <button type="button" disabled={busy || reading} onClick={() => { version.current++; onPhotoChange('') }}>{profile.photo && useProfilePhoto ? 'Use saved photo instead' : 'Remove photo'}</button>}
    </div></>}
  </details>
}
