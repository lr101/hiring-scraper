"""Render generated LaTeX or returned source checked against an inert grammar."""
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from zipfile import ZipFile

from lxml import etree
from pypdf import PdfReader

_renders = threading.BoundedSemaphore(2)
_ESCAPES = {'\\': r'\textbackslash{}', '{': r'\{', '}': r'\}', '$': r'\$',
            '&': r'\&', '#': r'\#', '%': r'\%', '_': r'\_',
            '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
_TEMPLATE = json.loads(Path(__file__).with_name('latex_template.json').read_text(encoding='utf-8'))


def validate_latex_source(source):
    """Accept only the fixed template and an inert, small body vocabulary.

    Disabling shell escape alone does not sandbox LuaTeX. Reject every command
    outside this grammar before handing returned Canvas source to the engine.
    """
    if (len(source) > 100000 or not source.startswith(_TEMPLATE['preamble'])
            or not source.endswith(_TEMPLATE['ending'])):
        raise ValueError('Use the supplied Radius LaTeX template without changing its preamble or ending.')
    body = source[len(_TEMPLATE['preamble']):-len(_TEMPLATE['ending'])]
    if not body.strip() or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', body):
        raise ValueError('LaTeX needs complete document text without unsupported control characters.')
    position, groups, lists = 0, 0, 0
    while position < len(body):
        char = body[position]
        if char == '\\':
            token = re.match(r'\\(?:begin\{itemize\}|end\{itemize\}|[&%$#_{}]|'
                             r'(?:textbackslash|textasciitilde|textasciicircum)\{\}|'
                             r'(?:section\*|subsection\*|textbf|emph)(?=\{)|'
                             r'(?:par|item)(?![A-Za-z]))', body[position:])
            if token is None:
                raise ValueError('Unsupported LaTeX command. Use only the document-body commands in the brief.')
            value = token[0]
            if value == r'\begin{itemize}':
                lists += 1
            elif value == r'\end{itemize}':
                lists -= 1
            elif value == r'\item' and not lists:
                raise ValueError('LaTeX list items must be inside itemize.')
            if lists < 0 or lists > 4:
                raise ValueError('Use balanced LaTeX lists with at most four levels.')
            position += len(value)
            continue
        if char in '%$#&_~^':
            raise ValueError('Escape LaTeX special characters using the examples in the brief.')
        if char == '{':
            groups += 1
        elif char == '}':
            groups -= 1
        if groups < 0 or groups > 20:
            raise ValueError('Use balanced LaTeX braces with at most twenty levels.')
        position += 1
    if groups or lists:
        raise ValueError('Close every LaTeX group and itemize list before returning the document.')
    return source


def _escape(text):
    # One pass: generated escapes must never be reinterpreted as input.
    return ''.join(_ESCAPES.get(char, char) for char in text)


def _inline(text):
    return ''.join(r'\textbf{' + _escape(part[2:-2]) + '}'
                   if part.startswith('**') and part.endswith('**') and len(part) > 4 else _escape(part)
                   for part in re.split(r'(\*\*[^*\n]+\*\*)', text))


def docx_text(data):
    """Carry contact details in headers/footers into the new LaTeX layout."""
    from hiring_scraper.app.cv_templates import template_paragraphs
    body = '\n'.join(row['text'] for row in template_paragraphs(data))
    parts = {'header': [], 'footer': []}
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    with ZipFile(BytesIO(data)) as archive:
        for name in sorted(archive.namelist()):
            match = re.fullmatch(r'word/(header|footer)\d+\.xml', name)
            if not match:
                continue
            root = etree.fromstring(archive.read(name), etree.XMLParser(resolve_entities=False, no_network=True))
            paragraphs = [''.join(node.text or '' for node in row.iter(ns + 't')
                                  if next(node.iterancestors(ns + 'p'), None) is row)
                          for row in root.iter(ns + 'p')]
            parts[match[1]].extend(paragraphs)
    text = '\n'.join([*parts['header'], body, *parts['footer']])
    if len(text) > 100000:
        raise ValueError('The CV contains too much text for a LaTeX export.')
    return text


def latex_source(text):
    """A selectable-text, single-column A4 layout for a CV or letter."""
    lines = []
    in_list = False
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        heading = re.match(r'^(#{1,3})\s+(.+)$', line)
        bullet = re.match(r'^\s*[-*]\s+(.+)$', line)
        if in_list and not bullet:
            lines.append(r'\end{itemize}')
            in_list = False
        if bullet:
            if not in_list:
                lines.append(r'\begin{itemize}')
                in_list = True
            lines.append(r'\item ' + _inline(bullet[1]))
        elif heading:
            command = {'#': 'section', '##': 'section', '###': 'subsection'}[heading[1]]
            lines.append('\\' + command + '*{' + _inline(heading[2]) + '}')
        elif line.strip():
            lines.append(_inline(line) + r'\par')
        else:
            lines.append('')
    if in_list:
        lines.append(r'\end{itemize}')
    return _TEMPLATE['preamble'] + '\n'.join(lines) + '\n' + _TEMPLATE['ending']


def render_latex_pdf(text, *, source=None):
    source = validate_latex_source(source if source is not None else latex_source(text))
    executable = shutil.which('lualatex')
    if not executable:
        raise RuntimeError('LaTeX PDF generation is unavailable. Download the LaTeX source or choose DOCX/PDF instead.')
    if not _renders.acquire(timeout=3):
        raise RuntimeError('LaTeX generation is busy. Try again in a moment.')
    try:
        with tempfile.TemporaryDirectory(prefix='radius-latex-') as folder:
            root = Path(folder)
            (root / 'application.tex').write_text(source, encoding='utf-8')
            # Only grammar-checked source reaches the engine. Disable shell
            # escape and confine output/cache files to this temporary directory.
            env = {**os.environ, 'openin_any': 'p', 'openout_any': 'p', 'HOME': folder,
                   'TMPDIR': folder, 'TEXMFOUTPUT': folder, 'TEXMFVAR': str(root / 'cache'),
                   'TEXMFCACHE': str(root / 'cache')}
            try:
                process = subprocess.Popen([executable, '-no-shell-escape', '-interaction=nonstopmode',
                                            '-halt-on-error', 'application.tex'], cwd=folder, env=env,
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, start_new_session=True)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise RuntimeError('LaTeX generation timed out. Download the source or try DOCX.') from None
            except OSError:
                raise RuntimeError('Could not start LaTeX. Download the source or try DOCX.') from None
            output = root / 'application.pdf'
            if process.returncode or not output.exists() or output.stat().st_size > 5 * 1024 * 1024:
                raise RuntimeError('Could not render this document with LaTeX. Download the source or try DOCX.')
            log = (root / 'application.log').read_text(encoding='utf-8', errors='replace')
            if 'Missing character:' in log:
                raise ValueError('The LaTeX font cannot display some characters in this document. Use DOCX or download the source and select a suitable font.')
            data = output.read_bytes()
            try:
                reader = PdfReader(BytesIO(data))
                if not reader.pages or not any(page.extract_text().strip() for page in reader.pages):
                    raise ValueError('LaTeX produced a PDF without readable text. Try DOCX instead.')
            except ValueError:
                raise
            except Exception:
                raise RuntimeError('Could not verify the LaTeX PDF text. Try DOCX instead.') from None
            return data
    finally:
        _renders.release()
