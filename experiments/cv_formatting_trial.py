"""Render a fictional real-size CV and record formatting stress-test evidence.
Run: .venv/bin/python experiments/cv_formatting_trial.py
"""
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import sys
from zipfile import ZipFile
from lxml import etree
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from cv_realistic_fixture import realistic_docx, tailored_changes
from hiring_scraper.app.cv_templates import edit_template, render_pdf, template_paragraphs


def main():
    folder = ROOT / 'reports/cv-formatting'
    folder.mkdir(exist_ok=True)
    original = realistic_docx()
    rows = template_paragraphs(original)
    normalize = lambda text: re.sub(r'\s+', ' ', text).strip()
    results = []
    for variant in ['original', 'tailored', 'expanded', 'stress']:
        changes = [] if variant == 'original' else tailored_changes(rows, variant)
        doc = original if not changes else edit_template(original, changes)
        pdf = render_pdf(doc)
        prefix = folder / f'mara-neumann-{variant}'
        prefix.with_suffix('.docx').write_bytes(doc)
        prefix.with_suffix('.pdf').write_bytes(pdf)
        reader = PdfReader(BytesIO(pdf))
        text = normalize('\n'.join(page.extract_text() for page in reader.pages))
        replacements = {row['id']: row['text'] for row in changes}
        complete = all(normalize(replacements.get(row['id'], row['text'])) in text for row in rows)
        subprocess.run(['pdftotext', '-bbox-layout', str(prefix.with_suffix('.pdf')), str(prefix.with_suffix('.html'))], check=True, capture_output=True)
        tree = etree.parse(str(prefix.with_suffix('.html')))
        outside = []
        words = 0
        for page_number, page in enumerate(tree.findall('.//{*}page'), 1):
            width, height = float(page.get('width')), float(page.get('height'))
            for word in page.findall('.//{*}word'):
                words += 1
                if float(word.get('xMin')) < 0 or float(word.get('yMin')) < 0 or float(word.get('xMax')) > width + .1 or float(word.get('yMax')) > height + .1:
                    outside.append({'page': page_number, 'word': word.text})
        with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(doc)) as after:
            unchanged = all(before.read(name) == after.read(name) for name in before.namelist() if name != 'word/document.xml')
        # Render pages independently: this Poppler build's multi-page raster
        # mode can omit repeated header/footer glyphs from later PNGs.
        for page_number in range(1, len(reader.pages) + 1):
            subprocess.run(['pdftoppm', '-f', str(page_number), '-l', str(page_number), '-singlefile', '-scale-to', '1200', '-png',
                            str(prefix.with_suffix('.pdf')), str(folder / f'mara-neumann-{variant}-{page_number}')], check=True, capture_output=True)
        result = {'variant': variant, 'pages': len(reader.pages), 'words_checked': words,
                  'all_paragraph_text_preserved': complete, 'words_outside_page': outside,
                  'unchanged_template_parts': unchanged, 'edited_paragraphs': len(changes),
                  'paragraphs_over_original_length_budget': sum(len(row['text']) > max(60, len(next(p['text'] for p in rows if p['id'] == row['id']))) for row in changes)}
        results.append(result)
        print(json.dumps(result))
    crop_checks = []
    for page in [1, 2]:
        for region, y, height in [('header', 0, 48), ('footer', 805, 37)]:
            images = []
            for variant in ['original', 'tailored']:
                target = folder / f'{variant}-{region}-{page}'
                subprocess.run(['pdftoppm', '-f', str(page), '-l', str(page), '-r', '72', '-singlefile', '-png', '-x', '0', '-y', str(y), '-W', '595', '-H', str(height), str(folder / f'mara-neumann-{variant}.pdf'), str(target)], check=True, capture_output=True)
                images.append(target.with_suffix('.png').read_bytes())
            crop_checks.append({'page': page, 'region': region, 'identical_pixels': images[0] == images[1]})
    report = {'fictional_sample': True, 'person': 'Mara Neumann', 'results': results, 'unchanged_header_footer': crop_checks,
              'note': 'Original and concise tailoring stay at two pages. Deliberate expansion demonstrates reflow; page count is not guaranteed for arbitrary longer text. No live personal ChatGPT account was used.'}
    (folder / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    assert all(result['all_paragraph_text_preserved'] and not result['words_outside_page'] and result['unchanged_template_parts'] for result in results)
    assert all(check['identical_pixels'] for check in crop_checks)
    assert results[0]['pages'] == results[1]['pages'] == 2


if __name__ == '__main__':
    main()
