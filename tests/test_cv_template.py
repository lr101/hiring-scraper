"""Compare exports to the uploaded DOCX's actual layout, not a generic CV layout."""
from io import BytesIO
import importlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from zipfile import ZipFile


def template_docx():
    parts = {
        '[Content_Types].xml': '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/></Types>''',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        'word/_rels/document.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="header" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/></Relationships>',
        'word/header1.xml': '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:rPr><w:color w:val="35654D"/><w:sz w:val="18"/></w:rPr><w:t>ORIGINAL CV TEMPLATE · ALEX</w:t></w:r></w:p></w:hdr>',
        'word/document.xml': '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>
<w:p><w:pPr><w:spacing w:after="200"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Liberation Sans" w:hAnsi="Liberation Sans"/><w:b/><w:color w:val="35654D"/><w:sz w:val="40"/></w:rPr><w:t>Alex Example</w:t></w:r></w:p>
<w:tbl><w:tblPr><w:tblW w:w="10000" w:type="dxa"/></w:tblPr><w:tblGrid><w:gridCol w:w="2800"/><w:gridCol w:w="7200"/></w:tblGrid><w:tr>
<w:tc><w:tcPr><w:tcW w:w="2800" w:type="dxa"/><w:shd w:fill="E7EEE8"/></w:tcPr><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>CONTACT</w:t></w:r></w:p><w:p><w:r><w:t>alex@example.invalid</w:t></w:r></w:p></w:tc>
<w:tc><w:tcPr><w:tcW w:w="7200" w:type="dxa"/></w:tcPr><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>EXPERIENCE</w:t></w:r></w:p><w:p><w:pPr><w:spacing w:before="160"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Liberation Sans"/><w:sz w:val="22"/></w:rPr><w:t>Coordinate projects</w:t></w:r></w:p></w:tc>
</w:tr></w:tbl><w:sectPr><w:headerReference w:type="default" r:id="header"/><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="900" w:right="900" w:bottom="900" w:left="900" w:header="350" w:footer="350"/></w:sectPr></w:body></w:document>''',
    }
    data = BytesIO()
    with ZipFile(data, 'w') as archive:
        for name, text in parts.items():
            archive.writestr(name, text.encode())
    return data.getvalue()


class TemplateTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('hiring_scraper.app.cv_templates'), 'Original DOCX template editing must be implemented')
        return importlib.import_module('hiring_scraper.app.cv_templates')

    def test_edits_preserve_all_template_parts_and_paragraph_formatting(self):
        module = self.api()
        original = template_docx()
        paragraphs = module.template_paragraphs(original)
        target = next(row for row in paragraphs if row['text'] == 'Coordinate projects')
        edited = module.edit_template(original, [{'id': target['id'], 'text': 'Coordinate programs'}])
        with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(edited)) as after:
            self.assertEqual(before.namelist(), after.namelist())
            for name in before.namelist():
                if name != 'word/document.xml':
                    self.assertEqual(before.read(name), after.read(name), name)
            from lxml import etree
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            a, b = etree.fromstring(before.read('word/document.xml')), etree.fromstring(after.read('word/document.xml'))
            for xpath in ['//w:sectPr', '//w:tblPr', '//w:tblGrid', '//w:tcPr', '//w:pPr', '//w:rPr']:
                self.assertEqual([etree.tostring(row) for row in a.xpath(xpath, namespaces=ns)],
                                 [etree.tostring(row) for row in b.xpath(xpath, namespaces=ns)], xpath)
        self.assertIn('Coordinate programs', str(module.template_paragraphs(edited)))
        with self.assertRaises(ValueError): module.edit_template(original, [{'id': 9999, 'text': 'Injected paragraph'}])
        with self.assertRaises(ValueError): module.edit_template(original, [target, target])

    def test_unsafe_content_in_headers_or_other_package_parts_is_rejected(self):
        module = self.api()
        original = template_docx()
        for unsafe in ['<!DOCTYPE doc SYSTEM "file:///etc/hostname">',
                       '<!DOCTYPE doc [<!ENTITY local SYSTEM "file:///etc/hostname">]>']:
            output = BytesIO()
            with ZipFile(BytesIO(original)) as source, ZipFile(output, 'w') as target:
                for entry in source.infolist():
                    content = source.read(entry.filename)
                    if entry.filename == 'word/header1.xml':
                        content = unsafe.encode() + content
                    target.writestr(entry, content)
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                module.template_paragraphs(output.getvalue())

    def test_pdf_keeps_original_header_columns_fonts_and_page_geometry(self):
        module = self.api()
        if not shutil.which('libreoffice') or not shutil.which('pdftoppm'):
            self.fail('Install LibreOffice Writer and poppler-utils to verify original-template PDF rendering')
        from pypdf import PdfReader
        original = template_docx()
        paragraph = next(row for row in module.template_paragraphs(original) if row['text'] == 'Coordinate projects')
        before = module.render_pdf(original)
        after = module.render_pdf(module.edit_template(original, [{'id': paragraph['id'], 'text': 'Coordinate programs'}]))
        pdf_a, pdf_b = PdfReader(BytesIO(before)), PdfReader(BytesIO(after))
        self.assertEqual(len(pdf_a.pages), 1)
        self.assertEqual(len(pdf_b.pages), 1)
        self.assertEqual(pdf_a.pages[0].mediabox, pdf_b.pages[0].mediabox)
        self.assertIn('ORIGINAL CV TEMPLATE', pdf_b.pages[0].extract_text())
        self.assertIn('Coordinate programs', pdf_b.pages[0].extract_text())
        self.assertNotIn('Coordinate projects', pdf_b.pages[0].extract_text())
        # Unchanged heading and contact column must have the same positions/font sizes.
        def positions(page):
            found = []
            def visitor(text, cm, tm, font, size):
                if any(word in text for word in ['Alex Example', 'CONTACT', 'EXPERIENCE', 'alex@example.invalid']):
                    found.append((text, tuple(cm), tuple(tm), size, str(font.get('/BaseFont')) if font else None))
            page.extract_text(visitor_text=visitor)
            return found
        self.assertTrue(positions(pdf_a.pages[0]))
        self.assertEqual(positions(pdf_a.pages[0]), positions(pdf_b.pages[0]))
        # Compare pixels in the unchanged header against the ORIGINAL rendered upload.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, content in [('original', before), ('edited', after)]:
                (root / f'{name}.pdf').write_bytes(content)
                subprocess.run(['pdftoppm', '-r', '72', '-singlefile', '-png', '-x', '0', '-y', '0', '-W', '595', '-H', '75',
                                str(root / f'{name}.pdf'), str(root / name)], check=True, capture_output=True)
            self.assertEqual((root / 'original.png').read_bytes(), (root / 'edited.png').read_bytes(),
                             'The original template header must remain visually identical')
