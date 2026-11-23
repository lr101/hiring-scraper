import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ApplicationHttpError, applicationRequest, incomingDocument, jsonRequest,
} from './applicationDocuments'
import type { ApplicationDocument, Finding } from './documents/types'
import { findUnitById } from './documents/editing'
import type { SavedDocument } from './applicationDocuments'

type HeldDraft = { id: string; findings: Finding[]; stale_inputs?: boolean }
type Draft = {
  saved: SavedDocument
  body: ApplicationDocument
  dismissed: string[]
  held?: HeldDraft
}

function fromSaved(saved: SavedDocument): Draft {
  return { saved, body: saved.body, dismissed: [] }
}

const hasEdits = (draft: Draft) => draft.body !== draft.saved.body || draft.dismissed.length > 0

function visibleFindings(draft: Draft) {
  return (draft.held?.findings ?? draft.saved.findings).filter(finding => {
    if (draft.dismissed.includes(finding.id)) return false
    if (!finding.unit_id || draft.held) return true
    const prior = findUnitById(draft.saved.body, finding.unit_id)
    const edited = findUnitById(draft.body, finding.unit_id)
    return edited && prior?.text === edited.text && prior?.lead_in === edited.lead_in
  })
}

export function useApplicationDraft(url: string) {
  const [draft, setDraft] = useState<Draft | null>(null)
  const state = useRef<Draft | null>(null)
  const [saving, setSaving] = useState(false)
  const inFlight = useRef(false)
  const deferred = useRef<SavedDocument | null>(null)
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState(false)
  const [remote, setRemote] = useState<SavedDocument | null>(null)
  const blocked = useRef(false)

  const replace = useCallback((next: Draft | null) => {
    state.current = next
    setDraft(next)
  }, [])

  const receive = useCallback((incoming: SavedDocument | null) => {
    if (!incoming) {
      // A deleted CV can remove all saved versions. Preserve unsaved text.
      if (state.current && !hasEdits(state.current) && !inFlight.current) replace(null)
      return
    }
    if (inFlight.current) {
      if (!deferred.current || deferred.current.version <= incoming.version) deferred.current = incoming
      return
    }
    const current = state.current
    switch (current ? incomingDocument(current.saved.version, incoming.version, hasEdits(current)) : 'replace') {
      case 'replace':
        replace(fromSaved(incoming))
        setError('')
        break
      case 'metadata':
        if (current) replace({ ...current, saved: { ...incoming, body: current.saved.body } })
        break
      case 'conflict':
        blocked.current = true
        setConflict(true)
        setRemote(previous => !previous || incoming.version >= previous.version ? incoming : previous)
        break
    }
  }, [replace])

  const save = useCallback(async (overwrite?: SavedDocument) => {
    const snapshot = state.current
    if (!snapshot || inFlight.current || (blocked.current && !overwrite)) return
    if (!overwrite && !hasEdits(snapshot)) return snapshot.saved.version
    inFlight.current = true
    setSaving(true)
    setError('')
    if (overwrite) {
      blocked.current = false
      setConflict(false)
      setRemote(null)
    }
    const previous = overwrite ?? snapshot.saved
    try {
      const result = await applicationRequest<{ version: number }>(url, jsonRequest('PUT', {
        expected_version: previous.version,
        body: snapshot.body,
        dismissed_findings: snapshot.dismissed,
        ...(snapshot.held ? { held_task_id: snapshot.held.id } : {}),
      }, AbortSignal.timeout(20000)))
      const current = state.current!
      const saved: SavedDocument = {
        ...previous, version: result.version, body: snapshot.body,
        findings: visibleFindings({ ...snapshot, saved: previous }),
        language: snapshot.body.language, origin: 'user', created_at: new Date().toISOString(),
        stale_inputs: previous.stale_inputs || Boolean(snapshot.held?.stale_inputs),
      }
      // A user can keep typing during the PUT. Acknowledge only the sent snapshot.
      replace({
        ...current, saved,
        dismissed: current.dismissed.filter(id => !snapshot.dismissed.includes(id)),
        held: current.held === snapshot.held ? undefined : current.held,
      })
      return result.version
    } catch (reason) {
      if (reason instanceof ApplicationHttpError && reason.status === 409) {
        blocked.current = true
        setConflict(true)
      } else {
        setError(reason instanceof Error ? reason.message : 'Could not save this document.')
      }
    } finally {
      inFlight.current = false
      setSaving(false)
      const incoming = deferred.current
      deferred.current = null
      if (incoming) receive(incoming)
    }
  }, [receive, replace, url])

  const dirty = draft ? hasEdits(draft) : false
  const body = draft?.body
  const dismissed = draft?.dismissed
  useEffect(() => {
    if (!dirty || saving || conflict || error) return
    const timer = setTimeout(() => void save(), 700)
    return () => clearTimeout(timer)
  }, [body, dismissed, conflict, dirty, error, save, saving])

  function edit(body: ApplicationDocument) {
    const current = state.current
    if (current) replace({ ...current, body, held: undefined })
    setError('')
  }

  function dismiss(finding: Finding) {
    const current = state.current
    if (current) replace({ ...current, dismissed: [...current.dismissed, finding.id] })
    setError('')
  }

  function adopt(body: ApplicationDocument, held: HeldDraft) {
    const current = state.current
    if (current) replace({ ...current, body, held, dismissed: [] })
    setError('')
  }

  function reload(saved: SavedDocument) {
    blocked.current = false
    setConflict(false)
    setRemote(null)
    setError('')
    replace(fromSaved(saved))
  }

  return {
    draft, body, dirty, saving, error, conflict, remote, receive, edit, dismiss, adopt, reload, save,
    findings: draft ? visibleFindings(draft) : [],
  }
}
