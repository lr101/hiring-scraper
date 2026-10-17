"""A real-size two-page CV must remain editable, intact and unclipped."""
from io import BytesIO
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from zipfile import ZipFile
from lxml import etree
from pypdf import PdfReader

from cv_realistic_fixture import realistic_docx, tailored_changes
from hiring_scraper.app.cv_templates import edit_template, render_pdf, template_paragraphs


class RealisticCVTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.root = Path(cls.folder.name)
        cls.original = realistic_docx()
        cls.paragraphs = template_paragraphs(cls.original)
        cls.docs, cls.readers = {}, {}
        for variant in ['original', 'tailored', 'expanded', 'stress']:
            data = cls.original if variant == 'original' else edit_template(cls.original, tailored_changes(cls.paragraphs, variant))
            cls.docs[variant] = data
            pdf = render_pdf(data)
            (cls.root / f'{variant}.pdf').write_bytes(pdf)
            cls.readers[variant] = PdfReader(BytesIO(pdf))

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_finished_tailoring_retains_two_pages_styles_and_template_parts(self):
        self.assertEqual(len(self.readers['original'].pages), 2)
        self.assertEqual(len(self.readers['tailored'].pages), 2)
        first, second = [p.extract_text() for p in self.readers['tailored'].pages]
        self.assertIn('Research & Funding Programs', first)
        self.assertIn('Professional experience', first)
        self.assertIn('Selected projects', second)
        self.assertIn('Community involvement', second)
        self.assertIn('Page 1 of 2', first)
        self.assertIn('Page 2 of 2', second)
        with ZipFile(BytesIO(self.original)) as source, ZipFile(BytesIO(self.docs['tailored'])) as changed:
            for name in source.namelist():
                if name != 'word/document.xml': self.assertEqual(source.read(name), changed.read(name), name)
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            before, after = etree.fromstring(source.read('word/document.xml')), etree.fromstring(changed.read('word/document.xml'))
            for selector in ['//w:pPr', '//w:rPr', '//w:tblPr', '//w:tcPr', '//w:sectPr']:
                self.assertEqual([etree.tostring(p) for p in before.xpath(selector, namespaces=ns)],
                                 [etree.tostring(p) for p in after.xpath(selector, namespaces=ns)], selector)
        for row in tailored_changes(self.paragraphs):
            source = next(p['text'] for p in self.paragraphs if p['id'] == row['id'])
            self.assertLessEqual(len(row['text']), max(60, len(source)))

    def test_each_page_keeps_original_header_and_footer_pixels(self):
        for page in [1, 2]:
            for region, y, height in [('header', 0, 48), ('footer', 805, 37)]:
                outputs = []
                for variant in ['original', 'tailored']:
                    target = self.root / f'{variant}-{page}-{region}'
                    subprocess.run(['pdftoppm', '-f', str(page), '-l', str(page), '-r', '72', '-singlefile', '-png',
                                    '-x', '0', '-y', str(y), '-W', '595', '-H', str(height),
                                    str(self.root / f'{variant}.pdf'), str(target)], capture_output=True, check=True)
                    outputs.append(target.with_suffix('.png').read_bytes())
                self.assertEqual(*outputs, f'{region} pixels changed on page {page}')

    def test_longer_edits_reflow_without_losing_text_or_clipping_off_the_page(self):
        normalize = lambda s: re.sub(r'\s+', ' ', s).strip()
        for variant, reader in self.readers.items():
            with self.subTest(variant=variant):
                changes = {} if variant == 'original' else {row['id']: row['text'] for row in tailored_changes(self.paragraphs, variant)}
                actual = normalize('\n'.join(p.extract_text() for p in reader.pages))
                for row in self.paragraphs:
                    self.assertIn(normalize(changes.get(row['id'], row['text'])), actual)
                # Deliberate over-expansion demonstrates the template's page-count limit.
                self.assertEqual(len(reader.pages), {'original': 2, 'tailored': 2, 'expanded': 3, 'stress': 5}[variant])
                subprocess.run(['pdftotext', '-bbox-layout', str(self.root / f'{variant}.pdf'), str(self.root / f'{variant}.html')], check=True, capture_output=True)
                tree = etree.parse(str(self.root / f'{variant}.html'))
                for page in tree.findall('.//{*}page'):
                    words = page.findall('.//{*}word')
                    self.assertGreater(len(words), 20, 'Unexpected blank or near-empty page')
                    for word in words:
                        x1, x2 = float(word.get('xMin')), float(word.get('xMax'))
                        y1, y2 = float(word.get('yMin')), float(word.get('yMax'))
                        self.assertGreaterEqual(x1, 0, word.text)
                        self.assertGreaterEqual(y1, 0, word.text)
                        self.assertLessEqual(x2, float(page.get('width')) + .1, word.text)
                        self.assertLessEqual(y2, float(page.get('height')) + .1, word.text)
                        self.assertGreater(x2, x1)
                        self.assertGreater(y2, y1)
