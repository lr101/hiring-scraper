import { Fragment, useCallback, useLayoutEffect, useRef } from 'react'
import type { ReactNode, Ref } from 'react'
import type { CvDocument, Entry, Section, Unit } from './types.ts'
import { syncInlineSeparators } from './inlineSeparators.ts'
import {
  formatEntryDates,
  renderPrintText,
  renderUnitContent,
  safeContactHref,
} from './viewShared.tsx'
import type { RenderUnit } from './viewShared.tsx'
import './classic-serif-v1.css'

export type { RenderUnit, UnitDisplayKind, UnitRenderContext } from './viewShared.tsx'

export interface CvViewProps {
  document: CvDocument
  photoUrl?: string | null
  renderUnit?: RenderUnit
  showHidden?: boolean
  className?: string
  pageRef?: Ref<HTMLDivElement>
  contentRef?: Ref<HTMLDivElement>
  overlay?: ReactNode
}

function visibleUnits(units: Unit[], showHidden: boolean): Unit[] {
  return showHidden ? units : units.filter((unit) => !unit.hidden)
}

const genericEntryLabel = /^(?:university|college|hochschule|universität|internship|praktikum|education|ausbildung)$/i
const roleEntryLabel = /^(?:internship|praktikum)$/i

const publicationSectionTitle = /(?:publication|publikation|research|forschung|paper|aufsatz)/i

function localizedRoleLabel(raw: string, language: CvDocument['language']): string {
  const trimmed = raw.trim()
  if (roleEntryLabel.test(trimmed)) {
    return language === 'de' ? 'Praktikum' : 'Internship'
  }
  return ''
}

function isShortOrListParagraph(unit: Unit): boolean {
  const text = `${unit.lead_in ? `${unit.lead_in} ` : ''}${unit.text}`.trim()
  if (!text) return false
  if (text.length < 180) return true
  if (text.endsWith(':')) return true
  if (unit.lead_in.trim()) return true
  return /^[^.!?]{2,40}:\s+/.test(text)
}

function italicizeQuotedTitles(text: string): ReactNode {
  const parts = text.split(/("[^"]+"|“[^”]+”|„[^“”]+[“”]|«[^»]+»|»[^«]+«)/u)
  return parts.map((part, index) => (
    index % 2
      ? <em key={index}>{renderPrintText(part)}</em>
      : <Fragment key={index}>{renderPrintText(part)}</Fragment>
  ))
}

function renderPossiblyItalicizedUnit(unit: Unit): ReactNode {
  const content = italicizeQuotedTitles(unit.text)
  if (!unit.lead_in) {
    return content
  }
  if (!unit.text) {
    return <strong className="doc-unit-lead-in">{renderPrintText(unit.lead_in)}</strong>
  }
  return (
    <>
      <strong className="doc-unit-lead-in">{renderPrintText(unit.lead_in)}</strong> {content}
    </>
  )
}

function renderSectionUnits(
  section: Section,
  showHidden: boolean,
  renderUnit?: RenderUnit,
): ReactNode {
  const items = visibleUnits(section.units, showHidden)
  if (items.length === 0) return null
  const italicizeTitles = !renderUnit && (
    section.kind === 'text' || publicationSectionTitle.test(section.title)
  )

  if (section.layout === 'bullets') {
    return (
      <ul className="doc-cv-bullets">
        {items.map((unit, index) => (
          <li
            key={unit.id}
            className="doc-cv-bullet"
            data-unit-id={unit.id}
            data-unit-hidden={unit.hidden ? 'true' : undefined}
          >
            {italicizeTitles
              ? renderPossiblyItalicizedUnit(unit)
              : renderUnitContent(renderUnit, {
                unit,
                kind: 'bullet',
                splittable: true,
                sectionId: section.id,
                index,
              })}
          </li>
        ))}
      </ul>
    )
  }

  if (section.layout === 'inline') {
    return (
      <p className="doc-cv-inline">
        {items.map((unit, index) => (
          <Fragment key={unit.id}>
            {index > 0 ? (
              <span className="doc-cv-inline-sep" aria-hidden="true">
                <span className="doc-cv-inline-sep-space">{' '}</span>
                <span className="doc-cv-inline-sep-mark">{'\u2022 '}</span>
              </span>
            ) : null}
            <span
              className="doc-cv-inline-entry"
              data-unit-hidden={unit.hidden ? 'true' : undefined}
            >
              <span
                className="doc-cv-inline-item"
                data-unit-id={unit.id}
                data-unit-hidden={unit.hidden ? 'true' : undefined}
              >
                {renderUnitContent(renderUnit, {
                  unit,
                  kind: 'inline',
                  splittable: true,
                  sectionId: section.id,
                  index,
                })}
              </span>
            </span>
          </Fragment>
        ))}
      </p>
    )
  }

  if (section.layout === 'columns') {
    const midpoint = Math.ceil(items.length / 2)
    const leftItems = items.slice(0, midpoint)
    const rightItems = items.slice(midpoint)
    return (
      <div className="doc-cv-columns">
        <ul className="doc-cv-column-list">
          {leftItems.map((unit, index) => (
            <li
              key={unit.id}
              className="doc-cv-column-item"
              data-unit-id={unit.id}
              data-unit-hidden={unit.hidden ? 'true' : undefined}
            >
              {renderUnitContent(renderUnit, {
                unit,
                kind: 'column',
                splittable: true,
                sectionId: section.id,
                index,
              })}
            </li>
          ))}
        </ul>
        {rightItems.length > 0 ? (
          <ul className="doc-cv-column-list">
            {rightItems.map((unit, index) => (
              <li
                key={unit.id}
                className="doc-cv-column-item"
                data-unit-id={unit.id}
                data-unit-hidden={unit.hidden ? 'true' : undefined}
              >
                {renderUnitContent(renderUnit, {
                  unit,
                  kind: 'column',
                  splittable: true,
                  sectionId: section.id,
                  index: midpoint + index,
                })}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    )
  }

  return (
    <div className="doc-cv-paragraphs">
      {items.map((unit, index) => (
        <p
          key={unit.id}
          className={
            isShortOrListParagraph(unit)
              ? 'doc-cv-paragraph doc-cv-paragraph--left'
              : 'doc-cv-paragraph'
          }
          data-unit-id={unit.id}
          data-unit-hidden={unit.hidden ? 'true' : undefined}
        >
          {italicizeTitles
            ? renderPossiblyItalicizedUnit(unit)
            : renderUnitContent(renderUnit, {
              unit,
              kind: 'paragraph',
              splittable: true,
              sectionId: section.id,
              index,
            })}
        </p>
      ))}
    </div>
  )
}

function renderEntry(
  entry: Entry,
  sectionId: string,
  language: CvDocument['language'],
  showHidden: boolean,
  renderUnit?: RenderUnit,
): ReactNode {
  const introUnits = visibleUnits(entry.intro, showHidden)
  const bulletUnits = visibleUnits(entry.units, showHidden)
  const dates = formatEntryDates(entry.locked, language)
  const { company, title, location } = entry.locked
  const trimmedCompany = company.trim()
  const trimmedTitle = title.trim()
  const label = entry.label.trim()
  const genericCompany = genericEntryLabel.test(trimmedCompany)
  const companyLine = genericCompany ? title : company
  const duplicateTitle = trimmedCompany.toLowerCase() === trimmedTitle.toLowerCase()
  const showTitle = !genericCompany && (!genericEntryLabel.test(trimmedTitle) || roleEntryLabel.test(trimmedTitle)) && !duplicateTitle
  const fallbackRoleTitle = (genericCompany || duplicateTitle)
    ? localizedRoleLabel(
      roleEntryLabel.test(label)
        ? label
        : roleEntryLabel.test(trimmedCompany)
          ? trimmedCompany
          : '',
      language,
    )
    : ''
  const titleText = showTitle ? localizedRoleLabel(trimmedTitle, language) || title : fallbackRoleTitle

  const hasProjectList = entry.kind === 'study' || bulletUnits.some((unit) => Boolean(unit.lead_in.trim()))
  const entryClass = hasProjectList ? 'doc-cv-entry doc-cv-entry--projects' : 'doc-cv-entry'

  return (
    <article key={entry.id} className={entryClass} data-entry-id={entry.id}>
      {label && !genericEntryLabel.test(label) ? <p className="doc-cv-entry-label">{label}</p> : null}
      <div className="doc-cv-entry-header">
        <div className="doc-cv-entry-row">
          <span className="doc-cv-entry-company">{renderPrintText(companyLine)}</span>
          {location ? (
            <span className="doc-cv-entry-location">{location}</span>
          ) : null}
        </div>
        {(titleText || dates) ? (
          <div className="doc-cv-entry-row">
            <span className="doc-cv-entry-title">{renderPrintText(titleText)}</span>
            {dates ? <span className="doc-cv-entry-dates">{dates}</span> : null}
          </div>
        ) : null}
      </div>
      {introUnits.length > 0 ? (
        <div className="doc-cv-entry-intro">
          {introUnits.map((unit, index) => (
            <p
              key={unit.id}
              className="doc-cv-paragraph doc-cv-paragraph--left"
              data-unit-id={unit.id}
              data-unit-hidden={unit.hidden ? 'true' : undefined}
            >
              {renderUnitContent(renderUnit, {
                unit,
                kind: 'entry-intro',
                splittable: true,
                sectionId,
                entryId: entry.id,
                index,
              })}
            </p>
          ))}
        </div>
      ) : null}
      {bulletUnits.length > 0 ? (
        <ul className="doc-cv-bullets">
          {bulletUnits.map((unit, index) => (
            <li
              key={unit.id}
              className="doc-cv-bullet"
              data-unit-id={unit.id}
              data-unit-hidden={unit.hidden ? 'true' : undefined}
            >
              {renderUnitContent(renderUnit, {
                unit,
                kind: 'bullet',
                splittable: true,
                sectionId,
                entryId: entry.id,
                index,
              })}
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  )
}

function isVisibleSection(section: Section, showHidden: boolean): boolean {
  return showHidden || section.entries.length > 0 || visibleUnits(section.units, false).length > 0
}

function renderSection(
  section: Section,
  language: CvDocument['language'],
  showHidden: boolean,
  renderUnit?: RenderUnit,
  previousGroup = '',
): ReactNode {
  const group = section.group?.trim() ?? ''
  const Title = group ? 'h3' : 'h2'
  const titleClass = group ? 'doc-cv-subsection-title' : 'doc-cv-section-title'

  return (
    <section
      key={section.id}
      className={`doc-cv-section doc-cv-section--${section.kind}`}
      data-section-id={section.id}
      data-section-kind={section.kind}
    >
      {group && group !== previousGroup ? <h2 className="doc-cv-section-title">{group}</h2> : null}
      {section.title.trim() ? <Title className={titleClass}>{section.title}</Title> : null}
      {renderSectionUnits(section, showHidden, renderUnit)}
      {section.entries.map((entry) =>
        renderEntry(entry, section.id, language, showHidden, renderUnit),
      )}
    </section>
  )
}

export function CvView({
  document: doc,
  photoUrl,
  renderUnit,
  showHidden = false,
  className,
  pageRef,
  contentRef,
  overlay,
}: CvViewProps) {
  const localContentRef = useRef<HTMLDivElement | null>(null)
  const setContentNode = useCallback(
    (node: HTMLDivElement | null) => {
      localContentRef.current = node
      if (typeof contentRef === 'function') {
        return contentRef(node)
      }
      if (contentRef) {
        contentRef.current = node
      }
    },
    [contentRef],
  )

  useLayoutEffect(() => {
    const el = localContentRef.current
    if (!el) return
    const sync = () => syncInlineSeparators(el)
    sync()
    let cancelled = false
    if (typeof document !== 'undefined' && document.fonts?.ready) {
      void document.fonts.ready.then(() => {
        if (!cancelled) sync()
      })
    }
    if (typeof ResizeObserver === 'undefined') {
      return () => {
        cancelled = true
      }
    }
    const observer = new ResizeObserver(sync)
    observer.observe(el)
    return () => {
      cancelled = true
      observer.disconnect()
    }
  }, [doc, showHidden, className])

  const showPhoto = Boolean(photoUrl?.trim())
  const showHeadline = showHidden || !doc.headline.hidden
  const profile = doc.sections[0]?.kind === 'profile' ? doc.sections[0] : null
  const leadingProfile = profile && isVisibleSection(profile, showHidden) ? profile : null
  const remainingSections = (profile ? doc.sections.slice(1) : doc.sections)
    .filter((section) => isVisibleSection(section, showHidden))

  const pageClasses = [
    'doc-page',
    `doc-page--${doc.page}`,
    `doc-template-${doc.template}`,
    'doc-kind-cv',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      ref={pageRef}
      className={pageClasses}
      lang={doc.language}
      data-doc-format={doc.format}
      data-page-format={doc.page}
    >
      {overlay}
      <div ref={setContentNode} className="doc-page-content">
        <div
          className={
            showPhoto
              ? 'doc-cv-header-wrap doc-cv-header-wrap--with-photo'
              : 'doc-cv-header-wrap'
          }
        >
          {showPhoto ? (
            <img
              className="doc-cv-photo"
              src={photoUrl ?? undefined}
              alt={doc.header.name}
            />
          ) : null}
          <div className="doc-cv-header-main">
            <header className="doc-cv-header">
              <h1 className="doc-cv-name">{doc.header.name}</h1>
              {showHeadline ? (
                <div
                  className="doc-cv-headline"
                  data-unit-id={doc.headline.id}
                  data-unit-hidden={doc.headline.hidden ? 'true' : undefined}
                >
                  {renderUnitContent(renderUnit, {
                    unit: doc.headline,
                    kind: 'headline',
                    splittable: false,
                  })}
                </div>
              ) : null}
              {doc.header.contacts.length > 0 ? (
                <p className="doc-cv-contacts">
                  {doc.header.contacts.map((contact, index) => {
                    const href = safeContactHref(contact)
                    return (
                      <Fragment key={`${contact.kind}-${index}-${contact.text}`}>
                        {index > 0 ? (
                          <span className="doc-cv-contact-sep" aria-hidden="true">
                            {' \u00B7 '}
                          </span>
                        ) : null}
                        {href ? (
                          <a
                            className="doc-cv-contact-link"
                            href={href}
                            rel="noopener noreferrer"
                          >
                            {contact.text}
                          </a>
                        ) : (
                          <span className="doc-cv-contact-text">{contact.text}</span>
                        )}
                      </Fragment>
                    )
                  })}
                </p>
              ) : null}
            </header>
            {leadingProfile
              ? renderSection(leadingProfile, doc.language, showHidden, renderUnit)
              : null}
          </div>
        </div>

        {remainingSections.map((section, index) =>
          renderSection(section, doc.language, showHidden, renderUnit,
            (index ? remainingSections[index - 1] : leadingProfile)?.group?.trim()),
        )}
      </div>
    </div>
  )
}
