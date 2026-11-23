import io
import unittest
import zipfile
from xml.sax.saxutils import escape

from hiring_scraper.documents import build_evidence
from hiring_scraper.documents.import_docx import DocxImportError, import_docx

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>'
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
    'Target="mailto:jonas.fiktiv@example.test" TargetMode="External"/>'
    '</Relationships>'
)


def run(text, *, b=False, i=False, sz=None):
    props = ''
    if b:
        props += '<w:b/>'
    if i:
        props += '<w:i/>'
    if sz:
        props += f'<w:sz w:val="{sz}"/>'
    rpr = f'<w:rPr>{props}</w:rPr>' if props else ''
    pieces = text.split('\t')
    inner = '<w:tab/>'.join(f'<w:t xml:space="preserve">{escape(p)}</w:t>' for p in pieces)
    return f'<w:r>{rpr}{inner}</w:r>'


def para(*runs, numbered=False):
    ppr = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>' if numbered else ''
    return f'<w:p>{ppr}{"".join(runs)}</w:p>'


DRAWING = '<w:r><w:drawing><wp:inline xmlns:wp="urn:fake"/></w:drawing></w:r>'
LINK = f'<w:hyperlink r:id="rId9">{run("jonas.fiktiv@example.test")}</w:hyperlink>'


def fictional_paragraphs(page_w=11906):
    body = [
        f'<w:p>{DRAWING}{run("Jonas Fiktiv", b=True, sz=28)}</w:p>',
        para(run('Data Analyst | Reporting Automation | Supply Chain')),
        f'<w:p>{run("+49 170 0000000   Kassel, DE   ")}{LINK}</w:p>',
        para(),
        para(run('Analyst who turns messy warehouse data into weekly decisions for operations teams.')),
        para(run('PROFESSIONAL EXPERIENCE', b=True)),
        para(run('LAGERWERK MUSTER GMBH', b=True), run('\tKassel, DE')),
        para(run('Operations Data Analyst', b=True, i=True), run('\t2022 – Present')),
        para(run('Built Python checks that flagged stock mismatches before the weekly close.'), numbered=True),
        para(run('Automated a Power BI report used by five shift leads.'), numbered=True),
        para(run('FIKTIV LOGISTIK AG', b=True), run('\tFulda, DE')),
        para(run('Junior Analyst', i=True), run('     2019-2021')),
        para(run('• Cleaned shipment data for monthly reviews.')),
        para(run('EDUCATION', b=True)),
        para(run('BEISPIEL UNIVERSITY', b=True), run('\tGießen, DE')),
        para(run('M.Sc. Logistics', b=True, i=True), run('\t2017 - 2019')),
        para(run('Focus on network planning and simulation.')),
        para(run('Thesis:', b=True), run(' Simulated cross-dock layouts for a fictional retailer.'), numbered=True),
        para(run('CORE COMPETENCIES', b=True)),
        para(run('Forecasting • Data Cleaning • Stakeholder Reporting')),
        para(run('LANGUAGES', b=True)),
        para(run('German – native'), numbered=True),
        para(run('English – C1'), numbered=True),
    ]
    sect = f'<w:sectPr><w:pgSz w:w="{page_w}" w:h="16838"/></w:sectPr>'
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}"><w:body>{"".join(body)}{sect}</w:body></w:document>'
    )


def build_docx(document_xml, *, extra=None, media=True):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', CONTENT_TYPES)
        archive.writestr('word/document.xml', document_xml)
        archive.writestr('word/_rels/document.xml.rels', RELS)
        if media:
            archive.writestr('word/media/image1.png', b'\x89PNG fake')
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


class DocxImportTests(unittest.TestCase):
    def test_fictional_flowing_cv_maps_to_document(self):
        doc = import_docx(build_docx(fictional_paragraphs()))
        self.assertEqual(doc.header.name, 'Jonas Fiktiv')
        self.assertEqual(doc.headline.text, 'Data Analyst | Reporting Automation | Supply Chain')
        self.assertTrue(doc.photo)
        self.assertEqual(doc.page, 'a4')
        self.assertEqual(doc.language, 'en')
        kinds = {c.kind: c for c in doc.header.contacts}
        self.assertEqual(kinds['phone'].text, '+49 170 0000000')
        self.assertEqual(kinds['location'].text, 'Kassel, DE')
        self.assertEqual(kinds['email'].href, 'mailto:jonas.fiktiv@example.test')

        sections = {s.kind: s for s in doc.sections}
        self.assertEqual(doc.sections[0].kind, 'profile')
        self.assertEqual(len(doc.sections[0].units), 1)

        work = sections['experience'].entries
        self.assertEqual([e.id for e in work], ['work-1', 'work-2'])
        self.assertEqual(work[0].locked.company, 'LAGERWERK MUSTER GMBH')
        self.assertEqual(work[0].locked.location, 'Kassel, DE')
        self.assertEqual(work[0].locked.title, 'Operations Data Analyst')
        self.assertTrue(work[0].locked.current)
        self.assertIsNone(work[0].locked.end)
        self.assertEqual(len(work[0].units), 2)
        self.assertEqual((work[1].locked.start, work[1].locked.end), ('2019', '2021'))
        self.assertEqual(work[1].units[0].text, 'Cleaned shipment data for monthly reviews.')

        study = sections['education'].entries[0]
        self.assertEqual(study.id, 'study-1')
        self.assertEqual(study.kind, 'study')
        self.assertEqual(study.locked.title, 'M.Sc. Logistics')
        self.assertEqual([u.text for u in study.intro], ['Focus on network planning and simulation.'])
        self.assertEqual(study.units[0].lead_in, 'Thesis:')
        self.assertTrue(study.units[0].text.startswith('Simulated'))

        skills = sections['skills']
        self.assertEqual(skills.layout, 'inline')
        self.assertEqual([u.text for u in skills.units], ['Forecasting', 'Data Cleaning', 'Stakeholder Reporting'])
        languages = sections['list']
        self.assertEqual(len(languages.units), 2)

        evidence = build_evidence(doc, {})
        self.assertIn('cv:work-1:b2', evidence)
        self.assertIn('cv:study-1:i1', evidence)

    def test_letter_page_size(self):
        doc = import_docx(build_docx(fictional_paragraphs(page_w=12240)))
        self.assertEqual(doc.page, 'letter')

    def test_rejects_dtd_entities(self):
        xml = fictional_paragraphs().replace(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<?xml version="1.0"?><!DOCTYPE w:document [<!ENTITY a "aaaa">]>',
        )
        with self.assertRaises(DocxImportError):
            import_docx(build_docx(xml))

    def test_rejects_macros_and_non_zip(self):
        with self.assertRaises(DocxImportError):
            import_docx(build_docx(fictional_paragraphs(), extra={'word/vbaProject.bin': b'\x00'}))
        with self.assertRaises(DocxImportError):
            import_docx(b'not a zip file')
        with self.assertRaises(DocxImportError):
            import_docx(b'')

    def test_rejects_missing_document_part(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('[Content_Types].xml', CONTENT_TYPES)
        with self.assertRaises(DocxImportError):
            import_docx(buffer.getvalue())

    def test_import_error_is_import_error_subclass(self):
        self.assertTrue(issubclass(DocxImportError, ImportError))


if __name__ == '__main__':
    unittest.main()
