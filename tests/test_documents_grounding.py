import unittest
from pathlib import Path

from hiring_scraper.documents import (
    CvDocument,
    LetterDocument,
    Sender,
    Unit,
    apply_patch,
    build_evidence,
    check_document,
    import_marked_tex,
)

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'documents'


def _base_profile() -> dict:
    return {
        'skills': ['Tableau', 'Power BI', 'SQL', 'Jira', 'Kanban', 'RFID', 'Six Sigma'],
        'language_levels': {'German': 'native', 'English': 'C1', 'Dutch': 'B1'},
        'experience_years': 5,
        'application_personalization': {
            'career_evidence': 'Optimized dock scheduling across 1,200 shipments with 35% faster turnaround.',
        },
        'application_details': {
            'preferences': {
                'salary_amount': '65000',
                'salary_currency': 'EUR',
                'salary_period': 'year',
                'notice_period': '3 months',
                'available_from': '2026-09-01',
                'authorized_to_work': False,
                'sponsorship_required': True,
            },
        },
    }


def _make_letter(language: str, paragraphs: list[Unit]) -> LetterDocument:
    return LetterDocument(
        language=language,  # type: ignore[arg-type]
        sender=Sender(name='Maren Lindner', lines=['Bremen, DE']),
        recipient_lines=['HarborTech AG'],
        place_date='Bremen, 2026-05-01',
        subject=Unit(id='u-subject', text='Bewerbung als Operations Analyst' if language == 'de' else 'Application for Operations Analyst'),
        salutation=Unit(id='u-salutation', text='Sehr geehrtes Team,' if language == 'de' else 'Dear Hiring Team,'),
        paragraphs=paragraphs,
        closing=Unit(id='u-closing', text='Mit freundlichen Grüßen' if language == 'de' else 'Kind regards,'),
        signature='Maren Lindner',
    )


class GroundingChecksTests(unittest.TestCase):
    def setUp(self):
        self.base_cv: CvDocument = import_marked_tex((FIXTURES / 'marked_de_cv.tex').read_text(encoding='utf-8'))
        self.profile = _base_profile()
        self.evidence = build_evidence(self.base_cv, self.profile)

    def test_source_and_user_units_are_never_flagged(self):
        doc = apply_patch(
            self.base_cv,
            'u-work-1-b1',
            '[TODO] Increased revenue by 999% with Kubernetes.',
            'user',
        )
        findings = check_document(doc, self.evidence, language='de', profile=self.profile)
        self.assertEqual(findings, [])

    def test_unknown_source_id_flagged(self):
        doc = apply_patch(
            self.base_cv,
            'u-work-1-b1',
            'Koordinierte Abläufe und standardisierte Prozesse im Lager.',
            'ai',
            sources=['cv:work-1:b99'],
        )
        findings = check_document(doc, self.evidence, language='de', profile=self.profile)
        self.assertTrue(any(f.code == 'review_grounding' and f.severity == 'high' and f.unit_id == 'u-work-1-b1' for f in findings))

    def test_number_normalization_accepts_format_variants_and_flags_hallucinations(self):
        # 35 % (with space) matches 35%; 1,200 matches 1.200; 2022 matches entry start year
        valid_doc = apply_patch(
            self.base_cv,
            'u-work-1-b1',
            'Reduzierte Durchlaufzeiten seit 2022 um 35 % bei 1,200 monatlichen Lieferungen.',
            'ai',
            sources=['cv:work-1:b1', 'cv:work-1:b2'],
        )
        findings = check_document(valid_doc, self.evidence, language='de', profile=self.profile)
        self.assertEqual([f for f in findings if f.code == 'unsupported_number'], [])

        # Fabricated 82% is flagged
        bad_doc = apply_patch(
            self.base_cv,
            'u-work-1-b1',
            'Reduzierte Durchlaufzeiten um 82% durch neue Prozesse.',
            'ai',
            sources=['cv:work-1:b1'],
        )
        bad_findings = check_document(bad_doc, self.evidence, language='de', profile=self.profile)
        self.assertTrue(any(f.code == 'unsupported_number' and f.severity == 'high' and '82%' in f.message for f in bad_findings))

    def test_technical_terms_allow_cited_and_skills_avoid_german_fps_and_downgrade_letter_roles(self):
        # German compounds + sentence-initial capitals + profile skills produce zero findings
        clean_cv = apply_patch(
            self.base_cv,
            'u-work-1-b1',
            'Entwickelte Qualitätsmanagement und Stakeholder-Koordination mit Tableau, SQL und KPI-Berichten.',
            'ai',
            sources=['cv:work-1:b1'],
        )
        self.assertEqual(check_document(clean_cv, self.evidence, language='de', profile=self.profile), [])

        # Unsupported tool / CamelCase / acronym in CV -> medium unsupported_term
        bad_cv = apply_patch(
            self.base_cv,
            'u-work-1-b1',
            'Entwickelte Dashboards in GraphQL und Kubernetes mit ERP-Anbindung.',
            'ai',
            sources=['cv:work-1:b1'],
        )
        cv_findings = [f for f in check_document(bad_cv, self.evidence, language='de', profile=self.profile) if f.code == 'unsupported_term']
        self.assertGreaterEqual(len(cv_findings), 3)
        self.assertTrue(all(f.severity == 'medium' for f in cv_findings))

        # Letter role='motivation' or 'bring' downgrades unsupported job-derived terms to 'info'
        letter = _make_letter(
            'en',
            [
                Unit(
                    id='u-p1',
                    text='I am excited to deepen my experience with Kubernetes and GraphQL in your team.',
                    role='motivation',
                    origin='ai',
                    sources=['job'],
                ),
                Unit(
                    id='u-p2',
                    text='I want to bring structured analysis and learn Snowflake workflows.',
                    role='bring',
                    origin='ai',
                    sources=['job'],
                ),
                Unit(
                    id='u-p3',
                    text='Previously I deployed Kubernetes clusters in production.',
                    role='experience',
                    origin='ai',
                    sources=['cv:work-1:b1'],
                ),
            ],
        )
        letter_findings = [
            f for f in check_document(letter, self.evidence, language='en', profile=self.profile, base_cv=self.base_cv)
            if f.code == 'unsupported_term'
        ]
        by_unit = {f.unit_id: f.severity for f in letter_findings}
        self.assertEqual(by_unit['u-p1'], 'info')
        self.assertEqual(by_unit['u-p2'], 'info')
        self.assertEqual(by_unit['u-p3'], 'medium')

    def test_employer_attribution_checks_cv_and_letter(self):
        # CV bullet under work-1 mentioning work-2's company or citing work-2
        wrong_company = apply_patch(
            self.base_cv,
            'u-work-1-b1',
            'Erstellte Auswertungen bei Hafenwerk Systems für die Logistik.',
            'ai',
            sources=['cv:work-1:b1'],
        )
        findings = check_document(wrong_company, self.evidence, language='de', profile=self.profile)
        self.assertTrue(any(f.code == 'misattributed' and f.severity == 'high' for f in findings))

        wrong_citation = apply_patch(
            self.base_cv,
            'u-work-1-b1',
            'Erstellte SQL-Auswertungen für Budgetberichte.',
            'ai',
            sources=['cv:work-2:b1'],
        )
        findings2 = check_document(wrong_citation, self.evidence, language='de', profile=self.profile)
        self.assertTrue(any(f.code == 'misattributed' and f.severity == 'high' for f in findings2))

        # Letter mentioning work-1 company without citing work-1 -> misattributed; with citation -> clean
        letter_bad = _make_letter(
            'de',
            [Unit(id='u-p1', text='Bei Nordwind Logistics GmbH verbesserte ich die Abläufe.', role='experience', origin='ai', sources=['cv:work-2:b1'])],
        )
        lf_bad = check_document(letter_bad, self.evidence, language='de', profile=self.profile, base_cv=self.base_cv)
        self.assertTrue(any(f.code == 'misattributed' for f in lf_bad))

        letter_ok = _make_letter(
            'de',
            [Unit(id='u-p1', text='Bei Nordwind Logistics GmbH verbesserte ich die Abläufe.', role='experience', origin='ai', sources=['cv:work-1:b1'])],
        )
        lf_ok = check_document(letter_ok, self.evidence, language='de', profile=self.profile, base_cv=self.base_cv)
        self.assertEqual([f for f in lf_ok if f.code == 'misattributed'], [])

    def test_sensitive_facts_languages_visa_salary_and_notice_period(self):
        # Matching sensitive facts -> clean
        ok_profile = {
            **self.profile,
            'application_details': {
                'preferences': {
                    **self.profile['application_details']['preferences'],
                    'authorized_to_work': True,
                    'sponsorship_required': False,
                },
            },
        }
        ok_ev = build_evidence(self.base_cv, ok_profile)
        ok_letter = _make_letter(
            'en',
            [
                Unit(
                    id='u-p1',
                    text='I speak English (C1) and German as a native speaker, hold an unrestricted work permit, have a 3 months notice period and expect 65,000 EUR.',
                    role='close',
                    origin='ai',
                    sources=['profile:language_levels', 'profile:notice_period', 'profile:salary'],
                ),
            ],
        )
        self.assertEqual(
            [f for f in check_document(ok_letter, ok_ev, language='en', profile=ok_profile) if f.code == 'sensitive_fact'],
            [],
        )

        # Conflicting sensitive facts -> flagged
        bad_letter = _make_letter(
            'en',
            [
                Unit(id='u-p1', text='I speak Dutch (C2) fluently.', role='bring', origin='ai', sources=['profile:language_levels']),
                Unit(id='u-p2', text='I hold an unrestricted work permit and need no visa sponsorship.', role='close', origin='ai', sources=['job']),
                Unit(id='u-p3', text='My salary expectation is 85,000 EUR per year.', role='close', origin='ai', sources=['profile:salary']),
                Unit(id='u-p4', text='My notice period is 2 weeks.', role='close', origin='ai', sources=['profile:notice_period']),
            ],
        )
        bad_findings = [
            f for f in check_document(bad_letter, self.evidence, language='en', profile=self.profile)
            if f.code == 'sensitive_fact'
        ]
        self.assertEqual({f.unit_id for f in bad_findings}, {'u-p1', 'u-p2', 'u-p3', 'u-p4'})
        self.assertTrue(all(f.severity == 'high' for f in bad_findings))

    def test_placeholders_language_mix_and_repetition(self):
        letter = _make_letter(
            'de',
            [
                Unit(id='u-p1', text='Ich bewerbe mich bei [Company] für die ausgeschriebene Stelle.', role='motivation', origin='ai', sources=['job']),
                Unit(
                    id='u-p2',
                    text='I developed and managed the weekly reporting dashboard across the entire operations department.',
                    role='experience',
                    origin='ai',
                    sources=['cv:work-1:b1'],
                ),
                Unit(
                    id='u-p3',
                    text='Koordinierte rund 1.200 Lieferungen pro Monat mit Disponenten, Lagerleitung und externen Dienstleistern.',
                    role='experience',
                    origin='ai',
                    sources=['cv:work-1:b2'],
                ),
                Unit(
                    id='u-p4',
                    text='Koordinierte rund 1.200 Lieferungen pro Monat mit Disponenten, Lagerleitung und externen Dienstleistern.',
                    role='bring',
                    origin='ai',
                    sources=['cv:work-1:b2'],
                ),
            ],
        )
        findings = check_document(letter, self.evidence, language='de', profile=self.profile, base_cv=self.base_cv)
        codes_by_unit = {(f.unit_id, f.code) for f in findings}
        self.assertIn(('u-p1', 'placeholder'), codes_by_unit)
        self.assertIn(('u-p2', 'language_mix'), codes_by_unit)
        self.assertIn(('u-p4', 'repetition'), codes_by_unit)

        # German sentence with normal business loanwords does NOT trigger language_mix
        loanword_letter = _make_letter(
            'de',
            [
                Unit(
                    id='u-p1',
                    text='Koordinierte Stakeholder, Onboarding und KPI-Reporting im Team für nachhaltige Verbesserungen.',
                    role='experience',
                    origin='ai',
                    sources=['cv:work-1:b1'],
                ),
            ],
        )
        lf = check_document(loanword_letter, self.evidence, language='de', profile=self.profile, base_cv=self.base_cv)
        self.assertEqual([f for f in lf if f.code == 'language_mix'], [])


if __name__ == '__main__':
    unittest.main()
