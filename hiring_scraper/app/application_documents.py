"""Render a browser-returned application document without persisting it."""
from io import BytesIO
import re
from typing import Annotated, Literal
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from hiring_scraper.app import cv_templates
from hiring_scraper.app.database import get_session
from hiring_scraper.app.models import Job, ProfileCV
from hiring_scraper.app.profiles import TemplateChange, TYPES, cv_metadata, require_profile

router = APIRouter(prefix='/api/v1/profiles', tags=['application-documents'])
MAX_BYTES = 5 * 1024 * 1024
# Keep this language-neutral drafting check aligned with assertFinishedText in
# frontend/src/applicationHandoff.ts.
_UNFINISHED = re.compile(
    r'\[(?:CONFIRM\b|TODO\b|TBD\b|INSERT\b|YOUR\b)[^\]]*\]'
    r'|(?:^|\n)\s*(?:TODO|TBD)\s*[:—-]'
    r'|(?:please|can you) confirm (?:your|the following)'
    r'|awaiting your (?:confirmation|approval)', re.IGNORECASE,
)


class ApplicationDocumentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    job_id: int = Field(gt=0)
    cv_updated_at: str = Field(min_length=1, max_length=200)
    cv: Annotated[str, Field(max_length=100000)] | Annotated[list[TemplateChange], Field(max_length=1000)]
    cover_letter: str = Field(max_length=100000)

    @field_validator('cv_updated_at')
    @classmethod
    def nonempty_revision(cls, value):
        if not value.strip():
            raise ValueError('Supply the application package CV revision.')
        return value

    @field_validator('cv', mode='before')
    @classmethod
    def strict_changes(cls, value):
        # TemplateChange is shared with the older export API, which permits
        # coercion. Require strict IDs/text for this browser package boundary.
        if isinstance(value, list):
            if len(value) > 1000:
                raise ValueError('Use at most 1000 template changes.')
            return [TemplateChange.model_validate(row, strict=True) for row in value]
        return value

    @model_validator(mode='after')
    def bounded_text(self):
        cv_length = len(self.cv) if isinstance(self.cv, str) else sum(len(row.text) for row in self.cv)
        if cv_length > 100000 or cv_length + len(self.cover_letter) > 200000:
            raise ValueError('Keep CV edits under 100000 characters and package documents under 200000 characters.')
        return self


def _finished_text(text, *, allow_empty=False):
    if not allow_empty and not text.strip():
        raise ValueError('The selected document is empty. Return a finished application document before exporting.')
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', text):
        raise ValueError('The selected document contains unsupported control characters. Remove them before exporting.')
    if _UNFINISHED.search(text):
        raise ValueError('The selected document contains unfinished drafting text. Return a finished document without placeholders.')


def _text_docx(text):
    """A simple self-contained A4 document; supplied text is always XML text."""
    paragraphs = ''.join(
        '<w:p><w:r><w:rPr><w:rFonts w:ascii="Liberation Sans" w:hAnsi="Liberation Sans"/>'
        '<w:sz w:val="22"/></w:rPr><w:t xml:space="preserve">'
        + escape(line) + '</w:t></w:r></w:p>'
        for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    )
    parts = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        'word/document.xml': '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + paragraphs + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/>'
            '</w:sectPr></w:body></w:document>',
    }
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for name, xml in parts.items():
            archive.writestr(name, xml.encode('utf-8'))
    return output.getvalue()


@router.post('/{profile_id}/application-document')
def application_document(profile_id: int, request: ApplicationDocumentRequest,
                         kind: Literal['cv', 'cover_letter'] = 'cv',
                         format: Literal['pdf', 'docx'] = 'pdf',
                         session: Session = Depends(get_session)):
    require_profile(profile_id, session)
    if session.get(Job, request.job_id) is None:
        raise HTTPException(404, 'Job not found.')
    cv = session.get(ProfileCV, profile_id)
    if cv is not None and cv_metadata(cv)['updated_at'] != request.cv_updated_at:
        raise HTTPException(409, 'Your saved CV changed. Reload this application package before exporting.')
    changes = request.cv if kind == 'cv' and isinstance(request.cv, list) else None
    if isinstance(request.cv, list) and (cv is None or not cv.filename.lower().endswith('.docx')):
        raise HTTPException(422, 'Attach a DOCX to export paragraph edits with your original CV layout.')
    source = bytes(cv.data) if changes is not None else None
    session.rollback()  # Return the DB connection before editing/conversion.
    try:
        if changes is not None:
            # The editor flattens line breaks, which can hide line-start TODOs.
            for row in changes:
                _finished_text(row.text, allow_empty=True)
            output = cv_templates.edit_template(source, [row.model_dump() for row in changes])
            _finished_text('\n'.join(row['text'] for row in cv_templates.template_paragraphs(output)))
        else:
            text = request.cv if kind == 'cv' else request.cover_letter
            _finished_text(text)
            output = _text_docx(text)
        if len(output) > MAX_BYTES:
            raise HTTPException(413, 'This document exceeds 5 MiB. Use a smaller document or DOCX template.')
        if format == 'pdf':
            output = cv_templates.render_pdf(output)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from None
    if len(output) > MAX_BYTES:
        raise HTTPException(413, 'This document exceeds 5 MiB. Try downloading the DOCX instead.')
    filename = f'{"tailored-cv" if kind == "cv" else "cover-letter"}-{request.job_id}.{format}'
    return Response(output, media_type='application/pdf' if format == 'pdf' else TYPES['.docx'], headers={
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    })
