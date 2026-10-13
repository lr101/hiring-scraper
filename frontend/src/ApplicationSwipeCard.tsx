import { useRef, useState } from 'react'
import type { ElementType, MouseEvent, ReactNode, TouchEvent } from 'react'
import { applicationStatusLabel, applicationStatuses, applicationSwipeAction } from './applications'
import type { Application, ApplicationSwipeAction, SwipeDirection } from './applications'

type SwipePreview = { key: number; dx: number; action: ApplicationSwipeAction; committing?: boolean }
type TouchStart = { x: number; y: number; mode: 'card' | 'undo' }

export default function ApplicationSwipeCard({
  as = 'article', application, disabled = false, className = '', children, onAction,
}: {
  as?: ElementType
  application?: Application | null
  disabled?: boolean
  className?: string
  children: ReactNode
  onAction: (action: ApplicationSwipeAction) => void
}) {
  const [preview, setPreview] = useState<SwipePreview | null>(null)
  const touchStart = useRef<TouchStart | null>(null)
  const suppressUndoClick = useRef(false)
  const Wrapper = as

  function startSwipe(event: TouchEvent<HTMLElement>) {
    if (disabled) return
    const target = event.target as HTMLElement
    const touch = event.touches[0]
    const mode = target.closest('[data-swipe-undo]') && application?.previous_status
      ? 'undo' : 'card'
    if (mode === 'card' && target.closest('a, button, select, input, textarea, label')) {
      touchStart.current = null
      return
    }
    touchStart.current = { x: touch.clientX, y: touch.clientY, mode }
  }

  function moveSwipe(event: TouchEvent<HTMLElement>) {
    const start = touchStart.current
    if (!start || disabled) return
    const touch = event.touches[0]
    const dx = touch.clientX - start.x
    const dy = touch.clientY - start.y
    if (Math.abs(dx) < 8 || Math.abs(dx) < Math.abs(dy) * 1.1) return
    const direction: SwipeDirection = dx > 0 ? 'right' : 'left'
    const action = start.mode === 'undo' && application?.previous_status
      ? { kind: 'undo' as const, status: application.previous_status }
      : applicationSwipeAction(application, direction)
    setPreview(action ? { key: 0, dx: Math.max(-144, Math.min(144, dx)), action } : null)
  }

  function finishSwipe(event: TouchEvent<HTMLElement>) {
    const start = touchStart.current
    touchStart.current = null
    if (!start || disabled) return
    const touch = event.changedTouches[0]
    const dx = touch.clientX - start.x
    const dy = touch.clientY - start.y
    const direction: SwipeDirection = dx > 0 ? 'right' : 'left'
    const action = start.mode === 'undo' && application?.previous_status
      ? { kind: 'undo' as const, status: application.previous_status }
      : applicationSwipeAction(application, direction)
    if (Math.abs(dx) < 56 || Math.abs(dx) < Math.abs(dy) * 1.25 || !action) {
      setPreview(null)
      return
    }
    const key = Date.now()
    setPreview({ key, dx: direction === 'right' ? 116 : -116, action, committing: true })
    if (start.mode === 'undo') {
      suppressUndoClick.current = true
      window.setTimeout(() => { suppressUndoClick.current = false }, 450)
    }
    onAction(action)
    window.setTimeout(() => setPreview(current => current?.key === key ? null : current), 220)
  }

  function cancelSwipe() {
    touchStart.current = null
    setPreview(null)
  }

  function suppressSwipedUndoClick(event: MouseEvent<HTMLElement>) {
    if (!suppressUndoClick.current || !(event.target as HTMLElement).closest('[data-swipe-undo]')) return
    event.preventDefault()
    event.stopPropagation()
    suppressUndoClick.current = false
  }

  const actionLabel = preview ? applicationStatuses.find(item => item.value === preview.action.status)?.label : null
  const direction = preview && preview.dx > 0 ? 'right' : 'left'

  return <Wrapper className={`application-swipe-card ${className} ${preview ? `swiping-${direction}` : ''} ${disabled ? 'swipe-disabled' : ''}`}
    style={preview ? { transform: `translate3d(${preview.dx}px, 0, 0) rotate(${Math.max(-6, Math.min(6, preview.dx * 0.035))}deg)`, transition: preview.committing ? 'transform 180ms cubic-bezier(.2,.8,.2,1)' : 'none' } : undefined}
    onTouchStart={startSwipe} onTouchMove={moveSwipe} onTouchEnd={finishSwipe} onTouchCancel={cancelSwipe}
    onClickCapture={suppressSwipedUndoClick}>
    {children}
    {preview && actionLabel && <span className={`swipe-action-cue swipe-action-${direction} ${preview.action.kind === 'undo' ? 'swipe-action-undo' : ''}`} aria-hidden="true">
      {preview.action.kind === 'undo' ? '↶ Undo to' : direction === 'right' ? '→' : '←'} {actionLabel}
    </span>}
  </Wrapper>
}

export function ApplicationStateBadge({ application, fallbackStatus }: {
  application?: Application | null
  fallbackStatus?: Application['status']
}) {
  const status = application?.status ?? fallbackStatus ?? null
  return <span className={`application-state-badge application-${status ?? 'untracked'}`}>{applicationStatusLabel(status)}</span>
}
