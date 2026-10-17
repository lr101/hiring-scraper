"""Bounded CV text extraction, run in a short-lived process for uploaded files."""
from io import BytesIO
import json
import sys
from xml.etree import ElementTree
from zipfile import ZipFile

MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT = 100000
TYPES = {'.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.txt': 'text/plain'}


def extract_text(data, extension):
    if extension == '.txt':
        text = data.decode('utf-8-sig')
        if '\x00' in text:
            raise ValueError('Use a UTF-8 text file.')
    elif extension == '.docx':
        from hiring_scraper.app.cv_templates import template_paragraphs
        template_paragraphs(data)  # Reject unsafe external content before storage/rendering.
        with ZipFile(BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > 10 * 1024 * 1024:
                raise ValueError('The expanded DOCX is too large. Export a smaller CV.')
            xml = archive.read('word/document.xml')
            if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                raise ValueError('This DOCX cannot be read. Export a new copy.')
            root = ElementTree.fromstring(xml)
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            text = '\n'.join(''.join(row.itertext()) for row in root.iter(ns + 'p'))
    elif extension == '.pdf':
        if not data.startswith(b'%PDF-'):
            raise ValueError('This file is not a readable PDF.')
        from pypdf import PdfReader, apply_configuration
        with apply_configuration(maximum_declared_stream_length=10 * 1024 * 1024,
                                 zlib_maximum_output_length=10 * 1024 * 1024,
                                 page_tree_maximum_entries=100, stream_decoding_work_maximum_length=25 * 1024 * 1024):
            reader = PdfReader(BytesIO(data), root_object_recovery_limit=1000)
            if reader.is_encrypted:
                raise ValueError('Upload a PDF without password protection.')
            if len(reader.pages) > 20:
                raise ValueError('Use a CV with at most 20 pages.')
            parts = []
            for page in reader.pages:
                parts.append(page.extract_text() or '')
                if sum(len(part) for part in parts) > MAX_TEXT:
                    raise ValueError('The CV contains too much text.')
            text = '\n'.join(parts)
    else:
        raise ValueError('Choose a PDF, DOCX or UTF-8 TXT file.')
    text = text.strip()
    if not text:
        raise ValueError('No readable text found. Use a text-based PDF, DOCX or TXT file instead of a scan.')
    if len(text) > MAX_TEXT:
        raise ValueError('The CV contains too much text. Use at most 100,000 characters.')
    return text


def main():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    except ImportError:
        pass
    try:
        data = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('CV files must be 5 MB or smaller.')
        print(json.dumps({'text': extract_text(data, sys.argv[1])}))
    except ValueError as error:
        # Only our own validation messages are exposed; library failures are generic.
        allowed = ('Use a ', 'The expanded ', 'This DOCX ', 'This file ', 'Upload a ', 'The CV ', 'No readable ', 'Choose a ', 'CV files ')
        message = str(error) if str(error).startswith(allowed) else 'Could not read this CV. Try a text-based PDF, DOCX or TXT.'
        print(json.dumps({'error': message}))
        sys.exit(1)
    except Exception:
        print(json.dumps({'error': 'Could not read this CV. Try a text-based PDF, DOCX or TXT.'}))
        sys.exit(1)


if __name__ == '__main__':
    main()
