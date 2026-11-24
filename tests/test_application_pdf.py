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

    def test_review_metadata_never_appears_in_the_pdf(self):
        doc = fictional_cv()
        doc.headline.origin = 'ai'
        doc.headline.original_text = 'Superseded fictional wording'
        doc.headline.reviewed_text = doc.headline.text
        doc.headline.sources = ['cv:headline']
        doc.review_sources = {'cv:headline': 'Fictional evidence for review only'}
        result = application_pdf.render_document('cv', doc.model_dump())
        self.assertIn(doc.headline.text, result.text)
        self.assertNotIn(doc.headline.original_text, result.text)
        self.assertNotIn(doc.review_sources['cv:headline'], result.text)

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

    def test_legacy_cv_renders_portrait_when_available(self):
        body = fictional_cv().model_dump()
        body['photo'] = False
        self.assertTrue(application_pdf.render_document('cv', body, photo_png=PIXEL).telemetry['photo_used'])
        self.assertFalse(application_pdf.render_document('cv', body).telemetry['photo_used'])

    def test_qualification_heading_precedes_its_subheadings_in_pdf(self):
        doc = fictional_cv()
        doc.sections[2].group = 'WEITERE QUALIFIKATIONEN'
        doc.sections.append(Section(id='s-languages', title='Sprachen', kind='list',
                                    group='WEITERE QUALIFIKATIONEN',
                                    units=[Unit(id='u-language', text='Englisch – B2')]))
        result = application_pdf.render_document('cv', doc.model_dump())
        self.assertEqual(self._passed(result, 'reading_order'), 'passed')
        self.assertEqual(result.text.count('WEITERE QUALIFIKATIONEN'), 1)
        headings = ['WEITERE QUALIFIKATIONEN', doc.sections[2].title.upper(), 'Sprachen', 'Englisch']
        offsets = [result.text.index(heading) for heading in headings]
        self.assertEqual(offsets, sorted(offsets))

    def test_over_budget_pdf_is_rejected_instead_of_truncated(self):
        from unittest.mock import patch

        with patch.object(application_pdf, 'MAX_PDF_PAGES', 1):
            with self.assertRaises(application_pdf.RenderFailed) as error:
                application_pdf.render_document('cv', fictional_cv(bullets=20).model_dump(mode='json'))
        self.assertEqual(error.exception.code, 'document_too_large')

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

    def test_pdf_metadata_and_a4_dimensions_for_de_and_en(self):
        from pypdf import PdfReader

        en_cv = fictional_cv()
        en_cv.language = 'en'
        en_letter = fictional_letter()
        en_letter.language = 'en'
        for kind, doc, title in [
            ('cv', fictional_cv(), 'Jörg Übermaß – Lebenslauf'),
            ('letter', fictional_letter(), 'Jörg Übermaß – Anschreiben'),
            ('cv', en_cv, 'Jörg Übermaß – CV'),
            ('letter', en_letter, 'Jörg Übermaß – Cover letter'),
        ]:
            with self.subTest(kind=kind, language=doc.language):
                result = application_pdf.render_document(kind, doc.model_dump())
                reader = PdfReader(BytesIO(result.pdf))
                self.assertEqual(reader.metadata.title, title)
                self.assertEqual(reader.metadata.author, 'Jörg Übermaß')
                box = reader.pages[0].mediabox
                self.assertAlmostEqual(float(box.width), 595.0, delta=2.0)
                self.assertAlmostEqual(float(box.height), 842.0, delta=2.0)

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

    def test_short_education_section_keeps_its_two_entries_under_one_heading(self):
        from pypdf import PdfReader

        doc = fictional_cv(bullets=14)
        doc.sections.insert(2, Section(
            id='s-education', kind='education', title='Ausbildung',
            entries=[
                Entry(
                    id=f'study-{n}', kind='study',
                    locked=LockedEntry(company=f'Fiktivlabor {n}', title='Forschungsprojekt',
                                       start='2020', end='2020'),
                    units=[
                        Unit(id=f'u-study-{n}-b{i}', text=f'Auswertung {i}: '
                             + 'Kalibrierte Sensoren für den Prüfstand mit dem Werkstattteam. ' * 3)
                        for i in range(1, 4)
                    ],
                )
                for n in (1, 2)
            ],
        ))
        result = application_pdf.render_document('cv', doc.model_dump())
        pages = [page.extract_text() for page in PdfReader(BytesIO(result.pdf)).pages]
        self.assertEqual(len(pages), 2)
        self.assertTrue(pages[1].lstrip().startswith('AUSBILDUNG'))
        for company in ('FIKTIVLABOR 1', 'FIKTIVLABOR 2'):
            self.assertIn(company, pages[1])

    def test_long_entry_avoids_single_orphan_bullet_at_top_of_page_two(self):
        from pypdf import PdfReader

        doc = fictional_cv(bullets=13)
        doc.sections[1].entries.append(
            Entry(
                id='work-2', kind='work',
                locked=LockedEntry(company='Fiktivlabor', title='Projektingenieur', start='2019', end='2021'),
                units=[
                    Unit(id=f'u-w2-b{n}', text=f'Messreihe {n}: ' + 'Dokumentierte Prüfergebnisse im Labor. ' * 5)
                    for n in range(1, 7)
                ],
            )
        )
        result = application_pdf.render_document('cv', doc.model_dump())
        pages = [page.extract_text() for page in PdfReader(BytesIO(result.pdf)).pages]
        self.assertEqual(len(pages), 2)
        p1_items = [n for n in range(1, 7) if f'Messreihe {n}:' in pages[0]]
        p2_items = [n for n in range(1, 7) if f'Messreihe {n}:' in pages[1]]
        self.assertTrue(len(p1_items) == 0 or len(p1_items) >= 2)
        self.assertGreaterEqual(len(p2_items), 2)

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

    def test_duplicate_company_title_internship_renders_localized_role_label(self):
        for lang, expected in (('de', 'Praktikum'), ('en', 'Internship')):
            with self.subTest(language=lang):
                doc = fictional_cv(bullets=1)
                doc.language = lang
                doc.sections.append(Section(
                    id='s-edu', kind='education', title='Ausbildung' if lang == 'de' else 'Education',
                    entries=[
                        Entry(
                            id='study-2', kind='study', label='Praktikum' if lang == 'de' else 'Internship',
                            locked=LockedEntry(
                                company='Applied Craft Fellowship', title='Applied Craft Fellowship',
                                start='2019', end='2019',
                            ),
                            units=[Unit(id='u-st2-b1', text='Built wooden mockups for community booths.')],
                        ),
                    ],
                ))
                result = application_pdf.render_document('cv', doc.model_dump())
                self.assertIn(expected, result.text)
                self.assertEqual(result.text.upper().count('APPLIED CRAFT FELLOWSHIP'), 1)

    def test_four_bullet_entry_splits_to_fill_page_one(self):
        from pypdf import PdfReader

        doc = fictional_cv(bullets=13)
        doc.sections[1].entries.append(
            Entry(
                id='work-2', kind='work',
                locked=LockedEntry(company='Fiktivlabor', title='Projektingenieur', start='2019', end='2021'),
                units=[
                    Unit(id=f'u-w2-b{n}', text=f'Messreihe {n}: ' + 'Dokumentierte Prüfergebnisse im Labor. ' * 5)
                    for n in range(1, 5)
                ],
            ),
        )
        result = application_pdf.render_document('cv', doc.model_dump())
        pages = [page.extract_text() for page in PdfReader(BytesIO(result.pdf)).pages]
        self.assertEqual(len(pages), 2)
        p1_items = [n for n in range(1, 5) if f'Messreihe {n}:' in pages[0]]
        p2_items = [n for n in range(1, 5) if f'Messreihe {n}:' in pages[1]]
        self.assertEqual(p1_items, [1, 2])
        self.assertEqual(p2_items, [3, 4])
        self.assertIn('FIKTIVLABOR', pages[0])
        ratios = result.telemetry['page_fill_ratios']
        self.assertEqual(len(ratios), 2)
        self.assertGreaterEqual(ratios[0], 0.80)
        self.assertLessEqual(ratios[0], 1.0)

    def test_education_starts_on_page_one_when_underfilled(self):
        from pypdf import PdfReader

        doc = fictional_cv(bullets=12)
        doc.sections.insert(2, Section(
            id='s-education', kind='education', title='Ausbildung', entries=[
                Entry(
                    id='study-1', kind='study',
                    locked=LockedEntry(company='Beispielhochschule', title='M.Sc. Energiesysteme',
                                       location='Kassel', start='2018', end='2020'),
                    intro=[
                        Unit(id='u-study-i1', text='Schwerpunkt auf Batteriemodule und thermische Modellierung.'),
                        Unit(id='u-study-i2', text='Ausgewählte Projekte:'),
                    ],
                    units=[
                        Unit(id=f'u-study-b{n}', lead_in=f'Projekt {n}:',
                             text='Entwickelte einen Prüfstand und wertete Messdaten für Zellen aus. ' * 4)
                        for n in range(1, 4)
                    ],
                ),
            ],
        ))
        result = application_pdf.render_document('cv', doc.model_dump())
        pages = [page.extract_text() for page in PdfReader(BytesIO(result.pdf)).pages]
        self.assertEqual(len(pages), 2)
        self.assertIn('AUSBILDUNG', pages[0])
        self.assertIn('BEISPIELHOCHSCHULE', pages[0])
        self.assertIn('Schwerpunkt auf Batteriemodule', pages[0])
        self.assertGreaterEqual(result.telemetry['page_fill_ratios'][0], 0.80)
        self.assertEqual(self._passed(result, 'entry_pagination'), 'passed')

    def test_inline_skills_keep_items_unbroken_unless_longer_than_line(self):
        from pypdf import PdfReader

        items = [
            'Thermische Analyse & Modellierung',
            'Grafana-Messwert-Dashboards',
            'Laser Cutting / Rapid Prototyping',
            'Cross-Functional Coordination',
            'Stakeholder & Vendor Alignment',
            'Test Plan Prioritization',
            'Hardware Verification & Validation',
            'Workshop Facilitation & Coaching',
        ]
        doc = fictional_cv(bullets=1)
        doc.sections[2].units = [
            Unit(id=f'u-skill-{i}', text=text) for i, text in enumerate(items, 1)
        ]
        result = application_pdf.render_document('cv', doc.model_dump())
        lines = [
            ' '.join(line.split())
            for page in PdfReader(BytesIO(result.pdf)).pages
            for line in (page.extract_text() or '').splitlines()
            if line.strip()
        ]
        for item in items:
            self.assertTrue(
                any(item in line for line in lines),
                f'Inline skill broke across lines: {item!r} not intact in {lines!r}',
            )
        self.assertFalse(any(line.endswith('•') for line in lines))
        self.assertFalse(any(line.startswith('/') for line in lines))

        # An item longer than the line still wraps cleanly without failing verification.
        doc.sections[2].units.append(Unit(
            id='u-skill-long',
            text='Extremely Long Specialized Competency Description Spanning More Than One Full Printable A4 Line In Liberation Serif',
        ))
        long_result = application_pdf.render_document('cv', doc.model_dump())
        self.assertEqual(long_result.pages, 1)
        self.assertEqual(self._passed(long_result, 'reading_order'), 'passed')

    def test_long_institution_header_avoids_lone_short_tail_line(self):
        from pypdf import PdfReader

        doc = fictional_cv(bullets=1)
        doc.sections.insert(2, Section(
            id='s-education', kind='education', title='EDUCATION', entries=[
                Entry(
                    id='study-long', kind='study',
                    locked=LockedEntry(
                        company='Northern Polytechnic Institute of Applied Sciences and Engineering (NPIASE)',
                        title='B.S. Systems Engineering',
                        location='Northampton, UK',
                        start='2018', end='2022',
                    ),
                    units=[Unit(id='u-st-b1', text='Built bench rigs for sensor verification.')],
                ),
            ],
        ))
        result = application_pdf.render_document('cv', doc.model_dump())
        lines = [
            ' '.join(line.split())
            for page in PdfReader(BytesIO(result.pdf)).pages
            for line in (page.extract_text() or '').splitlines()
            if line.strip()
        ]
        self.assertFalse(any(line == '(NPIASE)' for line in lines), f'Short tail stranded: {lines!r}')
        self.assertTrue(any('Northampton, UK' in line for line in lines))

    def test_underfilled_page_keeps_first_education_project_with_header(self):
        from pypdf import PdfReader

        doc = fictional_cv(bullets=13)
        doc.sections.insert(2, Section(
            id='s-education', kind='education', title='EDUCATION', entries=[
                Entry(
                    id='study-1', kind='study',
                    locked=LockedEntry(company='Example Technical College', title='B.S. Mechanical Engineering',
                                       location='Bristol, UK', start='2018', end='2022'),
                    intro=[
                        Unit(id='u-study-i1', text='Focused on thermal storage and prototype fabrication.'),
                        Unit(id='u-study-i2', text='Selected Engineering Projects:'),
                    ],
                    units=[
                        Unit(id=f'u-study-b{n}', lead_in=f'Project Alpha {n}:',
                             text='Built a compact test rig, logged thermal stability readings, and validated component tolerances. ' * 4)
                        for n in range(1, 4)
                    ],
                ),
            ],
        ))
        result = application_pdf.render_document('cv', doc.model_dump())
        pages = [page.extract_text() or '' for page in PdfReader(BytesIO(result.pdf)).pages]
        self.assertEqual(len(pages), 2)
        p1_projects = [n for n in range(1, 4) if f'Project Alpha {n}:' in pages[0]]
        p2_projects = [n for n in range(1, 4) if f'Project Alpha {n}:' in pages[1]]
        self.assertEqual(p1_projects, [1])
        self.assertEqual(p2_projects, [2, 3])
        for marker in ('EDUCATION', 'EXAMPLE TECHNICAL COLLEGE', 'Focused on thermal storage',
                       'Selected Engineering Projects:'):
            self.assertIn(marker, pages[0])
        self.assertGreaterEqual(result.telemetry['page_fill_ratios'][0], 0.80)
        self.assertEqual(self._passed(result, 'entry_pagination'), 'passed')

    def test_education_entry_does_not_strand_header_and_degree_note_without_bullets_on_page_one(self):
        from pypdf import PdfReader

        doc = fictional_cv(bullets=15)
        doc.language = 'en'
        doc.sections[1].title = 'EXPERIENCE'
        doc.sections.insert(2, Section(
            id='s-education', kind='education', title='EDUCATION', entries=[
                Entry(
                    id='study-1', kind='study',
                    locked=LockedEntry(company='Example Polytechnic Institute', title='B.S. Industrial Design',
                                       location='Leeds, UK', start='2018', end='2022'),
                    intro=[
                        Unit(id='u-study-i1', text='Concentration in modular prototyping and product evaluation.'),
                        Unit(id='u-study-i2', text='Relevant Product Development Projects:'),
                    ],
                    units=[
                        Unit(id=f'u-study-b{n}', lead_in=f'Studio Project {n}:',
                             text='Built modular hardware prototypes, ran usability sessions, and documented assembly tolerances. ' * 4)
                        for n in range(1, 4)
                    ],
                ),
            ],
        ))
        result = application_pdf.render_document('cv', doc.model_dump())
        pages = [page.extract_text() or '' for page in PdfReader(BytesIO(result.pdf)).pages]
        self.assertEqual(len(pages), 2)
        self.assertFalse(result.page_limit_exceeded)
        self.assertEqual(self._passed(result, 'entry_pagination'), 'passed')
        self.assertEqual(result.telemetry['orphan_entry_breaks'], [])
        # Header, degree note, list-label intro, and all 3 projects stay together on page 2.
        for marker in (
            'EDUCATION',
            'EXAMPLE POLYTECHNIC INSTITUTE',
            'Concentration in modular prototyping',
            'Relevant Product Development Projects:',
            'Studio Project 1:',
            'Studio Project 2:',
            'Studio Project 3:',
        ):
            self.assertNotIn(marker, pages[0], f'Stranded {marker!r} on page 1')
            self.assertIn(marker, pages[1], f'Missing {marker!r} on page 2')

    def test_invalid_document_fails_cleanly(self):
        with self.assertRaises(application_pdf.RenderFailed) as ctx:
            application_pdf.render_document('cv', {'format': 'radius-cv-v2'})
        self.assertEqual(ctx.exception.code, 'invalid_document')

    def test_pdfium_word_boundaries_hyphenated_compounds_and_inline_skill_separators(self):
        from hiring_scraper.documents import cv_text

        doc = fictional_cv(bullets=2)
        doc.sections[0].units[0].text = (
            'Ingenieurin für Batteriespeicher mit Fokus auf Markt- und Wettbewerbsanalysen. '
            'Analysierte Messreihen und verifizierte CAD-Entwürfe für Prüfstände. '
            'Steuerte interdisziplinäre Entwicklungsteams in Serienprojekten.'
        )
        doc.sections[1].title = 'PROFESSIONAL EXPERIENCE'
        doc.sections[1].entries[0].locked.company = 'BEISPIELWERK GMBH'
        doc.sections[1].entries[0].units = [
            Unit(
                id='u-work-1-b1',
                text=(
                    'Erstellte detaillierte technische Spezifikationen und überprüfte komplexe '
                    'CAD-Entwürfe sowie 3D-CAD Modelle für den Prüfstand. '
                    'Auswertung der Messreihen und Abstimmung mit Partnern.'
                ),
            ),
            Unit(
                id='u-work-1-b2',
                text=(
                    'Führte umfassende Markt- und Wettbewerbsanalysen für modulare Speichersysteme '
                    'durch. Steuerung der Qualitätssicherung über alle Meilensteine.'
                ),
            ),
        ]
        skills = [
            'Python', 'Messtechnik', 'Prüfstandsautomatisierung', 'Anforderungsanalyse',
            'CAD-Konstruktion', 'Produktentwicklung', 'Qualitätssicherung', 'Datenauswertung',
            'Stakeholder-Kommunikation', 'Systemintegration', 'Versuchsplanung',
            'Technische Dokumentation', 'Kostenanalyse', 'Projektsteuerung',
        ]
        doc.sections[2] = Section(
            id='s-skills', kind='skills', title='ADDITIONAL QUALIFICATIONS', layout='inline',
            group='QUALIFICATIONS',
            units=[Unit(id=f'u-skill-{idx}', text=name) for idx, name in enumerate(skills, start=1)],
        )

        result = application_pdf.render_document('cv', doc.model_dump(mode='json'))
        pdfium_text = application_pdf._extract_pdfium_text(result.pdf)
        self.assertTrue(application_pdf.export_verified(result.checks), result.checks)
        self.assertEqual(result.telemetry['pdfium_control_chars'], 0)
        self.assertEqual(result.telemetry['pdfium_merged_tokens'], 0)
        self.assertTrue(result.telemetry['pdfium_text_parity'])
        self.assertIn('CAD-Entwürfe', pdfium_text)
        self.assertIn('3D-CAD', pdfium_text)
        self.assertIn('Markt- und', pdfium_text)
        self.assertIn('ADDITIONAL QUALIFICATIONS', pdfium_text)
        self.assertIn('BEISPIELWERK GMBH', pdfium_text)
        from pypdf import PdfReader
        import re

        for label, extracted in [('pypdf', result.text), ('pdfium', pdfium_text)]:
            lines = [line.strip() for line in extracted.splitlines() if line.strip()]
            self.assertFalse(
                any(line.startswith('•') or line.endswith('•') for line in lines),
                f'{label} had leading or trailing inline separator bullet: {lines}',
            )
            skills_section = extracted[extracted.index('ADDITIONAL QUALIFICATIONS'):]
            skill_lines = [line.strip() for line in skills_section.splitlines()[1:] if line.strip()]
            self.assertGreaterEqual(len(skill_lines), 2, f'{label} did not wrap inline skills: {skill_lines}')
            for left, right in zip(skills, skills[1:]):
                self.assertNotIn(f'{left}{right}', skills_section, f'{label} merged wrapped skills')

        raw_chunks: list[str] = []
        for page in PdfReader(BytesIO(result.pdf)).pages:
            page.extract_text(visitor_text=lambda text, _cm, _tm, _fd, _fs: raw_chunks.append(text))
        raw_stream = ''.join(chunk for chunk in raw_chunks if chunk not in ('\n', '\r'))
        raw_skills = raw_stream[raw_stream.index('ADDITIONAL QUALIFICATIONS'):]
        for left, right in zip(skills, skills[1:]):
            self.assertNotIn(f'{left}{right}', raw_skills, f'PDF text layer merged {left!r} and {right!r}')
            self.assertRegex(
                raw_skills,
                rf'{re.escape(left)}(?:[ \t]+•[ \t]+|[ \t]+){re.escape(right)}',
                f'PDF text layer lacked whitespace boundary between {left!r} and {right!r}: {raw_skills!r}',
            )

        plain = cv_text(doc)
        skills_plain = plain[plain.index('ADDITIONAL QUALIFICATIONS'):]
        positions = [skills_plain.index(name) for name in skills]
        self.assertEqual(positions, sorted(positions))
        for left, right in zip(skills, skills[1:]):
            self.assertNotIn(f'{left}{right}', skills_plain)
            self.assertIn(f'{left} • {right}', skills_plain)

    def test_pdfium_preserves_a_compound_at_the_wrap_boundary(self):
        doc = fictional_cv(bullets=1)
        # Position the hyphen at a line break with the unprotected print layout.
        compound = 'CAD-Entwürfe'
        doc.sections[1].entries[0].units[0].text = 'Messung ' * 10 + compound + ' in einer Testreihe.'
        output = application_pdf.render_document('cv', doc.model_dump())
        self.assertEqual(output.telemetry['pdfium_control_chars'], 0)
        self.assertIn(compound, output.text)
        self.assertIn(compound, application_pdf._extract_pdfium_text(output.pdf))


class PaginationCheckTests(unittest.TestCase):
    def test_checks_bullets_against_the_actual_header_page(self):
        for count, on_first, expected in [
            (3, 1, []), (3, 2, ['work-1']), (4, 0, ['work-1']), (4, 2, []), (4, 3, ['work-1']),
            (5, 1, []), (5, 2, []), (5, 3, []), (5, 5, []),
        ]:
            with self.subTest(count=count, on_first=on_first):
                doc = fictional_cv(bullets=count)
                entry = doc.sections[1].entries[0]
                bullets = [unit.text for unit in entry.units]
                pages = ['\n'.join([entry.locked.company, entry.locked.title, *bullets[:on_first]]),
                         '\n'.join(bullets[on_first:])]
                self.assertEqual(application_pdf._orphan_entry_breaks(doc, pages), expected)

    def test_intro_breaks_and_orphaned_section_headings(self):
        doc = fictional_cv(bullets=3)
        entry = doc.sections[1].entries[0]
        entry.intro = [
            Unit(id='u-i1', text='Degree overview in energy storage.'),
            Unit(id='u-i2', text='Selected projects:'),
        ]
        bullets = [unit.text for unit in entry.units]
        # Heading + header + intro on page 1 with 0 bullets while caption + bullets start page 2 -> flagged.
        header_only_pages = [
            '\n'.join(['BERUFSERFAHRUNG', entry.locked.company, entry.locked.title, entry.intro[0].text]),
            '\n'.join([entry.intro[1].text, *bullets]),
        ]
        self.assertEqual(application_pdf._orphan_entry_breaks(doc, header_only_pages), ['work-1'])
        # Whole entry (heading + header + intro + caption + bullets) moved together to page 2 -> clean.
        whole_entry_pages = [
            'Prior section content.',
            '\n'.join(['BERUFSERFAHRUNG', entry.locked.company, entry.locked.title,
                       entry.intro[0].text, entry.intro[1].text, *bullets]),
        ]
        self.assertEqual(application_pdf._orphan_entry_breaks(doc, whole_entry_pages), [])
        # Header + intro + caption + first bullet on page 1 and 2 remaining bullets on page 2 -> clean.
        clean_split_pages = [
            '\n'.join(['BERUFSERFAHRUNG', entry.locked.company, entry.locked.title,
                       entry.intro[0].text, entry.intro[1].text, bullets[0]]),
            '\n'.join(bullets[1:]),
        ]
        self.assertEqual(application_pdf._orphan_entry_breaks(doc, clean_split_pages), [])
        # Dangling colon caption on page 1 with 0 bullets on page 1 -> flagged.
        dangling_pages = [
            '\n'.join(['BERUFSERFAHRUNG', entry.locked.company, entry.locked.title,
                       entry.intro[0].text, entry.intro[1].text]),
            '\n'.join(bullets),
        ]
        self.assertEqual(application_pdf._orphan_entry_breaks(doc, dangling_pages), ['work-1'])
        # Section heading orphaned on page 1 while entry starts on page 2 -> flagged.
        orphan_heading = [
            'BERUFSERFAHRUNG',
            '\n'.join([entry.locked.company, entry.locked.title, entry.intro[0].text, entry.intro[1].text, *bullets]),
        ]
        self.assertEqual(application_pdf._orphan_entry_breaks(doc, orphan_heading), ['work-1'])

    def test_study_entry_can_start_with_one_project_before_a_page_break(self):
        for count, on_first, expected in [
            (3, 1, []),
            (3, 2, ['study-1']),
            (4, 1, []),
            (4, 2, []),
            (4, 3, ['study-1']),
        ]:
            with self.subTest(count=count, on_first=on_first):
                doc = fictional_cv(bullets=1)
                entry = Entry(
                    id='study-1', kind='study',
                    locked=LockedEntry(company='Beispielhochschule', title='B.Sc. Technik', start='2018', end='2022'),
                    units=[
                        Unit(id=f'u-st-{n}', lead_in=f'Projekt {n}:', text=f'Messaufbau {n} validiert.')
                        for n in range(1, count + 1)
                    ],
                )
                doc.sections.append(Section(id='s-edu', kind='education', title='AUSBILDUNG', entries=[entry]))
                rendered = [f'Projekt {n}: Messaufbau {n} validiert.' for n in range(1, count + 1)]
                pages = [
                    '\n'.join([entry.locked.company, entry.locked.title, *rendered[:on_first]]),
                    '\n'.join(rendered[on_first:]),
                ]
                self.assertEqual(application_pdf._orphan_entry_breaks(doc, pages), expected)

    def test_stylesheet_left_aligns_letter_and_protects_inline_items(self):
        from pathlib import Path
        import re

        css = (Path(__file__).resolve().parents[1] / 'frontend' / 'src' / 'documents' / 'classic-serif-v1.css').read_text(encoding='utf-8')
        letter_rule = re.search(r'\.doc-letter-paragraph\s*\{([^}]*)\}', css)
        self.assertIsNotNone(letter_rule)
        self.assertIn('text-align: left', letter_rule.group(1))
        self.assertNotIn('text-align: justify', letter_rule.group(1))
        inline_rule = re.search(r'\.doc-cv-inline-entry\s*\{([^}]*)\}', css)
        self.assertIsNotNone(inline_rule)
        self.assertIn('display: inline-block', inline_rule.group(1))
        self.assertIn('max-width: 100%', inline_rule.group(1))


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

class ExportVerificationTests(unittest.TestCase):
    def test_oversized_render_inputs_are_rejected_before_chromium(self):
        from unittest.mock import patch

        doc = fictional_letter().model_dump(mode='json')
        with patch.object(application_pdf, 'available', return_value=True), \
                patch.object(application_pdf, 'MAX_DOCUMENT_BYTES', 100), \
                patch.object(application_pdf._RENDERER, 'render', side_effect=AssertionError('Chromium launched')):
            with self.assertRaises(application_pdf.RenderFailed) as error:
                application_pdf.render_document('letter', doc)
        self.assertEqual(error.exception.code, 'document_too_large')

    def test_pdf_export_requires_integrity_checks_but_allows_pagination_warnings(self):
        checks = [
            {'code': 'text_extractable', 'status': 'passed'},
            {'code': 'glyphs', 'status': 'passed'},
            {'code': 'special_characters', 'status': 'passed'},
            {'code': 'name_present', 'status': 'passed'},
            {'code': 'contacts_present', 'status': 'passed'},
            {'code': 'entry_pagination', 'status': 'failed'},
            {'code': 'overflow', 'status': 'warning'},
        ]
        self.assertTrue(application_pdf.export_verified(checks))
        advisory = checks + [{'code': 'pdfium_word_boundaries', 'status': 'failed'},
                             {'code': 'pdfium_control_chars', 'status': 'failed'}]
        self.assertTrue(application_pdf.export_verified(advisory))
        self.assertFalse(application_pdf.export_verified(checks[1:]))
        for index in range(5):
            broken = [dict(check) for check in checks]
            broken[index]['status'] = 'failed'
            self.assertFalse(application_pdf.export_verified(broken))

    def test_pdfium_checks_flag_control_chars_and_merged_tokens(self):
        doc = fictional_cv(bullets=1)
        clean = '\n'.join([
            doc.headline.text,
            doc.sections[0].units[0].text,
            doc.sections[1].entries[0].units[0].text,
            'Python • Messtechnik',
        ])
        ok_checks, ok_metrics = application_pdf._pdfium_checks(doc, clean, clean)
        self.assertEqual([(c['code'], c['status']) for c in ok_checks], [
            ('pdfium_control_chars', 'passed'),
            ('pdfium_word_boundaries', 'passed'),
        ])
        self.assertEqual(ok_metrics, {
            'pdfium_control_chars': 0, 'pdfium_merged_tokens': 0, 'pdfium_text_parity': True,
        })

        with_ctrl = clean.replace('Prüfständen', 'Prüf\x02ständen')
        ctrl_checks, ctrl_metrics = application_pdf._pdfium_checks(doc, clean, with_ctrl)
        self.assertEqual(ctrl_metrics['pdfium_control_chars'], 1)
        self.assertIn(('pdfium_control_chars', 'failed'), [(c['code'], c['status']) for c in ctrl_checks])

        merged = clean.replace('Projektingenieur Energiespeicher', 'ProjektingenieurEnergiespeicher')
        merged_checks, merged_metrics = application_pdf._pdfium_checks(doc, clean, merged)
        self.assertEqual(merged_metrics['pdfium_merged_tokens'], 1)
        self.assertIn(('pdfium_word_boundaries', 'failed'), [(c['code'], c['status']) for c in merged_checks])

        base_passed = [
            {'code': 'text_extractable', 'status': 'passed'},
            {'code': 'glyphs', 'status': 'passed'},
            {'code': 'special_characters', 'status': 'passed'},
            {'code': 'name_present', 'status': 'passed'},
            {'code': 'contacts_present', 'status': 'passed'},
        ]
        self.assertTrue(application_pdf.export_verified(base_passed + ok_checks))
        # pdfium findings are advisory telemetry and never block an export.
        self.assertTrue(application_pdf.export_verified(base_passed + ctrl_checks))
        self.assertTrue(application_pdf.export_verified(base_passed + merged_checks))


if __name__ == '__main__':
    unittest.main()
