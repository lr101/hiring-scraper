import base64
import unittest
from io import BytesIO

from hiring_scraper.app import application_pdf
from hiring_scraper.documents import (
    Contact,
    CvDocument,
    Entry,
    Header,
    LetterDocument,
    LockedEntry,
    Section,
    Sender,
    Unit,
)

# 1x1 transparent PNG
PIXEL = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
)


def fictional_cv(bullets: int = 3) -> CvDocument:
    return CvDocument(
        language='de',
        page='a4',
        photo=True,
        header=Header(
            name='Jörg Übermaß',
            contacts=[
                Contact(kind='phone', text='+49 170 0000000'),
                Contact(kind='email', text='joerg@example.test', href='mailto:joerg@example.test'),
                Contact(kind='location', text='Göttingen, DE'),
            ],
        ),
        headline=Unit(id='u-headline', text='Projektingenieur Energiespeicher'),
        sections=[
            Section(id='s-profile', kind='profile', title='', layout='paragraphs',
                    units=[Unit(id='u-profile-1', text='Ingenieur für Batteriespeicher mit Erfahrung in Prüfständen.')]),
            Section(
                id='s-experience', kind='experience', title='BERUFSERFAHRUNG', layout='bullets',
                entries=[
                    Entry(
                        id='work-1', kind='work',
                        locked=LockedEntry(company='Beispielwerk GmbH', title='Projektingenieur',
                                           location='Kassel', start='2021', end=None, current=True),
                        units=[
                            Unit(id=f'u-work-1-b{n}', text=f'Plante Prüfläufe für Speichermodule und werte Messreihe {n} aus. ' * (1 if bullets < 10 else 4))
                            for n in range(1, bullets + 1)
                        ],
                    ),
                ],
            ),
            Section(id='s-skills', kind='skills', title='KENNTNISSE', layout='inline',
                    units=[Unit(id='u-skills-1', text='Python'), Unit(id='u-skills-2', text='Messtechnik')]),
        ],
    )


def fictional_letter() -> LetterDocument:
    return LetterDocument(
        language='de',
        sender=Sender(name='Jörg Übermaß', lines=['Göttingen', 'joerg@example.test']),
        recipient_lines=['Speicherfabrik Muster AG', 'Personalabteilung'],
        place_date='Göttingen, 23. November 2026',
        subject=Unit(id='u-subject', text='Bewerbung als Projektingenieur'),
        salutation=Unit(id='u-salutation', text='Sehr geehrte Damen und Herren,'),
        paragraphs=[Unit(id=f'u-p{n}', text='Bei Beispielwerk plane ich Prüfläufe und werte Messreihen aus.',
                         origin='ai', sources=['cv:work-1:b1']) for n in range(1, 5)],
        closing=Unit(id='u-closing', text='Mit freundlichen Grüßen'),
        signature='Jörg Übermaß',
    )


@unittest.skipUnless(application_pdf.available(), 'Chromium or frontend/dist/print.html unavailable')
class RenderTests(unittest.TestCase):
    def _passed(self, result, code):
        return next(c for c in result.checks if c['code'] == code)['status']

    def test_cv_renders_with_contacts_and_umlauts(self):
        result = application_pdf.render_document('cv', fictional_cv().model_dump(mode='json'), photo_png=PIXEL)
        self.assertTrue(result.pdf.startswith(b'%PDF'))
        self.assertEqual(result.pages, 1)
        self.assertFalse(result.page_limit_exceeded)
        self.assertIn('Prüfständen', result.text)
        self.assertIn('ÜBERMASS', result.text.upper())
        self.assertNotIn('(cid:', result.text)
        self.assertNotIn('�', result.text)
        for code in ('contacts_present', 'glyphs', 'special_characters', 'name_present', 'page_count'):
            self.assertEqual(self._passed(result, code), 'passed', code)
        self.assertIn('content_height_px', result.telemetry)

    def test_letter_renders_one_page(self):
        result = application_pdf.render_document('letter', fictional_letter().model_dump(mode='json'))
        self.assertEqual(result.pages, 1)
        self.assertIn('Grüßen', result.text)
        self.assertEqual(self._passed(result, 'name_present'), 'passed')

    def test_multi_entry_cv_extracts_headers_and_bullets_in_order(self):
        doc = fictional_cv(bullets=1)
        doc.sections[1].entries += [
            Entry(id='work-2', kind='work',
                  locked=LockedEntry(company='Fiktivlabor', title='Praktikant',
                                     start='2020', end='2020'),
                  units=[Unit(id='u-work-2-b1', text='Kalibrierte Sensoren im Prüfstand.')]),
        ]
        doc.sections[2].layout = 'columns'
        doc.sections[2].units.insert(1, Unit(id='u-skills-3', text='Konstruktion'))
        result = application_pdf.render_document('cv', doc.model_dump(), photo_png=PIXEL)
        self.assertEqual(self._passed(result, 'reading_order'), 'passed')
        self.assertTrue(result.telemetry['photo_used'])
        self.assertNotIn('2020-2020', result.text)
        self.assertIn('2021-heute', result.text)
        self.assertNotIn('‐', result.text)
        parts = ('BEISPIELWERK', 'Plante Prüfläufe', 'FIKTIVLABOR', '2020',
                 'Kalibrierte Sensoren', 'Python', 'Konstruktion', 'Messtechnik')
        positions = [result.text.index(part) for part in parts]
        self.assertEqual(positions, sorted(positions))
        self.assertRegex(result.text, r'●\s*Kalibrierte')

    def test_pdf_metadata_identifies_document_and_author(self):
        from pypdf import PdfReader

        for kind, doc, title in [
            ('cv', fictional_cv(), 'Jörg Übermaß – Lebenslauf'),
            ('letter', fictional_letter(), 'Jörg Übermaß – Anschreiben'),
        ]:
            with self.subTest(kind=kind):
                result = application_pdf.render_document(kind, doc.model_dump())
                metadata = PdfReader(BytesIO(result.pdf)).metadata
                self.assertEqual(metadata.title, title)
                self.assertEqual(metadata.author, 'Jörg Übermaß')

    def test_education_keeps_institution_heading_and_bullets_together(self):
        from pypdf import PdfReader

        doc = fictional_cv(bullets=15)
        doc.sections.insert(2, Section(
            id='s-education', kind='education', title='Ausbildung', entries=[
                Entry(id='study-1', kind='study', label='Hochschule',
                      locked=LockedEntry(company='University', title='Fiktivlabor', start='2020', end='2020'),
                      units=[
                          Unit(id=f'u-study-b{n}', text=f'Auswertung {n}: ' + 'Kalibrierte Sensoren im Prüfstand. ' * 6)
                          for n in range(1, 5)
                      ]),
            ],
        ))
        result = application_pdf.render_document('cv', doc.model_dump())
        pages = [page.extract_text() for page in PdfReader(BytesIO(result.pdf)).pages]
        self.assertEqual(len(pages), 2)
        self.assertNotIn('UNIVERSITY', result.text)
        self.assertNotIn('HOCHSCHULE', result.text)
        self.assertEqual(result.text.upper().count('FIKTIVLABOR'), 1)
        for part in ('AUSBILDUNG', 'FIKTIVLABOR', 'Auswertung 1:', 'Auswertung 4:'):
            self.assertIn(part, pages[1])

    def test_long_cv_reports_overflow(self):
        doc = fictional_cv(bullets=45).model_dump(mode='json')
        result = application_pdf.render_document('cv', doc)
        self.assertGreater(result.pages, 2)
        self.assertTrue(result.page_limit_exceeded)
        overflow = next(c for c in result.checks if c['code'] == 'overflow')
        self.assertEqual(overflow['status'], 'warning')
        self.assertGreater(len(result.telemetry['beyond_limit_units']), 0)

    def test_compact_print_avoids_an_extra_page_from_entry_breaks(self):
        doc = fictional_cv()
        doc.sections[1].entries = [
            Entry(id=f'work-{i}', kind='work',
                  locked=LockedEntry(company=f'Labor {i}', title='Projektingenieur', start='2020', end='2021'),
                  units=[
                      Unit(id=f'u-work-{i}-b{n}', text=f'Messreihe {n} im Labor {i}. '
                           + 'Kalibrierte Sensoren und erfasste auftretende Abweichungen im Prüfstand. ' * 3)
                      for n in range(count)
                  ])
            for i, count in enumerate((10, 8, 8, 6), 1)
        ]
        result = application_pdf.render_document('cv', doc.model_dump())
        self.assertEqual(result.pages, 2)
        self.assertFalse(result.page_limit_exceeded)
        self.assertEqual(self._passed(result, 'reading_order'), 'passed')

    def test_entry_intro_stays_with_first_bullet_at_page_break(self):
        from pypdf import PdfReader

        doc = fictional_cv(bullets=16)
        doc.sections[1].entries.append(
            Entry(id='work-2', kind='work',
                  locked=LockedEntry(company='Fiktivlabor', title='Praktikant', start='2020', end='2020'),
                  intro=[Unit(id='u-intro', text='Übersicht über Kalibrierung und thermische Messung. ' * 3)],
                  units=[Unit(id='u-bullet', text='Kalibrierte Sensoren im Prüfstand.')]),
        )
        result = application_pdf.render_document('cv', doc.model_dump())
        pages = [page.extract_text() for page in PdfReader(BytesIO(result.pdf)).pages]
        self.assertEqual(len(pages), 2)
        for part in ('FIKTIVLABOR', 'Übersicht über Kalibrierung', 'Kalibrierte Sensoren'):
            self.assertIn(part, pages[1])
        self.assertNotIn('u-bullet', result.telemetry['split_units'])

    def test_telemetry_reports_paragraph_that_spans_two_pdf_pages(self):
        doc = fictional_cv(bullets=14)
        doc.sections[1].entries.append(
            Entry(id='work-2', kind='work',
                  locked=LockedEntry(company='Fiktivlabor', title='Praktikant', start='2020', end='2020'),
                  intro=[Unit(id='u-long-intro', text='Übersicht über Kalibrierung und thermische Messung. ' * 21)]),
        )
        result = application_pdf.render_document('cv', doc.model_dump())
        self.assertEqual(result.pages, 2)
        self.assertIn('u-long-intro', result.telemetry['split_units'])

    def test_invalid_document_fails_cleanly(self):
        with self.assertRaises(application_pdf.RenderFailed) as ctx:
            application_pdf.render_document('cv', {'format': 'radius-cv-v2'})
        self.assertEqual(ctx.exception.code, 'invalid_document')


class AvailabilityTests(unittest.TestCase):
    def test_reading_order_accepts_line_wrap_at_an_explicit_compound_hyphen(self):
        doc = fictional_cv(bullets=1)
        doc.headline.text = 'Koordination der Speicher-Entwicklung'
        extracted = '\n'.join([
            'Koordination der Speicher-\nEntwicklung',
            doc.sections[0].units[0].text,
            doc.sections[1].entries[0].units[0].text,
            'Python • Messtechnik',
        ])
        checks = application_pdf._text_checks('cv', doc, extracted, 1)
        self.assertIn(('reading_order', 'passed'), [(c['code'], c['status']) for c in checks])

    def test_reading_order_check_rejects_extraction_with_moved_bullet(self):
        doc = fictional_cv(bullets=1)
        doc.sections[1].entries.append(
            Entry(id='work-2', kind='work',
                  locked=LockedEntry(company='Fiktivlabor', title='Praktikant',
                                     start='2020', end='2020'),
                  units=[Unit(id='u-work-2-b1', text='Kalibrierte Sensoren im Prüfstand.')]),
        )
        doc.sections[2].units[1].hidden = True
        extracted = '\n'.join([
            doc.headline.text,
            doc.sections[0].units[0].text,
            doc.sections[1].entries[1].units[0].text,
            doc.sections[1].entries[0].units[0].text,
            'Python',
        ])
        checks = application_pdf._text_checks('cv', doc, extracted, 1)
        self.assertIn(('reading_order', 'failed'), [(c['code'], c['status']) for c in checks])

    def test_missing_dist_is_unavailable(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {'RADIUS_FRONTEND_DIST': '/nonexistent/radius-dist'}):
            self.assertFalse(application_pdf.available())
            with self.assertRaises(application_pdf.RenderUnavailable):
                application_pdf.render_document('letter', fictional_letter().model_dump(mode='json'))


if __name__ == '__main__':
    unittest.main()
