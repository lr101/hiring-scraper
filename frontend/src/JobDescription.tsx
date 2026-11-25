import { useId, useState } from 'react'

export type DescriptionBlock =
  | { type: 'heading'; text: string; section?: 'duties' | 'requirements' | 'optional' | 'benefits' | 'neutral' }
  | { type: 'paragraph'; text: string }
  | { type: 'list'; items: string[] }

// Long postings stay open through the role's duties and requirements; company
// story, benefits and legal text after them are one click away.
const COLLAPSED_CHARS = 2000
const COLLAPSE_THRESHOLD = 3400
const MIN_HIDDEN_CHARS = 600
const ROLE_SECTIONS = new Set(['duties', 'requirements', 'optional'])

function blockLength(block: DescriptionBlock) {
  return block.type === 'list' ? block.items.reduce((total, item) => total + item.length + 20, 0) : block.text.length + 40
}

/** Fallback for an API response without blocks: blank lines separate paragraphs. */
function textBlocks(text: string): DescriptionBlock[] {
  return text.split(/\n{2,}/).map(part => part.trim()).filter(Boolean).map(part => {
    const lines = part.split('\n')
    if (lines.every(line => /^\s*[-*•]\s+/.test(line))) {
      return { type: 'list', items: lines.map(line => line.replace(/^\s*[-*•]\s+/, '')) }
    }
    if (lines.length === 1 && part.startsWith('## ')) return { type: 'heading', text: part.slice(3) }
    return { type: 'paragraph', text: part }
  })
}

function collapsedView(all: DescriptionBlock[]): DescriptionBlock[] {
  // End after the last duties/requirements section: at the next heading of
  // any other kind once that section is over.
  let lastRoleHeading = -1
  all.forEach((block, index) => {
    if (block.type === 'heading' && block.section && ROLE_SECTIONS.has(block.section)) lastRoleHeading = index
  })
  if (lastRoleHeading >= 0) {
    const nextHeading = all.findIndex((block, index) => index > lastRoleHeading && block.type === 'heading'
      && !(block.section && ROLE_SECTIONS.has(block.section)))
    return nextHeading < 0 ? all : all.slice(0, nextHeading)
  }
  // No recognizable sections: show a first stretch, ending at a block boundary.
  let used = 0
  const collapsed: DescriptionBlock[] = []
  for (const block of all) {
    if (used >= COLLAPSED_CHARS && collapsed.length && block.type === 'heading') break
    if (used >= COLLAPSED_CHARS * 1.4) break
    collapsed.push(block)
    used += blockLength(block)
  }
  // Never end a collapsed view on a heading without its content.
  while (collapsed.length > 1 && collapsed[collapsed.length - 1].type === 'heading') collapsed.pop()
  return collapsed
}

export function JobDescription({ blocks, text }: { blocks?: DescriptionBlock[] | null; text: string }) {
  const [expanded, setExpanded] = useState(false)
  const bodyId = useId()
  const all = blocks?.length ? blocks : textBlocks(text)
  const total = all.reduce((sum, block) => sum + blockLength(block), 0)
  let collapsed = all
  if (total > COLLAPSE_THRESHOLD) {
    collapsed = collapsedView(all)
    const hiddenChars = all.slice(collapsed.length).reduce((sum, block) => sum + blockLength(block), 0)
    if (hiddenChars < MIN_HIDDEN_CHARS) collapsed = all
  }
  const collapsible = collapsed.length < all.length
  const visible = collapsible && !expanded ? collapsed : all
  return <section className="job-description" aria-labelledby={`${bodyId}-title`}>
    <h2 id={`${bodyId}-title`}>Role description</h2>
    <div id={bodyId} className={`job-description-body${collapsible && !expanded ? ' is-collapsed' : ''}`}>
      {visible.map((block, index) => {
        if (block.type === 'heading') return <h3 key={index}>{block.text}</h3>
        if (block.type === 'list') return <ul key={index}>{block.items.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul>
        return <p key={index}>{block.text}</p>
      })}
    </div>
    {collapsible && <button type="button" className="job-description-toggle" aria-controls={bodyId}
      aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
      {expanded ? 'Show less' : 'Show full description'}
    </button>}
  </section>
}
