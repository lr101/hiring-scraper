"""Eligibility gates: hard hides only for explicit, cited incompatibilities (via matcher v3)."""
from __future__ import annotations

import unittest

from hiring_scraper.matching_v3 import match_job_v3

# Gates do not depend on model output; every case uses one fixed, moderately
# supported analysis so the full (non-pending) result shape is exercised.
_ANALYSIS = {
    'occupation': 'product management', 'industry': 'software',
    'duties': [{'line': 1, 'text': 'Own the product roadmap and backlog'}],
    'requirements': [{'line': 2, 'text': 'Experience as a Product Owner', 'need': 'must', 'kind': 'experience'}],
}
_EVIDENCE = {
    'items': [{'ref': 'd0', 'support': 0.8, 'quote': 'Owned product backlog', 'source': 'summary'},
              {'ref': 'r0', 'support': 0.8, 'quote': 'Product Owner with 5 years', 'source': 'summary'}],
    'role': {'support': 0.9, 'role': 'Product Owner'},
    'domain': {'support': 0.8, 'quote': 'Product Owner', 'source': 'summary', 'field': 'product management'},
}


def match_job_v2(job: dict, profile: dict, enrichment: dict | None = None) -> dict:
    return match_job_v3(job, profile, enrichment, analysis=_ANALYSIS, evidence=_EVIDENCE)
from hiring_scraper.gates import (
    explicit_onsite_evidence,
    has_sponsorship_alternative,
    normalize_jurisdictions,
)


CONSUMER_KEYS = {
    'score',
    'eligible',
    'uncertain',
    'fit_tier',
    'label',
    'reasons',
    'unknowns',
    'conflicts',
    'soft_conflicts',
    'requirement_gaps',
    'matched_skills',
    'missing_skills',
    'gates',
    'task_fit',
    'qualification_fit',
    'mandatory_review',
    'language_review',
    'duty_evidence',
    'semantic_match',
    'static_rank_basis',
    'explanation',
    'calibration',
}

LEDGER_ENTRY_KEYS = {
    'signal',
    'contribution',
    'detail',
    'job_quote',
    'profile_source',
    'profile_quote',
}


def _base_profile(**overrides) -> dict:
    profile = {
        'desired_roles': ['Product Owner', 'Product Manager'],
        'secondary_roles': ['Project Manager'],
        'skills': ['Product strategy', 'Agile', 'SQL', 'Stakeholder coordination', 'Jira'],
        'languages': ['English', 'German'],
        'language_levels': {'English': 'C1', 'German': 'B2'},
        'experience_years': 5,
        'seniority': 'Mid-level',
        'summary': (
            'Product Owner with 5 years of experience defining product roadmaps, prioritizing '
            'backlogs, writing user stories, running sprint planning, and coordinating '
            'cross-functional stakeholders using SQL and Agile methods.'
        ),
        'education': [
            {
                'level': 'master',
                'field': 'Business Informatics',
                'completed': True,
            }
        ],
        'work_styles': ['remote', 'hybrid'],
        'employment_types': ['full-time'],
        'excluded_terms': ['Cold Calling'],
        'application_preferences': {
            'willing_to_work_onsite': False,
            'authorized_to_work': True,
            'sponsorship_required': False,
            'authorization_country': 'Germany',
        },
        'experience_entries': [
            {
                'title': 'Product Owner',
                'summary': 'Owned product backlog, roadmap prioritization, and stakeholder alignment.',
            }
        ],
    }
    profile.update(overrides)
    return profile


def _strong_po_job(**overrides) -> dict:
    job = {
        'title': 'Product Owner (m/w/d)',
        'location_text': 'Berlin, Germany',
        'work_arrangement': 'remote',
        'is_remote': True,
        'employment_type': 'full-time',
        'description': (
            'Your Responsibilities:\n'
            '- Own the product roadmap and product backlog for our core platform.\n'
            '- Write user stories, lead sprint planning, and prioritize features.\n'
            '- Coordinate cross-functional engineering and business stakeholders.\n'
            '- Analyze KPIs using SQL.\n\n'
            'Your Profile:\n'
            '- 3+ years of experience as a Product Owner or Product Manager.\n'
            '- Strong experience with Agile methods, product strategy, and SQL.\n'
            '- English (B2) required.\n'
        ),
    }
    job.update(overrides)
    return job


class TestGatesContractAndLedger(unittest.TestCase):


    def test_fit_tier_respects_unresolved_hard_review_flags(self) -> None:
        job = _strong_po_job()
        job['description'] = job['description'].replace(
            '- 3+ years of experience as a Product Owner or Product Manager.\n', '',
        )
        clean_res = match_job_v2(job, _base_profile())
        self.assertTrue(clean_res['eligible'])
        self.assertGreaterEqual(clean_res['score'], 65)
        self.assertEqual(clean_res['fit_tier'], 'recommended')

        # Adding an unresolved qualitative language review flag prevents 'recommended'
        # even while keeping score >= 65 and eligible=True.
        flagged_job = _strong_po_job(
            description=job['description'] + '\n- Business fluent German required.\n'
        )
        flagged_res = match_job_v2(flagged_job, _base_profile())
        self.assertTrue(flagged_res['eligible'])
        self.assertTrue(flagged_res['language_review'])
        self.assertGreaterEqual(flagged_res['score'], 65)
        self.assertEqual(flagged_res['fit_tier'], 'possible')


class TestGatesHardGates(unittest.TestCase):
    def _assert_cited_hard_fail(self, res: dict, gate_name: str) -> None:
        self.assertFalse(res['eligible'], f'Expected hard fail on gate={gate_name}')
        self.assertEqual(res['gates'][gate_name]['status'], 'fail')
        self.assertEqual(res['fit_tier'], 'unlikely')
        self.assertTrue(res['conflicts'])
        failed_conds = [
            c for c in res['prerequisite_conditions']
            if c['status'] == 'fail' and c['gate'] == gate_name
        ]
        self.assertTrue(failed_conds, f'Missing prerequisite_conditions fail entry for {gate_name}')
        for cond in failed_conds:
            self.assertTrue(cond['job_quote'].strip(), 'Hard fail must cite job_quote')
            self.assertTrue(cond['profile_source'].strip(), 'Hard fail must cite profile_source')
            self.assertTrue(cond['profile_quote'].strip(), 'Hard fail must cite profile_quote')

    def test_hard_gate_cefr_minimum_and_localized_profile_keys(self) -> None:
        profile = _base_profile(language_levels={'Deutsch': 'B2', 'Englisch': 'C1'})
        job = _strong_po_job(
            description=_strong_po_job()['description'] + '\n- Sehr gute Deutschkenntnisse (mindestens C1) erforderlich.\n'
        )
        res = match_job_v2(job, profile)
        self._assert_cited_hard_fail(res, 'language')

    def test_hard_gate_shared_parenthetical_cefr_floor(self) -> None:
        profile = _base_profile(language_levels={'German': 'B2', 'English': 'C1'})
        job = _strong_po_job(
            description='Requirements:\n- Deutsch und Englisch (C1) zwingend erforderlich.\n'
        )
        res = match_job_v2(job, profile)
        self._assert_cited_hard_fail(res, 'language')

    def test_hard_gate_mandatory_native_and_native_level(self) -> None:
        profile_c1 = _base_profile(language_levels={'German': 'C1', 'English': 'C1'})
        job_native_level = _strong_po_job(
            description='Requirements:\n- Native-level German required.\n'
        )
        self._assert_cited_hard_fail(match_job_v2(job_native_level, profile_c1), 'language')

        profile_c2 = _base_profile(language_levels={'German': 'C2', 'English': 'C1'})
        job_muttersprache = _strong_po_job(
            description='Anforderungen:\n- Deutsch als Muttersprache zwingend erforderlich.\n'
        )
        self._assert_cited_hard_fail(match_job_v2(job_muttersprache, profile_c2), 'language')

    def test_hard_gate_excluded_title_term(self) -> None:
        profile = _base_profile(excluded_terms=['Cold Calling', 'Praktikum'])
        job = _strong_po_job(title='Praktikum Product Management (m/w/d)')
        res = match_job_v2(job, profile)
        self._assert_cited_hard_fail(res, 'excluded_terms')

    def test_hard_gate_explicit_onsite_text(self) -> None:
        profile = _base_profile(
            application_preferences={
                'willing_to_work_onsite': False,
                'authorized_to_work': True,
                'sponsorship_required': False,
                'authorization_country': 'Germany',
            }
        )
        for text in (
            'This is a full-time on-site role at our Berlin office; no remote work possible.',
            '100% Präsenzpflicht vor Ort am Standort München.',
            'You must work in the office 4 days per week.',
        ):
            self.assertIsNotNone(explicit_onsite_evidence(text), text)
            res = match_job_v2(_strong_po_job(description=text), profile)
            self._assert_cited_hard_fail(res, 'location')

    def test_hard_gate_denied_work_authorization_bound_to_jurisdiction(self) -> None:
        for country_label, permit_sentence, expected_code in (
            ('Switzerland', 'Eine gültige schweizerische Arbeitsbewilligung ist zwingend erforderlich.', 'CH'),
            ('United Kingdom', 'Existing British right to work in the UK is strictly required.', 'GB'),
            ('Germany', 'Valid work authorization for Germany is mandatory.', 'DE'),
            ('Austria', 'Gültige Arbeitserlaubnis für Österreich erforderlich.', 'AT'),
            ('EU', 'Valid work permit for the European Union is required.', 'EU'),
        ):
            self.assertIn(expected_code, normalize_jurisdictions(country_label))
            profile = _base_profile(
                application_preferences={
                    'willing_to_work_onsite': True,
                    'authorized_to_work': False,
                    'sponsorship_required': True,
                    'authorization_country': country_label,
                }
            )
            job = _strong_po_job(
                description=_strong_po_job()['description'] + f'\n- {permit_sentence}\n'
            )
            res = match_job_v2(job, profile)
            self._assert_cited_hard_fail(res, 'eligibility')


class TestGatesMustNotHideCases(unittest.TestCase):
    def test_qualitative_language_wording_flags_only_never_hides(self) -> None:
        profile = _base_profile(language_levels={'German': 'B1', 'English': 'B2'})
        for phrase in (
            'Fluent German required.',
            'Verhandlungssichere Deutschkenntnisse erforderlich.',
            'Sehr gute Deutschkenntnisse in Wort und Schrift.',
            'Business fluent German and English.',
            'Fluent or native English and German.',
            'Verhandlungssicher oder muttersprachlich in Deutsch.',
        ):
            job = _strong_po_job(description=f'Requirements:\n- {phrase}\n')
            res = match_job_v2(job, profile)
            self.assertTrue(res['eligible'], f'Must NOT hard-hide on qualitative phrase: {phrase}')
            self.assertNotEqual(res['gates']['language']['status'], 'fail', phrase)

    def test_language_or_alternatives_and_german_or_levels_not_bound_to_english(self) -> None:
        # Per-language OR alternative: English C1 satisfies "German (C1) or English (C1)".
        profile = _base_profile(language_levels={'German': 'A2', 'English': 'C1'})
        job_or = _strong_po_job(
            description='Requirements:\n- German (C1) or English (C1) required.\n'
        )
        res_or = match_job_v2(job_or, profile)
        self.assertTrue(res_or['eligible'])
        self.assertEqual(res_or['gates']['language']['status'], 'pass')

        # German OR-level "(C1 oder C2)" must NOT bind to English "(B2)".
        profile2 = _base_profile(language_levels={'German': 'C1', 'English': 'B2'})
        job_scoped = _strong_po_job(
            description='Anforderungen:\n- Deutsch (C1 oder C2) und Englisch (B2) erforderlich.\n'
        )
        res_scoped = match_job_v2(job_scoped, profile2)
        self.assertTrue(res_scoped['eligible'])
        self.assertEqual(res_scoped['gates']['language']['status'], 'pass')

    def test_non_language_c1_driving_license_and_german_law_not_hidden(self) -> None:
        profile = _base_profile(language_levels={'German': 'A1', 'English': 'C1'})
        job = _strong_po_job(
            description=(
                'Requirements:\n'
                '- Führerschein Klasse C1 und Staplerschein erforderlich.\n'
                '- Knowledge of German labour law and programming languages.\n'
            )
        )
        res = match_job_v2(job, profile)
        self.assertTrue(res['eligible'])
        self.assertEqual(res['gates']['language']['status'], 'pass')

    def test_metadata_only_onsite_flags_only_never_hides(self) -> None:
        profile = _base_profile(
            application_preferences={
                'willing_to_work_onsite': False,
                'authorized_to_work': True,
                'sponsorship_required': False,
                'authorization_country': 'Germany',
            }
        )
        job = _strong_po_job(
            work_arrangement='onsite',
            is_remote=False,
            description=(
                'Responsibilities:\n'
                '- Own the product roadmap and prioritize backlog items.\n'
                'Requirements:\n'
                '- 3+ years of Product Owner experience.\n'
            ),
        )
        res = match_job_v2(job, profile)
        self.assertTrue(res['eligible'], 'Metadata-only onsite must flag, never hard-hide')
        self.assertEqual(res['gates']['location']['status'], 'unknown')

    def test_sponsorship_alternative_and_other_jurisdiction_permit_never_hide(self) -> None:
        profile = _base_profile(
            application_preferences={
                'willing_to_work_onsite': True,
                'authorized_to_work': False,
                'sponsorship_required': True,
                'authorization_country': 'Germany',
            }
        )
        spons_text = 'Valid work permit for Germany required or we sponsor your work visa.'
        self.assertTrue(has_sponsorship_alternative(spons_text))
        res_spons = match_job_v2(_strong_po_job(description=spons_text), profile)
        self.assertTrue(res_spons['eligible'], 'Sponsorship alternative must produce unknown, not fail')
        self.assertEqual(res_spons['gates']['eligibility']['status'], 'unknown')

        # Permit bound to Switzerland while profile denies Germany only -> unknown, not fail.
        other_jur_job = _strong_po_job(
            description='Requirements:\n- Valid work permit for Switzerland is required.\n'
        )
        res_other = match_job_v2(other_jur_job, profile)
        self.assertTrue(res_other['eligible'])
        self.assertEqual(res_other['gates']['eligibility']['status'], 'unknown')

    def test_company_boast_not_treated_as_applicant_tenure(self) -> None:
        profile = _base_profile(experience_years=2)
        job = _strong_po_job(
            description=(
                'About us:\n'
                'Our company has 15 years of experience delivering fintech products.\n'
                'Wir verfügen über 20 Jahre Markterfahrung.\n\n'
                'Your Role:\n'
                '- Own the product backlog and roadmap.\n'
            )
        )
        res = match_job_v2(job, profile)
        self.assertTrue(res['eligible'])
        self.assertFalse(any(e['signal'] == 'penalty:tenure_shortfall' for e in res['explanation']))


class TestReviewRegressions(unittest.TestCase):
    def test_language_disjunction_evaluates_complete_arms(self):
        job = _strong_po_job(description='Either Spanish B2 or German C2 and French C1 is required')
        for levels, eligible in (
            ({'Spanish': 'C2', 'German': 'C2', 'French': 'B1'}, True),
            ({'Spanish': 'A2', 'German': 'C2', 'French': 'B1'}, False),
            ({'Spanish': 'A2', 'German': 'C2', 'French': 'C1'}, True),
            ({'Spanish': 'A2', 'German': 'C2'}, True),
        ):
            with self.subTest(levels=levels):
                self.assertEqual(match_job_v2(job, _base_profile(language_levels=levels))['eligible'], eligible)

    def test_language_floors_respect_polarity_and_actor(self):
        profile = _base_profile(language_levels={'English': 'B2', 'German': 'B1'})
        for text, status in (
            ('English C1 is helpful but not essential', 'pass'),
            ('C2 level in German is used by our external translation partner', 'unknown'),
            ('Deutsch C2 ist von Vorteil, aber nicht zwingend erforderlich.', 'pass'),
            ('English C1 is nice to have.', 'pass'),
        ):
            with self.subTest(text=text):
                result = match_job_v2(_strong_po_job(description=text), profile)
                self.assertTrue(result['eligible'])
                self.assertEqual(result['gates']['language']['status'], status)

    def test_onsite_mandates_bind_to_current_vacancy(self):
        for sentence, eligible in (
            ('Our warehouse staff must work onsite', True),
            ('Büroanwesenheit ist verpflichtend', False),
            ('Präsenz im Büro ist wünschenswert, aber nicht zwingend.', True),
            ('Arbeit vor Ort ist Pflicht.', False),
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(match_job_v2(_strong_po_job(description=sentence), _base_profile())['eligible'], eligible)

    def test_permit_mandates_bind_to_current_vacancy(self):
        profile = _base_profile(application_preferences={
            'authorized_to_work': False, 'authorization_country': 'Germany',
        })
        for sentence, eligible in (
            ('A valid German work permit is advantageous but not essential', True),
            ('A valid German work permit can make the process easier.', True),
            ('Eine gültige deutsche Arbeitserlaubnis ist wünschenswert.', True),
            ('Eine gültige deutsche Arbeitserlaubnis ist verpflichtend.', False),
            ('A valid German work permit is required. Visa sponsorship is offered in Paris.', False),
            ('A valid German work permit is required. Visa sponsorship is available in Germany.', True),
            ('A valid German work permit is required. Visa sponsorship is available in London.', False),
            ('A valid German work permit is required. Visa sponsorship is available for nursing roles.', False),
            ('Unsere Lagerkräfte benötigen eine gültige deutsche Arbeitserlaubnis.', True),
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(match_job_v2(_strong_po_job(description=sentence), profile)['eligible'], eligible)


    def test_language_floors_apply_to_applicants_and_each_explicit_or_arm(self):
        profile = _base_profile(language_levels={'English': 'B2', 'German': 'B2', 'French': 'B2'})
        for text, status in (
            ('Support English learners at C1 level.', 'pass'),
            ('Betreuen Sie Englischlernende auf Niveau C1.', 'pass'),
            ('Communication with our customers in English at C1 level.', 'unknown'),
            ('Deutsch oder Französisch (mindestens C1)', 'fail'),
            ('German (C1) or French (B2) required.', 'pass'),
            ('Deutsch (C1) oder Französisch erforderlich.', 'unknown'),
            ('German C1 or a relevant teaching certificate is required.', 'unknown'),
        ):
            with self.subTest(text=text):
                result = match_job_v2(_strong_po_job(description=text), profile)
                self.assertEqual(result['gates']['language']['status'], status)
        result = match_job_v2(
            _strong_po_job(description='Deutsch oder Französisch (mindestens C1)'),
            _base_profile(language_levels={'German': 'B2'}),
        )
        self.assertTrue(result['eligible'])
        self.assertTrue(result['language_review'])

    def test_onsite_requires_affirmative_attendance(self):
        for text in (
            'No remote work experience is required.',
            'Keine Remote-Arbeitserfahrung erforderlich.',
            'In-office attendance is not required.',
            'Onsite experience is required to supervise teams Monday to Friday.',
        ):
            with self.subTest(text=text):
                self.assertTrue(match_job_v2(_strong_po_job(description=text), _base_profile())['eligible'])

    def test_permit_assistance_and_sponsorship_scope(self):
        profile = _base_profile(application_preferences={
            'authorized_to_work': False, 'authorization_country': 'Germany',
        })
        for text, eligible in (
            ('We can help employees obtain a German work permit.', True),
            ('A valid German work permit can be obtained after you start.', True),
            ('Wir unterstützen bei der Beantragung einer deutschen Arbeitserlaubnis.', True),
            ('A German work permit is required. Visa sponsorship is available only for Canadian roles.', False),
            ('Eine deutsche Arbeitserlaubnis ist erforderlich. Visa sponsorship is available only for German research roles.', False),
            ('A German work permit is required. Visa sponsorship is available for this role.', True),
        ):
            with self.subTest(text=text):
                self.assertEqual(match_job_v2(_strong_po_job(description=text), profile)['eligible'], eligible)

    def test_only_explicit_inactive_same_scope_credential_hides(self):
        cases = (
            ('Active medical license in Germany required.', 'Medical license in Germany (expired)',
             'Active medical license in Germany', 'Medical license in Austria (expired)'),
            ('Approbation erforderlich.', 'Approbation (abgelaufen)', 'Approbation (gültig)',
             'Berufszulassung (abgelaufen)'),
        )
        for requirement, expired, valid, other in cases:
            job = _strong_po_job(description=f'Requirements:\n{requirement}')
            with self.subTest(requirement=requirement):
                denied = match_job_v2(job, _base_profile(certifications=[expired]))
                self.assertFalse(denied['eligible'])
                condition = next(row for row in denied['prerequisite_conditions'] if row['status'] == 'fail')
                self.assertEqual(condition['job_quote'], requirement)
                self.assertEqual(condition['profile_quote'], expired)
                self.assertTrue(match_job_v2(job, _base_profile(certifications=[other]))['eligible'])
                self.assertTrue(match_job_v2(job, _base_profile(certifications=[expired, valid]))['eligible'])


    def test_language_gate_level_ranges_equivalents_and_mixed_or_and(self):
        b1_german = _base_profile(language_levels={'German': 'B1', 'English': 'C2'})
        b2_german = _base_profile(language_levels={'German': 'B2', 'English': 'C2'})
        c1_german = _base_profile(language_levels={'German': 'C1', 'English': 'C2'})
        for text, prof, expected_eligible in (
            # Leading multi-level ranges: minimum is lower bound (B2 or C1 -> B2)
            ('B2 or C1 German is required for this role.', b1_german, False),
            ('B2 or C1 German is required for this role.', b2_german, True),
            ('Native or C1 German is required.', b2_german, False),
            ('Native or C1 German is required.', c1_german, True),
            # Equivalence / higher qualifiers must not mask as unresolved OR arm
            ('Deutsch C1 oder vergleichbar erforderlich.', b2_german, False),
            ('Deutsch C1 oder besser erforderlich.', b2_german, False),
            ('German C1 or equivalent is required.', b2_german, False),
            # Trailing additive skill OR must not mask language requirement
            ('Deutsch auf C1-Niveau sowie AWS oder GCP erforderlich.', b2_german, False),
            # Level-internal OR combined with inter-language AND must stay AND
            ('German at C2 or native level and fluent English are required.', b2_german, False),
            ('Deutsch auf C1- oder C2-Niveau sowie fließendes Englisch erforderlich.', b2_german, False),
            # Semicolon sentence with benefit second clause must still enforce first clause
            ('German C1 is required; we offer language courses.', b1_german, False),
        ):
            with self.subTest(text=text):
                res = match_job_v2(_strong_po_job(description=text), prof)
                self.assertEqual(res['eligible'], expected_eligible)

    def test_onsite_gate_vacancy_subject_benefits_header_and_german_compounds(self):
        no_onsite = _base_profile(application_preferences={'willing_to_work_onsite': False})
        for text, expected_eligible in (
            # Explicit vacancy subject and About us / Über uns sections must still enforce
            ('This vacancy requires 100% on-site presence in Berlin.', False),
            ('Diese Ausschreibung erfordert 100% Präsenz vor Ort.', False),
            ('About us\nThis role requires on-site presence in our Berlin office.', False),
            ('Über uns\nFür diese Position ist Anwesenheit am Standort München erforderlich.', False),
            # Broadened explicit DE+EN mandatory onsite wordings
            ('On-site presence in Berlin is required for this role.', False),
            ('Tägliche Präsenz im Büro in Hamburg ist erforderlich.', False),
            ('This position is on-site 5 days/week.', False),
            ('Vor Ort 5 Tage/Woche, ohne Homeoffice.', False),
            # Other staff (including German compound nouns) must NEVER hide
            ('Unsere Produktionsmitarbeiter arbeiten 5 Tage vor Ort; deine Stelle ist remote.', True),
            ('Unsere Vertriebsmitarbeiter sind 100% vor Ort beim Kunden tätig.', True),
            ('On-site support is handled by our operations team.', True),
        ):
            with self.subTest(text=text):
                res = match_job_v2(_strong_po_job(description=text), no_onsite)
                self.assertEqual(res['eligible'], expected_eligible)

    def test_authorization_comma_or_arm_benefits_header_and_de_sponsorship(self):
        no_de = _base_profile(application_preferences={
            'authorized_to_work': False,
            'sponsorship_required': True,
            'authorization_country': 'Germany',
        })
        no_eu = _base_profile(application_preferences={
            'authorized_to_work': False,
            'sponsorship_required': True,
            'authorization_country': 'EU',
        })
        eu_auth = _base_profile(application_preferences={
            'authorized_to_work': True,
            'sponsorship_required': False,
            'authorization_country': 'EU',
        })
        for text, prof, expected_eligible in (
            ('This vacancy requires an existing German work permit.', no_de, False),
            ('About us\nCandidates must hold an existing work permit for Germany.', no_de, False),
            ('Existing German work permit, or EU work permit is required.', eu_auth, True),
            ('Existing German work permit, or EU work permit is required.', no_eu, False),
            # Unsaved citizenship OR arm must stay fail-safe eligible (unknown), not hide
            ('Existing German work permit, or EU citizenship is required.', eu_auth, True),
            ('Existing German work permit, or EU citizenship is required.', no_de, True),
            ('Für diese Position benötigen Sie eine deutsche Arbeitserlaubnis; wir unterstützen beim Visum.', no_de, True),
        ):
            with self.subTest(text=text):
                res = match_job_v2(_strong_po_job(description=text), prof)
                self.assertEqual(res['eligible'], expected_eligible)


if __name__ == '__main__':
    unittest.main()
