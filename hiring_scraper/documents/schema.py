"""Strict Pydantic v2 document contract for structured CVs, letters and findings.

Pure, database-free schema shared with the frontend document renderer and editor.
All text fields hold plain Unicode without markup or LaTeX.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

UNIT_ID_RE = re.compile(r'^[a-z0-9-]{1,64}$')
ENTRY_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{1,100}$')
FINDING_ID_RE = re.compile(r'^f-[a-z0-9-]{1,62}$')
DATE_RE = re.compile(r'^[1-9][0-9]{3}(?:-(?:0[1-9]|1[0-2]))?$')

MAX_UNIT_TEXT = 1200
MAX_SECTION_UNITS = 60
MAX_ENTRY_INTRO_UNITS = 20
MAX_LETTER_PARAGRAPHS = 20

UnitOrigin = Literal['source', 'ai', 'user']
UnitRole = Literal['', 'motivation', 'experience', 'education', 'bring', 'close']
ContactKind = Literal['phone', 'email', 'location', 'link']
SectionKind = Literal['profile', 'experience', 'education', 'skills', 'list', 'text']
SectionLayout = Literal['paragraphs', 'bullets', 'inline', 'columns']
EntryKind = Literal['work', 'study']
DocumentLanguage = Literal['de', 'en']
PageSize = Literal['a4', 'letter']
DocumentTemplate = Literal['classic-serif-v1']
FindingCode = Literal[
    'unsupported_number',
    'unsupported_term',
    'misattributed',
    'sensitive_fact',
    'placeholder',
    'language_mix',
    'repetition',
    'overflow',
    'extra_page',
    'review_grounding',
    'review_coverage',
    'review_style',
    'review_format',
]
FindingSeverity = Literal['high', 'medium', 'info']

SourceId = Annotated[str, Field(min_length=1, max_length=120)]
SingleLineStr = Annotated[str, Field(max_length=200)]


def _no_newlines(value: str, field_name: str) -> str:
    if '\x00' in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError(f'{field_name} contains an invalid Unicode character.')
    if '\n' in value or '\r' in value:
        raise ValueError(f'{field_name} must not contain newline characters.')
    return value


class Unit(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    id: str = Field(min_length=1, max_length=64, pattern=UNIT_ID_RE.pattern)
    text: str = Field(max_length=MAX_UNIT_TEXT)
    lead_in: str = Field(default='', max_length=200)
    origin: UnitOrigin = 'source'
    sources: list[SourceId] = Field(default_factory=list, max_length=32)
    role: UnitRole = ''
    hidden: bool = False

    @field_validator('text')
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _no_newlines(value, 'text')

    @field_validator('lead_in')
    @classmethod
    def _validate_lead_in(cls, value: str) -> str:
        return _no_newlines(value, 'lead_in')

    @field_validator('sources')
    @classmethod
    def _validate_sources(cls, value: list[str]) -> list[str]:
        for item in value:
            _no_newlines(item, 'sources')
        return value


class Contact(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    kind: ContactKind
    text: str = Field(min_length=1, max_length=200)
    href: str = Field(default='', max_length=500)

    @field_validator('text')
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _no_newlines(value, 'text')

    @field_validator('href')
    @classmethod
    def _validate_href(cls, value: str) -> str:
        return _no_newlines(value, 'href')


class Header(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    name: str = Field(min_length=1, max_length=200)
    contacts: list[Contact] = Field(default_factory=list, max_length=12)

    @field_validator('name')
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _no_newlines(value, 'name')


class LockedEntry(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    company: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=300)
    location: str = Field(default='', max_length=300)
    start: str = Field(min_length=4, max_length=7, pattern=DATE_RE.pattern)
    end: str | None = Field(default=None, max_length=7)
    current: bool = False

    @field_validator('company', 'title', 'location')
    @classmethod
    def _validate_single_line(cls, value: str) -> str:
        return _no_newlines(value, 'locked entry field')

    @model_validator(mode='after')
    def _validate_dates(self) -> LockedEntry:
        end = self.end
        if end is not None:
            if not DATE_RE.fullmatch(end):
                raise ValueError('Dates must be YYYY or YYYY-MM.')
        if self.current and end is not None:
            raise ValueError('A current entry cannot have an end date.')
        if not self.current and end is None:
            raise ValueError('A completed entry requires an end date.')
        if end is not None and (end[:4] < self.start[:4] or (len(end) == len(self.start) == 7 and end < self.start)):
            raise ValueError('Entry end date cannot precede start date.')
        return self


class Entry(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    id: str = Field(min_length=1, max_length=100, pattern=ENTRY_ID_RE.pattern)
    kind: EntryKind
    locked: LockedEntry
    label: str = Field(default='', max_length=200)
    intro: list[Unit] = Field(default_factory=list, max_length=MAX_ENTRY_INTRO_UNITS)
    units: list[Unit] = Field(default_factory=list, max_length=MAX_SECTION_UNITS)

    @field_validator('label')
    @classmethod
    def _validate_label(cls, value: str) -> str:
        return _no_newlines(value, 'label')


class Section(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    id: str = Field(min_length=1, max_length=64, pattern=UNIT_ID_RE.pattern)
    kind: SectionKind
    title: str = Field(max_length=200)
    group: str = Field(default='', max_length=200)
    layout: SectionLayout = 'bullets'
    units: list[Unit] = Field(default_factory=list, max_length=MAX_SECTION_UNITS)
    entries: list[Entry] = Field(default_factory=list, max_length=20)

    @field_validator('title', 'group')
    @classmethod
    def _validate_heading(cls, value: str, info: ValidationInfo) -> str:
        return _no_newlines(value, info.field_name)

    @model_validator(mode='after')
    def _validate_section(self) -> Section:
        if self.entries and self.kind not in ('experience', 'education'):
            raise ValueError(f"Section kind '{self.kind}' cannot contain entries.")
        total_units = len(self.units) + sum(len(entry.intro) + len(entry.units) for entry in self.entries)
        if total_units > MAX_SECTION_UNITS:
            raise ValueError(f'Section {self.id!r} exceeds maximum of {MAX_SECTION_UNITS} units.')
        return self


class CvDocument(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    format: Literal['radius-cv-v2'] = 'radius-cv-v2'
    language: DocumentLanguage
    template: DocumentTemplate = 'classic-serif-v1'
    page: PageSize = 'a4'
    photo: bool = True
    header: Header
    headline: Unit
    sections: list[Section] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def _validate_uniqueness(self) -> CvDocument:
        section_ids: set[str] = set()
        entry_ids: set[str] = set()
        unit_ids: set[str] = {self.headline.id}
        for section in self.sections:
            if section.id in section_ids:
                raise ValueError(f'Duplicate section id: {section.id!r}')
            section_ids.add(section.id)
            for unit in section.units:
                if unit.id in unit_ids:
                    raise ValueError(f'Duplicate unit id: {unit.id!r}')
                unit_ids.add(unit.id)
            for entry in section.entries:
                if entry.id in entry_ids:
                    raise ValueError(f'Duplicate entry id: {entry.id!r}')
                entry_ids.add(entry.id)
                for unit in (*entry.intro, *entry.units):
                    if unit.id in unit_ids:
                        raise ValueError(f'Duplicate unit id: {unit.id!r}')
                    unit_ids.add(unit.id)
        return self


class Sender(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    name: str = Field(min_length=1, max_length=200)
    lines: list[SingleLineStr] = Field(default_factory=list, max_length=10)

    @field_validator('name')
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _no_newlines(value, 'name')

    @field_validator('lines')
    @classmethod
    def _validate_lines(cls, value: list[str]) -> list[str]:
        for line in value:
            _no_newlines(line, 'lines')
        return value


class LetterDocument(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    format: Literal['radius-letter-v2'] = 'radius-letter-v2'
    language: DocumentLanguage
    template: DocumentTemplate = 'classic-serif-v1'
    page: PageSize = 'a4'
    sender: Sender
    recipient_lines: list[SingleLineStr] = Field(default_factory=list, max_length=10)
    place_date: str = Field(default='', max_length=200)
    subject: Unit
    salutation: Unit
    paragraphs: list[Unit] = Field(default_factory=list, max_length=MAX_LETTER_PARAGRAPHS)
    closing: Unit
    signature: str = Field(default='', max_length=200)

    @field_validator('place_date', 'signature')
    @classmethod
    def _validate_single_line(cls, value: str) -> str:
        return _no_newlines(value, 'letter field')

    @field_validator('recipient_lines')
    @classmethod
    def _validate_recipient_lines(cls, value: list[str]) -> list[str]:
        for line in value:
            _no_newlines(line, 'recipient_lines')
        return value

    @model_validator(mode='after')
    def _validate_uniqueness(self) -> LetterDocument:
        unit_ids: set[str] = set()
        for unit in walk_units(self):
            if unit.id in unit_ids:
                raise ValueError(f'Duplicate unit id: {unit.id!r}')
            unit_ids.add(unit.id)
        return self


class Finding(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    id: str = Field(min_length=3, max_length=64, pattern=FINDING_ID_RE.pattern)
    document: Literal['cv', 'letter']
    unit_id: str | None = Field(default=None, max_length=64)
    code: FindingCode
    severity: FindingSeverity
    message: str = Field(min_length=1, max_length=500)
    suggestion: str = Field(default='', max_length=500)

    @field_validator('unit_id')
    @classmethod
    def _validate_unit_id(cls, value: str | None) -> str | None:
        if value is not None and not UNIT_ID_RE.fullmatch(value):
            raise ValueError(f'Invalid unit_id: {value!r}')
        return value


DocumentT = TypeVar('DocumentT', CvDocument, LetterDocument)


def walk_units(doc: CvDocument | LetterDocument) -> Iterator[Unit]:
    """Yield every Unit in reading order."""
    if isinstance(doc, CvDocument):
        yield doc.headline
        for section in doc.sections:
            yield from section.units
            for entry in section.entries:
                yield from entry.intro
                yield from entry.units
        return
    yield doc.subject
    yield doc.salutation
    yield from doc.paragraphs
    yield doc.closing


def unit_by_id(doc: CvDocument | LetterDocument, unit_id: str) -> Unit | None:
    """Return the Unit with ``unit_id``, or ``None`` if absent."""
    for unit in walk_units(doc):
        if unit.id == unit_id:
            return unit
    return None


def _patch_unit(
    unit: Unit,
    text: str,
    origin: UnitOrigin,
    *,
    lead_in: str | None = None,
    sources: list[str] | None = None,
    hidden: bool | None = None,
) -> Unit:
    data = unit.model_dump()
    data['text'] = text
    data['origin'] = origin
    if lead_in is not None:
        data['lead_in'] = lead_in
    if sources is not None:
        data['sources'] = list(sources)
    if hidden is not None:
        data['hidden'] = hidden
    return Unit.model_validate(data)


def apply_patch(
    doc: DocumentT,
    unit_id: str,
    text: str,
    origin: UnitOrigin,
    *,
    lead_in: str | None = None,
    sources: list[str] | None = None,
    hidden: bool | None = None,
) -> DocumentT:
    """Return a new validated document with ``unit_id`` updated.

    Raises ``KeyError`` if ``unit_id`` is not found in ``doc``.
    """
    found = False

    def replace(unit: Unit) -> Unit:
        nonlocal found
        if unit.id != unit_id:
            return unit
        found = True
        return _patch_unit(unit, text, origin, lead_in=lead_in, sources=sources, hidden=hidden)

    if isinstance(doc, CvDocument):
        new_headline = replace(doc.headline)
        new_sections: list[Section] = []
        for section in doc.sections:
            new_section_units = [replace(u) for u in section.units]
            new_entries: list[Entry] = []
            for entry in section.entries:
                new_intro = [replace(u) for u in entry.intro]
                new_entry_units = [replace(u) for u in entry.units]
                new_entries.append(
                    Entry.model_validate({
                        **entry.model_dump(exclude={'intro', 'units'}),
                        'intro': [u.model_dump() for u in new_intro],
                        'units': [u.model_dump() for u in new_entry_units],
                    })
                )
            new_sections.append(
                Section.model_validate({
                    **section.model_dump(exclude={'units', 'entries'}),
                    'units': [u.model_dump() for u in new_section_units],
                    'entries': [e.model_dump() for e in new_entries],
                })
            )
        if not found:
            raise KeyError(f'Unknown unit id: {unit_id!r}')
        return CvDocument.model_validate({
            **doc.model_dump(exclude={'headline', 'sections'}),
            'headline': new_headline.model_dump(),
            'sections': [s.model_dump() for s in new_sections],
        })

    new_subject = replace(doc.subject)
    new_salutation = replace(doc.salutation)
    new_paragraphs = [replace(u) for u in doc.paragraphs]
    new_closing = replace(doc.closing)
    if not found:
        raise KeyError(f'Unknown unit id: {unit_id!r}')
    return LetterDocument.model_validate({
        **doc.model_dump(exclude={'subject', 'salutation', 'paragraphs', 'closing'}),
        'subject': new_subject.model_dump(),
        'salutation': new_salutation.model_dump(),
        'paragraphs': [u.model_dump() for u in new_paragraphs],
        'closing': new_closing.model_dump(),
    })
