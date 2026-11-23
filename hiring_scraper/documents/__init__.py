"""Pure, database-free structured application documents package."""
from hiring_scraper.documents.evidence import build_evidence, skill_slug
from hiring_scraper.documents.grounding import check_document
from hiring_scraper.documents.import_docx import DocxImportError, import_docx
from hiring_scraper.documents.import_tex import import_marked_tex
from hiring_scraper.documents.project_tex import facts_from_document, project_tex
from hiring_scraper.documents.schema import (
    Contact,
    CvDocument,
    Entry,
    Finding,
    Header,
    LetterDocument,
    LockedEntry,
    Section,
    Sender,
    Unit,
    apply_patch,
    unit_by_id,
    walk_units,
)
from hiring_scraper.documents.text import cv_text, letter_text

__all__ = [
    'Contact',
    'DocxImportError',
    'CvDocument',
    'Entry',
    'Finding',
    'Header',
    'LetterDocument',
    'LockedEntry',
    'Section',
    'Sender',
    'Unit',
    'apply_patch',
    'build_evidence',
    'check_document',
    'cv_text',
    'facts_from_document',
    'import_docx',
    'import_marked_tex',
    'letter_text',
    'project_tex',
    'skill_slug',
    'unit_by_id',
    'walk_units',
]
