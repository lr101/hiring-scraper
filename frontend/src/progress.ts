export function elapsedLabel(value: string | null | undefined, now = Date.now()): string | null {
  if (!value) return null
  const timestamp = new Date(/(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`).getTime()
  if (Number.isNaN(timestamp)) return null
  const seconds = Math.max(0, Math.floor((now - timestamp) / 1000))
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

export function hasWaitedTooLong(value: string | null | undefined, now = Date.now()): boolean {
  if (!value) return false
  const timestamp = new Date(/(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`).getTime()
  return !Number.isNaN(timestamp) && now - timestamp >= 60_000
}
