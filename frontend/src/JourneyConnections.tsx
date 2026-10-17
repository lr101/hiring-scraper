import { useEffect, useId, useState } from 'react'
import type { RefObject } from 'react'
import type { ApplicationStatus } from './applications'

const connections: [ApplicationStatus, ApplicationStatus][] = [
  ['new', 'open'], ['new', 'in_gots_hand'], ['open', 'in_gots_hand'],
  ['open', 'waiting_for_reply'], ['open', 'automated_application'],
  ['in_gots_hand', 'waiting_for_reply'], ['in_gots_hand', 'automated_application'],
  ['waiting_for_reply', 'interview'], ['automated_application', 'interview'],
  ['interview', 'accepted'],
]

type Layout = { width: number; height: number; paths: { from: string; to: string; d: string }[] }

/** Draw behind the real stage buttons, including the mobile scroll content. */
export default function JourneyConnections({ containerRef }: { containerRef: RefObject<HTMLDivElement | null> }) {
  const marker = useId()
  const [layout, setLayout] = useState<Layout>({ width: 0, height: 0, paths: [] })
  // The containing DOM ref is attached after child layout effects run.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    let frame = 0
    const measure = () => {
      const bounds = container.getBoundingClientRect()
      const boxes = new Map([...container.querySelectorAll<HTMLButtonElement>('[data-journey-stage]')].map(button => {
        const box = button.getBoundingClientRect()
        return [button.dataset.journeyStage!, {
          left: box.left - bounds.left + container.scrollLeft,
          top: box.top - bounds.top + container.scrollTop,
          right: box.right - bounds.left + container.scrollLeft,
          bottom: box.bottom - bounds.top + container.scrollTop,
        }]
      }))
      const padding = getComputedStyle(container)
      // Derive the extent from buttons, never scrollWidth: the old SVG width
      // must not hold the scroll area open after resizing back to desktop.
      const width = Math.max(container.clientWidth, ...[...boxes.values()].map(box => box.right + parseFloat(padding.paddingRight)))
      const height = Math.max(container.clientHeight, ...[...boxes.values()].map(box => box.bottom + parseFloat(padding.paddingBottom)))
      const paths = connections.flatMap(([from, to]) => {
        const start = boxes.get(from), end = boxes.get(to)
        if (!start || !end) return []
        if (Math.abs(start.left - end.left) < 1) {
          const x = (start.left + start.right) / 2
          return [{ from, to, d: `M ${x} ${start.bottom} V ${end.top}` }]
        }
        const x1 = start.right, y1 = (start.top + start.bottom) / 2
        const x2 = end.left, y2 = (end.top + end.bottom) / 2
        const mid = (x1 + x2) / 2, direction = Math.sign(y2 - y1)
        const radius = Math.min(7, Math.abs(y2 - y1) / 2, (x2 - x1) / 4)
        return [{ from, to, d: direction === 0 ? `M ${x1} ${y1} H ${x2}`
          : `M ${x1} ${y1} H ${mid - radius} Q ${mid} ${y1} ${mid} ${y1 + direction * radius} V ${y2 - direction * radius} Q ${mid} ${y2} ${mid + radius} ${y2} H ${x2}` }]
      })
      setLayout({ width, height, paths })
    }
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(measure)
    })
    observer.observe(container)
    container.querySelectorAll('[data-journey-stage]').forEach(button => observer.observe(button))
    measure()
    return () => { observer.disconnect(); cancelAnimationFrame(frame) }
  }, [containerRef])

  return <svg className="journey-connections" width={layout.width} height={layout.height} aria-hidden="true" focusable="false">
    <defs><marker id={marker} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto">
      <path d="M 1 1 L 7 4 L 1 7" />
    </marker></defs>
    {layout.paths.map(path => <path key={`${path.from}-${path.to}`} data-from={path.from} data-to={path.to} d={path.d} markerEnd={`url(#${marker})`} />)}
  </svg>
}
