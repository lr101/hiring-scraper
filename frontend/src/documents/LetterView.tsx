import type { ReactNode, Ref } from 'react'
import type { LetterDocument } from './types.ts'
import { renderUnitContent } from './viewShared.tsx'
import type { RenderUnit } from './viewShared.tsx'
import './classic-serif-v1.css'

export type { RenderUnit, UnitDisplayKind, UnitRenderContext } from './viewShared.tsx'

export interface LetterViewProps {
  document: LetterDocument
  renderUnit?: RenderUnit
  showHidden?: boolean
  className?: string
  pageRef?: Ref<HTMLDivElement>
  contentRef?: Ref<HTMLDivElement>
  overlay?: ReactNode
}

export function LetterView({
  document: doc,
  renderUnit,
  showHidden = false,
  className,
  pageRef,
  contentRef,
  overlay,
}: LetterViewProps) {
  const paragraphs = showHidden ? doc.paragraphs : doc.paragraphs.filter((unit) => !unit.hidden)
  const showSubject = showHidden || !doc.subject.hidden
  const showSalutation = showHidden || !doc.salutation.hidden
  const showClosing = showHidden || !doc.closing.hidden

  const pageClasses = [
    'doc-page',
    `doc-page--${doc.page}`,
    `doc-template-${doc.template}`,
    'doc-kind-letter',
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
      <div ref={contentRef} className="doc-page-content">
        <div className="doc-letter-sender">
          <p className="doc-letter-sender-name">{doc.sender.name}</p>
          {doc.sender.lines.map((line, index) => (
            <p key={`sender-${index}`} className="doc-letter-sender-line">
              {line}
            </p>
          ))}
        </div>

        {doc.recipient_lines.length > 0 ? (
          <div className="doc-letter-recipient">
            {doc.recipient_lines.map((line, index) => (
              <p key={`recipient-${index}`} className="doc-letter-recipient-line">
                {line}
              </p>
            ))}
          </div>
        ) : null}

        {doc.place_date.trim() ? <p className="doc-letter-date">{doc.place_date}</p> : null}

        {showSubject ? (
          <p
            className="doc-letter-subject"
            data-unit-id={doc.subject.id}
            data-unit-hidden={doc.subject.hidden ? 'true' : undefined}
          >
            {renderUnitContent(renderUnit, {
              unit: doc.subject,
              kind: 'subject',
              splittable: false,
            })}
          </p>
        ) : null}

        {showSalutation ? (
          <p
            className="doc-letter-salutation"
            data-unit-id={doc.salutation.id}
            data-unit-hidden={doc.salutation.hidden ? 'true' : undefined}
          >
            {renderUnitContent(renderUnit, {
              unit: doc.salutation,
              kind: 'salutation',
              splittable: false,
            })}
          </p>
        ) : null}

        {paragraphs.length > 0 ? (
          <div className="doc-letter-paragraphs">
            {paragraphs.map((unit, index) => (
              <p
                key={unit.id}
                className="doc-letter-paragraph"
                data-unit-id={unit.id}
                data-unit-role={unit.role || undefined}
                data-unit-hidden={unit.hidden ? 'true' : undefined}
              >
                {renderUnitContent(renderUnit, {
                  unit,
                  kind: 'paragraph',
                  splittable: true,
                  index,
                })}
              </p>
            ))}
          </div>
        ) : null}

        <div className="doc-letter-signoff">
          {showClosing ? (
            <p
              className="doc-letter-closing"
              data-unit-id={doc.closing.id}
              data-unit-hidden={doc.closing.hidden ? 'true' : undefined}
            >
              {renderUnitContent(renderUnit, {
                unit: doc.closing,
                kind: 'closing',
                splittable: false,
              })}
            </p>
          ) : null}
          {doc.signature.trim() ? <p className="doc-letter-signature">{doc.signature}</p> : null}
        </div>
      </div>
    </div>
  )
}
