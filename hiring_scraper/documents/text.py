"""Plain-text projections for structured CVs and cover letters."""
from __future__ import annotations

from hiring_scraper.documents.schema import CvDocument, Entry, LetterDocument, Section, Unit


def unit_full_text(unit: Unit) -> str:
    """Return plain Unicode text for a unit including its optional lead-in."""
    lead = unit.lead_in.strip()
    body = unit.text.strip()
    if lead and body:
        return f'{lead} {body}'
    return lead or body


def _date_range(entry: Entry, language: str) -> str:
    locked = entry.locked
    if locked.current:
        present = 'heute' if language == 'de' else 'present'
        return f'{locked.start}–{present}'
    if not locked.end or locked.end == locked.start:
        return locked.start
    return f'{locked.start}–{locked.end}'


def _entry_lines(entry: Entry, language: str) -> list[str]:
    lines: list[str] = []
    if entry.label.strip():
        lines.append(entry.label.strip())
    company = entry.locked.company.strip()
    location = entry.locked.location.strip()
    lines.append(f'{company} — {location}' if location else company)
    dates = _date_range(entry, language)
    lines.append(f'{entry.locked.title.strip()} ({dates})')
    for unit in entry.intro:
        if unit.hidden:
            continue
        rendered = unit_full_text(unit)
        if rendered:
            lines.append(rendered)
    for unit in entry.units:
        if unit.hidden:
            continue
        rendered = unit_full_text(unit)
        if rendered:
            lines.append(f'- {rendered}')
    return lines


def _section_block(section: Section, language: str) -> str:
    parts: list[str] = []
    visible_units = [text for u in section.units if not u.hidden and (text := unit_full_text(u))]
    if visible_units:
        if section.layout == 'inline':
            parts.append(' • '.join(visible_units))
        elif section.layout == 'bullets':
            parts.append('\n'.join(f'- {item}' for item in visible_units))
        else:
            parts.append('\n'.join(visible_units))
    if section.entries:
        entry_blocks = ['\n'.join(_entry_lines(entry, language)) for entry in section.entries]
        parts.append('\n\n'.join(entry_blocks))
    if not parts:
        return ''
    body = '\n\n'.join(parts)
    title = section.title.strip()
    return f'{title}\n{body}' if title else body


def cv_text(doc: CvDocument) -> str:
    """Project a ``CvDocument`` to readable plain text in reading order, skipping hidden units."""
    blocks: list[str] = []
    header_lines: list[str] = [doc.header.name.strip()]
    if not doc.headline.hidden:
        headline_text = unit_full_text(doc.headline)
        if headline_text:
            header_lines.append(headline_text)
    contacts = [contact.text.strip() for contact in doc.header.contacts if contact.text.strip()]
    if contacts:
        header_lines.append(' | '.join(contacts))
    blocks.append('\n'.join(header_lines))

    for section in doc.sections:
        block = _section_block(section, doc.language)
        if block:
            blocks.append(block)
    return '\n\n'.join(blocks).strip()


def letter_text(doc: LetterDocument) -> str:
    """Project a ``LetterDocument`` to readable plain text in reading order, skipping hidden units."""
    blocks: list[str] = []
    sender_lines = [doc.sender.name.strip(), *(line.strip() for line in doc.sender.lines if line.strip())]
    if sender_lines:
        blocks.append('\n'.join(sender_lines))
    recipient = [line.strip() for line in doc.recipient_lines if line.strip()]
    if recipient:
        blocks.append('\n'.join(recipient))
    if doc.place_date.strip():
        blocks.append(doc.place_date.strip())
    if not doc.subject.hidden:
        subject = unit_full_text(doc.subject)
        if subject:
            blocks.append(subject)
    if not doc.salutation.hidden:
        salutation = unit_full_text(doc.salutation)
        if salutation:
            blocks.append(salutation)
    for paragraph in doc.paragraphs:
        if paragraph.hidden:
            continue
        text = unit_full_text(paragraph)
        if text:
            blocks.append(text)
    closing_lines: list[str] = []
    if not doc.closing.hidden:
        closing = unit_full_text(doc.closing)
        if closing:
            closing_lines.append(closing)
    if doc.signature.strip():
        closing_lines.append(doc.signature.strip())
    if closing_lines:
        blocks.append('\n'.join(closing_lines))
    return '\n\n'.join(blocks).strip()
