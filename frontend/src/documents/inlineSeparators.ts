const LINE_TOLERANCE_PX = 2

export function syncInlineSeparators(root: ParentNode | null | undefined): void {
  if (!root || typeof root.querySelectorAll !== 'function') return
  const lists = root.querySelectorAll<HTMLElement>('.doc-cv-inline')
  for (const list of lists) {
    const separators = Array.from(list.querySelectorAll<HTMLElement>('.doc-cv-inline-sep'))
    for (const sep of separators) {
      sep.classList.remove('doc-cv-inline-sep--hidden', 'doc-cv-inline-sep--collapsed')
      sep.style?.removeProperty('width')
    }
    const listRect = typeof list.getBoundingClientRect === 'function' ? list.getBoundingClientRect() : null
    // The editor may scale the sheet to fit small screens; convert rects back to layout px.
    const scale = listRect && list.offsetWidth > 0 && listRect.width > 0 ? listRect.width / list.offsetWidth : 1
    const tolerance = LINE_TOLERANCE_PX * scale
    for (const sep of separators) {
      const prev = sep.previousElementSibling as HTMLElement | null
      const next = sep.nextElementSibling as HTMLElement | null
      if (!prev || !next) continue
      const prevRect = prev.getBoundingClientRect()
      const sepRect = sep.getBoundingClientRect()
      const nextRect = next.getBoundingClientRect()
      if (prevRect.height === 0 && nextRect.height === 0) continue
      const wrapped = nextRect.top >= prevRect.bottom - tolerance
      if (wrapped) {
        if (sepRect.top >= prevRect.bottom - tolerance) {
          sep.classList.add('doc-cv-inline-sep--collapsed')
          const nextAfter = next.getBoundingClientRect()
          if (nextAfter.top < prevRect.bottom - tolerance && listRect && sep.style) {
            const remaining = Math.max(0, Math.floor((listRect.right - prevRect.right) / scale))
            if (remaining > 0) {
              sep.style.width = `${remaining}px`
            }
          }
        } else {
          sep.classList.add('doc-cv-inline-sep--hidden')
        }
      }
    }
  }
}
