"""Render generated LaTeX or returned source checked against an inert grammar."""
import base64
import binascii
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import tempfile
import threading
from zipfile import ZipFile

from lxml import etree
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

_renders = threading.BoundedSemaphore(2)
_ESCAPES = {'\\': r'\textbackslash{}', '{': r'\{', '}': r'\}', '$': r'\$',
            '&': r'\&', '#': r'\#', '%': r'\%', '_': r'\_',
            '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
_TEMPLATE = json.loads(Path(__file__).with_name('latex_template.json').read_text(encoding='utf-8'))


def validate_latex_source(source, *, letter=False):
    """Accept only the fixed template and an inert, small body vocabulary.

    Disabling shell escape alone does not sandbox LuaTeX. Reject every command
    outside this grammar before handing returned Canvas source to the engine.
    """
    preamble = next((value for value in (_TEMPLATE['preamble'], _TEMPLATE['letter_preamble']) if source.startswith(value)), None)
    if (len(source) > 100000 or preamble is None
            or not source.endswith(_TEMPLATE['ending'])):
        raise ValueError('Use the supplied Radius LaTeX template without changing its preamble or ending.')
    letter = letter or preamble == _TEMPLATE['letter_preamble']
    body = source[len(preamble):-len(_TEMPLATE['ending'])]
    if not body.strip() or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', body):
        raise ValueError('LaTeX needs complete document text without unsupported control characters.')
    if re.search(r'^\s*% radius:', body, re.M):
        from hiring_scraper.app.tex_cv import parse_tex_cv
        parse_tex_cv(source)
        if '^^' in body:
            raise ValueError('LaTeX character-code escapes are not supported.')
        body = re.sub(r'^\s*% radius:[^\n]*', '', body, flags=re.M)
    allowed = (r'\\|thispagestyle\{empty\}|[&%$#_{}]|'
               r'(?:textbackslash|textasciitilde|textasciicircum)\{\}|'
               r'(?:textbf|emph)(?=\{)|par(?![A-Za-z])')
    if not letter:
        allowed += r'|begin\{itemize\}|end\{itemize\}|(?:section\*|subsection\*)(?=\{)|item(?![A-Za-z])'
    position, groups, lists = 0, 0, 0
    while position < len(body):
        char = body[position]
        if char == '\\':
            token = re.match(r'\\(?:' + allowed + ')', body[position:])
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


def latex_source(text, *, letter=False):
    """A selectable-text, single-column A4 layout for a CV or letter."""
    if letter:
        blocks = re.split(r'\n(?:[ \t]*\n)+', text.replace('\r\n', '\n').replace('\r', '\n').strip())
        body = r'\thispagestyle{empty}' + '\n'
        body += '\n'.join(r'\\'.join(_inline(line) for line in block.splitlines()) + r'\par' for block in blocks)
        return _TEMPLATE['letter_preamble'] + body + '\n' + _TEMPLATE['ending']
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


def photo_bytes(data_url):
    """Only bounded raster PNG data is materialized, under a fixed asset name."""
    if not data_url.startswith('data:image/png;base64,') or len(data_url) > 2800000:
        raise ValueError('Choose a PNG photo under 2 MiB.')
    try:
        data = base64.b64decode(data_url.split(',', 1)[1], validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('Could not read this PNG photo.') from None
    if (len(data) < 45 or len(data) > 2 * 1024 * 1024 or not data.startswith(b'\x89PNG\r\n\x1a\n')
            or data[8:16] != b'\x00\x00\x00\rIHDR'):
        raise ValueError('Choose a valid PNG photo under 2 MiB.')
    width, height = struct.unpack('!II', data[16:24])
    if not (1 <= width <= 4096 and 1 <= height <= 4096):
        raise ValueError('Use a PNG photo no larger than 4096 pixels per side.')
    # Validate complete chunk framing and checksums before using or bundling it.
    offset, image_data, ended = 8, False, False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError('The PNG photo is truncated or corrupt.')
        size = struct.unpack('!I', data[offset:offset + 4])[0]
        end = offset + size + 12
        if end > len(data):
            raise ValueError('The PNG photo is truncated or corrupt.')
        kind = data[offset + 4:offset + 8]
        checksum = struct.unpack('!I', data[end - 4:end])[0]
        if binascii.crc32(data[offset + 4:end - 4]) != checksum:
            raise ValueError('The PNG photo is truncated or corrupt.')
        if kind == b'IHDR' and offset != 8:
            raise ValueError('The PNG photo has an invalid image header.')
        if kind == b'IDAT' and size:
            image_data = True
        if kind == b'IEND':
            ended = size == 0 and end == len(data)
            break
        offset = end
    if not image_data or not ended:
        raise ValueError('The PNG photo is truncated or corrupt.')
    try:
        with Image.open(BytesIO(data), formats=['PNG']) as image:
            image.verify()
        with Image.open(BytesIO(data), formats=['PNG']) as image:
            image.load()  # Verify compressed pixels as well as chunk checksums.
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise ValueError('The PNG photo is truncated or corrupt.') from None
    return data


def source_with_photo(source):
    """Add a photo beside the fixed-template header, after source validation."""
    validate_latex_source(source)
    if not source.startswith(_TEMPLATE['preamble']):
        raise ValueError('Photos use the CV template, not the letter template.')
    body = source[len(_TEMPLATE['preamble']):-len(_TEMPLATE['ending'])]
    sections = list(re.finditer(r'\\section\*\{', body))
    # Keep the name/contact header together; reserve a separate photo column.
    split = sections[1].start() if len(sections) > 1 else len(body)
    header, rest = body[:split], body[split:]
    preamble = _TEMPLATE['preamble'].replace(r'\begin{document}', r'\usepackage{graphicx}' + '\n' + r'\begin{document}')
    return (preamble + r'\noindent\begin{minipage}[t]{0.76\textwidth}\vspace{0pt}' + '\n' + header
            + '\n' + r'\end{minipage}\hfill\begin{minipage}[t]{0.20\textwidth}\vspace{0pt}'
            + r'\includegraphics[width=\linewidth,height=35mm,keepaspectratio]{portrait.png}'
            + r'\end{minipage}\par' + '\n' + rest + _TEMPLATE['ending'])


def latex_source_bundle(source, photo):
    """Package source and one portrait; custom source is never compiled here."""
    try:
        validate_latex_source(source)
        fixed = True
    except ValueError:
        fixed = False
    if fixed:
        source, filename = source_with_photo(source), 'portrait.png'
    else:
        images = re.findall(r'\\includegraphics(?:\[[^]\n]*\])?\{([^}\n]+)\}', source)
        if len(images) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]+\.png', images[0]):
            raise ValueError('For a photo bundle, the custom CV must reference one PNG portrait using a plain filename, such as portrait_cropped.png.')
        filename = images[0]
    output = BytesIO()
    with ZipFile(output, 'w') as bundle:
        bundle.writestr('application.tex', source)
        bundle.writestr(filename, photo)
        bundle.writestr('README.txt', 'Extract all files into one folder. Compile the trusted CV locally with:\n'
                        'lualatex -no-shell-escape application.tex\n'
                        'The header text and custom layout are retained. The supplied photo fills the existing portrait slot.\n')
    return output.getvalue()


def render_latex_pdf(text, *, source=None, photo=None, letter=False):
    source = validate_latex_source(source if source is not None else latex_source(text, letter=letter), letter=letter)
    if photo is not None:
        source = source_with_photo(source)
    executable = shutil.which('lualatex')
    if not executable:
        raise RuntimeError('LaTeX PDF generation is unavailable. Download the LaTeX source or choose DOCX/PDF instead.')
    if not _renders.acquire(timeout=3):
        raise RuntimeError('LaTeX generation is busy. Try again in a moment.')
    try:
        with tempfile.TemporaryDirectory(prefix='radius-latex-') as folder:
            root = Path(folder)
            (root / 'application.tex').write_text(source, encoding='utf-8')
            if photo is not None:
                (root / 'portrait.png').write_bytes(photo)
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
