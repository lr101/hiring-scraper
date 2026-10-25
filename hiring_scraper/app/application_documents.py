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


class LatexDocuments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    cv: str | None = Field(default=None, min_length=1, max_length=100000)
    cover_letter: str | None = Field(default=None, min_length=1, max_length=100000)


class ApplicationDocumentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    job_id: int = Field(gt=0)
    cv_updated_at: str = Field(min_length=1, max_length=200)
    cv: Annotated[str, Field(max_length=100000)] | Annotated[list[TemplateChange], Field(max_length=1000)]
    cover_letter: str = Field(max_length=100000)
    latex_documents: LatexDocuments | None = None
    photo_data_url: str | None = Field(default=None, max_length=2800000)

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
    """Render the brief's simple Markdown subset; supplied prose stays XML text."""
    paragraphs = []
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        heading = re.match(r'^(#{1,3})\s+(.+)$', line)
        bullet = re.match(r'^\s*[-*]\s+(.+)$', line)
        size = {'#': '32', '##': '28', '###': '24'}.get(heading[1], '22') if heading else '22'
        content = heading[2] if heading else '• ' + bullet[1] if bullet else line
        runs = []
        for part in re.split(r'(\*\*[^*\n]+\*\*)', content):
            bold = part.startswith('**') and part.endswith('**') and len(part) > 4
            prose = part[2:-2] if bold else part
            runs.append('<w:r><w:rPr><w:rFonts w:ascii="Liberation Sans" w:hAnsi="Liberation Sans"/>'
                        + ('<w:b/>' if heading or bold else '') + f'<w:sz w:val="{size}"/></w:rPr>'
                        + '<w:t xml:space="preserve">' + escape(prose) + '</w:t></w:r>')
        paragraphs.append('<w:p>' + ''.join(runs) + '</w:p>')
    parts = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        'word/document.xml': '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + ''.join(paragraphs) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
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
                         format: Literal['pdf', 'docx', 'tex', 'latex_pdf', 'tex_bundle'] = 'pdf',
                         session: Session = Depends(get_session)):
    require_profile(profile_id, session)
    if session.get(Job, request.job_id) is None:
        raise HTTPException(404, 'Job not found.')
    cv = session.get(ProfileCV, profile_id)
    # Preserve old API exports for existing DOCX/browser drafts during migration.
    # All new base CVs and the current companion UI use LaTeX exclusively.
    if kind == 'cv' and cv is not None and (cv.variants or cv.filename.lower().endswith('.tex')) and format not in {'tex', 'latex_pdf', 'tex_bundle'}:
        raise HTTPException(422, 'CVs use LaTeX rendering. Download LaTeX PDF or .tex source.')
    if cv is not None and cv_metadata(cv)['updated_at'] != request.cv_updated_at:
        raise HTTPException(409, 'Your saved CV changed. Reload this application package before exporting.')
    preserved_source = False
    if kind == 'cv' and cv is not None:
        from hiring_scraper.app.tex_cv import validate_tailored_cv
        originals = [row['text'] for row in cv.variants.values()] if cv.variants else [cv.extracted_text]
        originals = [text for text in originals if text and '% radius:entry ' in text]
        if originals:
            returned = request.latex_documents.cv if request.latex_documents else None
            if not returned:
                raise HTTPException(422, 'Return the full marked LaTeX CV in latex_documents.cv.')
            for original in originals:
                try:
                    validate_tailored_cv(original, returned)
                    preserved_source = True
                    break
                except ValueError:
                    pass
            if not preserved_source:
                raise HTTPException(422, 'Returned CV changed its structure, layout or history. Edit only existing text in marked profile and responsibility blocks of a saved base CV.')
    changes = request.cv if kind == 'cv' and isinstance(request.cv, list) else None
    if isinstance(request.cv, list) and (cv is None or not cv.filename.lower().endswith('.docx')):
        raise HTTPException(422, 'Attach a DOCX to export paragraph edits with your original CV layout.')
    source = bytes(cv.data) if changes is not None else None
    session.rollback()  # Return the DB connection before editing/conversion.
    try:
        from hiring_scraper.app.latex_documents import photo_bytes, latex_source_bundle
        photo = photo_bytes(request.photo_data_url) if request.photo_data_url else None
        if photo is not None and (kind != 'cv' or format not in {'latex_pdf', 'tex_bundle'}):
            raise ValueError('Photos are supported for CV LaTeX PDFs and source + photo ZIP bundles only.')
        if format == 'tex_bundle' and (kind != 'cv' or photo is None):
            raise ValueError('Choose a CV photo before downloading a source + photo ZIP bundle.')
        if changes is not None:
            # The editor flattens line breaks, which can hide line-start TODOs.
            for row in changes:
                _finished_text(row.text, allow_empty=True)
            output = cv_templates.edit_template(source, [row.model_dump() for row in changes])
            text = '\n'.join(row['text'] for row in cv_templates.template_paragraphs(output))
            _finished_text(text)
        else:
            text = request.cv if kind == 'cv' else request.cover_letter
            _finished_text(text)
            output = _text_docx(text)
        if len(output) > MAX_BYTES:
            raise HTTPException(413, 'This document exceeds 5 MiB. Use a smaller document or DOCX template.')
        if format in {'tex', 'latex_pdf', 'tex_bundle'}:
            from hiring_scraper.app.latex_documents import docx_text, latex_source, render_latex_pdf, validate_latex_source
            if changes is not None:
                text = docx_text(output)
                _finished_text(text)
            returned_source = getattr(request.latex_documents, kind, None)
            if returned_source is not None:
                _finished_text(returned_source)
                # Downloading source does not execute it. Custom templates may
                # be returned only after comparison with the saved original.
                local_bundle = cv is None and kind == 'cv' and format == 'tex_bundle'
                if local_bundle:
                    from hiring_scraper.app.tex_cv import parse_tex_cv
                    parse_tex_cv(returned_source)
                if not (local_bundle or (preserved_source and format in {'tex', 'tex_bundle'})):
                    if preserved_source and returned_source.endswith(r'\end{document}'):
                        returned_source += '\n'
                    try:
                        validate_latex_source(returned_source, letter=kind == 'cover_letter')
                    except ValueError:
                        if preserved_source:
                            raise ValueError('This CV preserves a custom LaTeX template. Download the .tex source and compile locally with its original assets; the server PDF renderer supports only the Radius template.') from None
                        raise
            if format == 'tex_bundle':
                output = latex_source_bundle(returned_source or latex_source(text), photo)
            elif format == 'tex':
                output = (returned_source or latex_source(text, letter=kind == 'cover_letter')).encode('utf-8')
            else:
                output = render_latex_pdf(text, source=returned_source, photo=photo, letter=kind == 'cover_letter')
        elif format == 'pdf':
            output = cv_templates.render_pdf(output)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from None
    if len(output) > MAX_BYTES:
        raise HTTPException(413, 'This document exceeds 5 MiB. Try downloading the DOCX instead.')
    extension = 'pdf' if format == 'latex_pdf' else 'zip' if format == 'tex_bundle' else format
    filename = f'{"tailored-cv" if kind == "cv" else "cover-letter"}-{request.job_id}.{extension}'
    media_type = 'application/zip' if format == 'tex_bundle' else 'application/pdf' if extension == 'pdf' else 'text/plain; charset=utf-8' if format == 'tex' else TYPES['.docx']
    return Response(output, media_type=media_type, headers={
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    })
