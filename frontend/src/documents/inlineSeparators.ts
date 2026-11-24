const LINE_TOLERANCE_PX = 2

export function syncInlineSeparators(root: ParentNode | null | undefined): void {
  if (!root || typeof root.querySelectorAll !== 'function') return
  const lists = root.querySelectorAll<HTMLElement>('.doc-cv-inline')
  for (const list of lists) {
    const separators = Array.from(list.querySelectorAll<HTMLElement>('.doc-cv-inline-sep'))
    for (const sep of separators) {
      sep.classList.remove('doc-cv-inline-sep--hidden', 'doc-cv-inline-sep--collapsed')
    }
    for (const sep of separators) {
      const prev = sep.previousElementSibling as HTMLElement | null
      const next = sep.nextElementSibling as HTMLElement | null
      if (!prev || !next) continue
      const prevRect = prev.getBoundingClientRect()
      const sepRect = sep.getBoundingClientRect()
      const nextRect = next.getBoundingClientRect()
      if (prevRect.height === 0 && nextRect.height === 0) continue
      const wrapped = nextRect.top >= prevRect.bottom - LINE_TOLERANCE_PX
      if (wrapped) {
        if (sepRect.top >= prevRect.bottom - LINE_TOLERANCE_PX) {
          sep.classList.add('doc-cv-inline-sep--collapsed')
        } else {
          sep.classList.add('doc-cv-inline-sep--hidden')
        }
      }
    }
  }
}
