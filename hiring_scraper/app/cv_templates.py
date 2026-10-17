"""Edit text in the uploaded DOCX package and render that package to PDF."""
from io import BytesIO
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
from zipfile import ZipFile

from lxml import etree

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
_renders = threading.BoundedSemaphore(2)


def _document(data):
    with ZipFile(BytesIO(data)) as archive:
        if len(set(archive.namelist())) != len(archive.namelist()):
            raise ValueError('Use a DOCX without duplicate package parts.')
        if sum(info.file_size for info in archive.infolist()) > 10 * 1024 * 1024:
            raise ValueError('The DOCX template is too large.')
        if any(any(part in name.lower() for part in ['vbaproject', '/embeddings/', 'activex', 'customui', 'webextensions']) for name in archive.namelist()):
            raise ValueError('Use a DOCX without macros or embedded objects.')
        for name in archive.namelist():
            if name.lower().endswith(('.xml', '.rels', '.svg')):
                part = etree.fromstring(archive.read(name), etree.XMLParser(resolve_entities=False, no_network=True))
                if part.getroottree().docinfo.doctype:
                    raise ValueError('Use a DOCX without custom XML entities.')
                if name.lower().endswith('.svg'):
                    raise ValueError('Use PNG or JPEG images in the DOCX template.')
                for node in part.iter():
                    if not isinstance(node.tag, str):
                        continue
                    if etree.QName(node).localname.lower() in {'oleobject', 'control', 'altchunk', 'attachedtemplate'}:
                        raise ValueError('Use a DOCX without active or linked content.')
                    if 'macroenabled' in (node.get('ContentType') or '').casefold():
                        raise ValueError('Use a DOCX without macros.')
            if name.endswith('.rels'):
                for relation in part:
                    if ((relation.get('TargetMode') or '').casefold() == 'external' or '://' in (relation.get('Target') or '')) and not (relation.get('Type') or '').endswith('/hyperlink'):
                        raise ValueError('Use a DOCX without linked external images or templates.')
        xml = archive.read('word/document.xml')
    if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
        raise ValueError('Use a DOCX without custom XML entities.')
    root = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
    if root.getroottree().docinfo.doctype:
        raise ValueError('Use a DOCX without custom XML entities.')
    return root


def _text_nodes(paragraph):
    # Text boxes can contain nested paragraphs; don't edit their text twice.
    return [node for node in paragraph.iter(f'{{{W}}}t')
            if next(node.iterancestors(f'{{{W}}}p'), None) is paragraph]


def template_paragraphs(data):
    root = _document(data)
    return [{'id': index, 'text': ''.join(node.text or '' for node in _text_nodes(paragraph))}
            for index, paragraph in enumerate(root.iter(f'{{{W}}}p')) if _text_nodes(paragraph)]


def edit_template(data, changes):
    root = _document(data)
    paragraphs = list(root.iter(f'{{{W}}}p'))
    seen = set()
    if len(changes) > 1000 or sum(len(row['text']) for row in changes) > 100000:
        raise ValueError('Too many template changes.')
    for change in changes:
        index, text = change['id'], change['text']
        if index in seen or not isinstance(index, int) or index < 0 or index >= len(paragraphs) or len(text) > 5000:
            raise ValueError('A template paragraph is invalid or duplicated.')
        seen.add(index)
        nodes = _text_nodes(paragraphs[index])
        if not nodes:
            raise ValueError('This paragraph has no editable text.')
        original = ''.join(node.text or '' for node in nodes)
        if text == original:
            continue
        # Preserve every run, paragraph, table, section and relationship. Spread
        # the replacement over existing runs, preserving their formatting spans.
        remaining = text.replace('\r', '').replace('\n', ' ')
        for position, node in enumerate(nodes):
            count = len(node.text or '') if position < len(nodes) - 1 else len(remaining)
            node.text, remaining = remaining[:count], remaining[count:]
            node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    output = BytesIO()
    with ZipFile(BytesIO(data)) as source, ZipFile(output, 'w') as target:
        for entry in source.infolist():
            target.writestr(entry, etree.tostring(root, encoding='UTF-8', xml_declaration=True, standalone=True)
                            if entry.filename == 'word/document.xml' else source.read(entry.filename))
    return output.getvalue()


def render_pdf(data):
    _document(data)  # Recheck before a document renderer sees external relationships.
    executable = shutil.which('libreoffice') or shutil.which('soffice')
    if not executable:
        raise RuntimeError('PDF conversion is unavailable. Download the DOCX and export it in your document editor.')
    if not _renders.acquire(timeout=3):
        raise RuntimeError('PDF conversion is busy. Try again in a moment.')
    try:
        with tempfile.TemporaryDirectory(prefix='cv-render-') as folder:
            root = Path(folder)
            source = root / 'cv.docx'
            source.write_bytes(data)
            profile = root / 'office-profile'
            profile.mkdir()
            (profile / 'user').mkdir()
            (profile / 'user/registrymodifications.xcu').write_text('''<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item></oor:items>''')
            process = subprocess.Popen([executable, f'-env:UserInstallation={profile.as_uri()}', '--headless', '--nologo',
                                        '--nodefault', '--nofirststartwizard', '--norestore', '--convert-to', 'pdf:writer_pdf_Export',
                                        '--outdir', str(root), str(source)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True)
            try:
                process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise RuntimeError('PDF conversion timed out. Download the DOCX instead.') from None
            output = root / 'cv.pdf'
            if process.returncode or not output.exists() or output.stat().st_size > 20 * 1024 * 1024:
                raise RuntimeError('Could not render this template. Download the DOCX to open in your document editor.')
            return output.read_bytes()
    finally:
        _renders.release()
