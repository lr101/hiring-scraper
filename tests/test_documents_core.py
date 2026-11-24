import ast
import unittest
from pathlib import Path

from pydantic import ValidationError

from hiring_scraper.documents import (
    Contact,
    CvDocument,
    Entry,
    Finding,
    Header,
    LetterDocument,
    LockedEntry,
    Section,
    Sender,
    Unit,
    apply_patch,
    build_evidence,
    cv_text,
    letter_text,
    skill_slug,
    unit_by_id,
    walk_units,
)


class DocumentTextValidationTests(unittest.TestCase):
    def test_rejects_characters_postgres_jsonb_cannot_store(self):
        for character in ('\x00', '\ud800'):
            with self.subTest(character=repr(character)), self.assertRaises(ValidationError):
                Unit(id='u-first', text='Fictional draft' + character)


def _sample_cv() -> CvDocument:
    return CvDocument(
        language='en',
        page='a4',
        photo=False,
        header=Header(
            name='Maren Lindner',
            contacts=[
                Contact(kind='phone', text='+49 151 00001234'),
                Contact(kind='location', text='Bremen, DE'),
                Contact(kind='email', text='maren@example.test', href='mailto:maren@example.test'),
            ],
        ),
        headline=Unit(id='u-headline', text='Operations & Supply Chain Analyst'),
        sections=[
            Section(
                id='s-profile',
                kind='profile',
                title='',
                layout='paragraphs',
                units=[
                    Unit(id='u-profile-1', text='Operations specialist focused on logistics analytics.'),
                    Unit(id='u-profile-2', text='Hidden draft summary.', hidden=True),
                ],
            ),
            Section(
                id='s-experience',
                kind='experience',
                title='EXPERIENCE',
                layout='bullets',
                entries=[
                    Entry(
                        id='work-1',
                        kind='work',
                        locked=LockedEntry(
                            company='Nordwind Logistics GmbH',
                            title='Operations Coordinator',
                            location='Bremen, DE',
                            start='2022',
                            end=None,
                            current=True,
                        ),
                        units=[
                            Unit(id='u-work-1-b1', text='Cut lead times by 35% using Tableau KPI reports.'),
                            Unit(id='u-work-1-b2', text='Hidden internal note.', hidden=True),
                        ],
                    ),
                ],
            ),
            Section(
                id='s-education',
                kind='education',
                title='EDUCATION',
                layout='bullets',
                entries=[
                    Entry(
                        id='study-1',
                        kind='study',
                        label='UNIVERSITY',
                        locked=LockedEntry(
                            company='Nordsee University',
                            title='B.Sc. Industrial Engineering',
                            location='Bremen, DE',
                            start='2018',
                            end='2022',
                            current=False,
                        ),
                        intro=[Unit(id='u-study-1-i1', text='Focus on logistics and quality systems.')],
                        units=[
                            Unit(
                                id='u-study-1-b1',
                                lead_in='Capstone:',
                                text='Redesigned warehouse picking flow for 18% shorter routes.',
                            ),
                        ],
                    ),
                ],
            ),
            Section(
                id='s-skills',
                kind='skills',
                title='TOOLS',
                layout='inline',
                units=[
                    Unit(id='u-skills-1', text='Tableau'),
                    Unit(id='u-skills-2', text='SQL'),
                    Unit(id='u-skills-3', text='Hidden Tool', hidden=True),
                ],
            ),
        ],
    )


def _sample_letter() -> LetterDocument:
    return LetterDocument(
        language='en',
        page='a4',
        sender=Sender(name='Maren Lindner', lines=['Bremen, DE', 'maren@example.test']),
        recipient_lines=['HarborTech AG', 'Hiring Team', 'Hamburg'],
        place_date='Bremen, 2026-05-01',
        subject=Unit(id='u-subject', text='Application for Operations Analyst'),
        salutation=Unit(id='u-salutation', text='Dear Hiring Team,'),
        paragraphs=[
            Unit(id='u-p1', text='I am applying for the Operations Analyst role.', role='motivation', origin='ai', sources=['job']),
            Unit(id='u-p2', text='Hidden draft paragraph.', role='experience', origin='ai', hidden=True),
            Unit(id='u-p3', text='At Nordwind Logistics I reduced lead times by 35%.', role='experience', origin='ai', sources=['cv:work-1:b1']),
        ],
        closing=Unit(id='u-closing', text='Kind regards,'),
        signature='Maren Lindner',
    )


class PackagePurityTests(unittest.TestCase):
    def test_documents_package_has_no_sqlalchemy_fastapi_or_app_imports(self):
        pkg_dir = Path(__file__).resolve().parents[1] / 'hiring_scraper' / 'documents'
        forbidden = ('sqlalchemy', 'fastapi', 'starlette', 'hiring_scraper.app')
        py_files = sorted(pkg_dir.glob('*.py'))
        self.assertGreaterEqual(len(py_files), 6)
        for path in py_files:
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for bad in forbidden:
                            self.assertFalse(
                                alias.name == bad or alias.name.startswith(bad + '.'),
                                f'{path.name} imports forbidden module {alias.name}',
                            )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    for bad in forbidden:
                        self.assertFalse(
                            node.module == bad or node.module.startswith(bad + '.'),
                            f'{path.name} imports from forbidden module {node.module}',
                        )


class SchemaTests(unittest.TestCase):
    def test_strict_models_reject_coercion_and_extra_fields(self):
        with self.assertRaises(ValidationError):
            Unit(id='u-1', text='ok', unknown_field=1)  # type: ignore[call-arg]
        with self.assertRaises(ValidationError):
            Unit(id='u-1', text=123)  # type: ignore[arg-type]
        with self.assertRaises(ValidationError):
            Unit(id='u-1', text='ok', hidden='false')  # type: ignore[arg-type]
        with self.assertRaises(ValidationError):
            Unit(id='U_BAD', text='ok')
        with self.assertRaises(ValidationError):
            Unit(id='u-1', text='line1\nline2')
        with self.assertRaises(ValidationError):
            Unit(id='u-1', text='x' * 1201)

    def test_section_limits_and_entries_constraints(self):
        too_many = [Unit(id=f'u-item-{i}', text=f'Item {i}') for i in range(61)]
        with self.assertRaises(ValidationError):
            Section(id='s-list', kind='list', title='List', units=too_many)

        entry = Entry(
            id='work-1',
            kind='work',
            locked=LockedEntry(company='Acme', title='Analyst', start='2022', end='2023', current=False),
        )
        with self.assertRaises(ValidationError):
            Section(id='s-skills', kind='skills', title='Skills', entries=[entry])

    def test_locked_entry_date_validation(self):
        with self.assertRaises(ValidationError):
            LockedEntry(company='Acme', title='Analyst', start='2022', end='2023', current=True)
        with self.assertRaises(ValidationError):
            LockedEntry(company='Acme', title='Analyst', start='2022', end=None, current=False)
        with self.assertRaises(ValidationError):
            LockedEntry(company='Acme', title='Analyst', start='2024', end='2022', current=False)
        with self.assertRaises(ValidationError):
            LockedEntry(company='Acme', title='Analyst', start='2024-13', end=None, current=True)
        with self.assertRaises(ValidationError):
            LockedEntry(company='Acme', title='Analyst', start='2024', end='', current=True)

    def test_duplicate_unit_ids_rejected(self):
        cv = _sample_cv()
        data = cv.model_dump()
        data['sections'][0]['units'][0]['id'] = 'u-headline'
        with self.assertRaises(ValidationError):
            CvDocument.model_validate(data)

        letter = _sample_letter()
        ldata = letter.model_dump()
        ldata['closing']['id'] = 'u-subject'
        with self.assertRaises(ValidationError):
            LetterDocument.model_validate(ldata)

    def test_finding_contract(self):
        finding = Finding(
            id='f-1',
            document='cv',
            unit_id=None,
            code='overflow',
            severity='medium',
            message='Page 1 overflows by 14px.',
        )
        self.assertEqual(finding.suggestion, '')
        with self.assertRaises(ValidationError):
            Finding(
                id='bad-1',
                document='cv',
                unit_id='u-1',
                code='overflow',
                severity='medium',
                message='Bad id prefix',
            )

    def test_walk_units_unit_by_id_and_apply_patch(self):
        cv = _sample_cv()
        ids = [u.id for u in walk_units(cv)]
        self.assertEqual(
            ids,
            [
                'u-headline',
                'u-profile-1',
                'u-profile-2',
                'u-work-1-b1',
                'u-work-1-b2',
                'u-study-1-i1',
                'u-study-1-b1',
                'u-skills-1',
                'u-skills-2',
                'u-skills-3',
            ],
        )
        self.assertEqual(unit_by_id(cv, 'u-study-1-b1').lead_in, 'Capstone:')
        self.assertIsNone(unit_by_id(cv, 'u-nonexistent'))

        patched = apply_patch(cv, 'u-work-1-b1', 'Updated bullet text.', 'user')
        self.assertIsNot(patched, cv)
        self.assertEqual(unit_by_id(cv, 'u-work-1-b1').text, 'Cut lead times by 35% using Tableau KPI reports.')
        self.assertEqual(unit_by_id(cv, 'u-work-1-b1').origin, 'source')
        self.assertEqual(unit_by_id(patched, 'u-work-1-b1').text, 'Updated bullet text.')
        self.assertEqual(unit_by_id(patched, 'u-work-1-b1').origin, 'user')

        with self.assertRaises(KeyError):
            apply_patch(cv, 'u-missing', 'New text', 'user')
        with self.assertRaises(ValidationError):
            apply_patch(cv, 'u-work-1-b1', 'bad\nnewline', 'user')

        letter = _sample_letter()
        patched_letter = apply_patch(letter, 'u-p1', 'Tailored motivation paragraph.', 'ai', sources=['job'])
        self.assertEqual(unit_by_id(patched_letter, 'u-p1').text, 'Tailored motivation paragraph.')
        self.assertEqual(unit_by_id(letter, 'u-p1').text, 'I am applying for the Operations Analyst role.')


class TextProjectionTests(unittest.TestCase):
    def test_cv_text_skips_hidden_and_keeps_reading_order(self):
        rendered = cv_text(_sample_cv())
        self.assertIn('Maren Lindner', rendered)
        self.assertIn('Operations & Supply Chain Analyst', rendered)
        self.assertIn('+49 151 00001234 | Bremen, DE | maren@example.test', rendered)
        self.assertIn('Nordwind Logistics GmbH — Bremen, DE', rendered)
        self.assertIn('Operations Coordinator (2022–present)', rendered)
        self.assertIn('- Capstone: Redesigned warehouse picking flow for 18% shorter routes.', rendered)
        self.assertIn('Tableau • SQL', rendered)
        self.assertNotIn('Hidden draft summary', rendered)
        self.assertNotIn('Hidden internal note', rendered)
        self.assertNotIn('Hidden Tool', rendered)
        self.assertLess(rendered.index('EXPERIENCE'), rendered.index('EDUCATION'))
        self.assertLess(rendered.index('EDUCATION'), rendered.index('TOOLS'))

    def test_letter_text_skips_hidden_paragraphs(self):
        rendered = letter_text(_sample_letter())
        self.assertIn('HarborTech AG', rendered)
        self.assertIn('Application for Operations Analyst', rendered)
        self.assertIn('At Nordwind Logistics I reduced lead times by 35%.', rendered)
        self.assertNotIn('Hidden draft paragraph', rendered)
        self.assertIn('Kind regards,\nMaren Lindner', rendered)


class EvidenceTests(unittest.TestCase):
    def test_build_evidence_follows_documented_id_scheme(self):
        cv = _sample_cv()
        profile = {
            'skills': ['Power BI', 'C++', 'C#', '.NET', 'SQL'],
            'skill_evidence': {
                'SQL': {'context': 'professional', 'note': 'Daily logistics reporting queries'},
            },
            'application_personalization': {
                'career_evidence': '- Built weekly warehouse throughput model\n\n- Trained 6 dispatchers on SOPs\n',
            },
            'summary': 'Process-driven operations specialist.',
            'education': [{'level': 'bachelor', 'field': 'Industrial Engineering', 'note': 'Honors'}],
            'certifications': ['Six Sigma Green Belt', 'ISO 9001 Auditor'],
            'language_levels': {'German': 'native', 'English': 'C1'},
            'experience_years': 4.0,
        }
        ev = build_evidence(cv, profile)

        self.assertEqual(ev['cv:profile:p1'], 'Operations specialist focused on logistics analytics.')
        self.assertNotIn('cv:profile:p2', ev)  # hidden unit skipped
        self.assertEqual(ev['cv:work-1:b1'], 'Cut lead times by 35% using Tableau KPI reports.')
        self.assertNotIn('cv:work-1:b2', ev)
        self.assertEqual(ev['cv:study-1:i1'], 'Focus on logistics and quality systems.')
        self.assertEqual(ev['cv:study-1:b1'], 'Capstone: Redesigned warehouse picking flow for 18% shorter routes.')
        self.assertEqual(ev['cv:s-skills:u1'], 'Tableau')
        self.assertEqual(ev['cv:s-skills:u2'], 'SQL')
        self.assertNotIn('cv:s-skills:u3', ev)

        self.assertEqual(ev['skill:power-bi'], 'Power BI')
        self.assertEqual(ev['skill:cpp'], 'C++')
        self.assertEqual(ev['skill:csharp'], 'C#')
        self.assertEqual(ev['skill:dotnet'], '.NET')
        self.assertEqual(ev['skill:sql'], 'SQL (professional): Daily logistics reporting queries')

        self.assertEqual(ev['evidence:1'], 'Built weekly warehouse throughput model')
        self.assertEqual(ev['evidence:2'], 'Trained 6 dispatchers on SOPs')

        self.assertEqual(ev['profile:summary'], 'Process-driven operations specialist.')
        self.assertEqual(ev['profile:education'], 'bachelor Industrial Engineering (Honors)')
        self.assertEqual(ev['profile:certifications'], 'Six Sigma Green Belt, ISO 9001 Auditor')
        self.assertEqual(ev['profile:language_levels'], 'German: native, English: C1')
        self.assertEqual(ev['profile:experience_years'], '4 years')
        self.assertEqual(skill_slug('Qualitätsmanagement'), 'qualitaetsmanagement')


if __name__ == '__main__':
    unittest.main()
