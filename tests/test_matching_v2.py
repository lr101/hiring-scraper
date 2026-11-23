"""Unit tests for the local-only v2 job matcher (gates, features, ledger, calibration)."""
from __future__ import annotations

import unittest
from collections import Counter

from hiring_scraper.matching_v2 import VERSION, match_job_v2
from hiring_scraper.matching_v2.calibrate import (
    calibrate_float,
    calibrate_score,
    fit_monotone_knots,
    fit_pava,
    validate_knots,
)
from hiring_scraper.matching_v2.features import classify_skill_mention
from hiring_scraper.matching_v2.gates import (
    authorization_jurisdictions,
    explicit_onsite_evidence,
    has_sponsorship_alternative,
    normalize_jurisdictions,
)
from hiring_scraper.matching_v2.weights import (
    CALIBRATION_KNOTS,
    FEATURE_WEIGHTS,
    SOFT_PENALTIES,
)
from tests.eval.metrics import score_band, score_spread
from tests.eval.run import MATCHERS


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


class TestMatchingV2ContractAndLedger(unittest.TestCase):
    def test_consumer_keys_and_local_only_score_source(self) -> None:
        res = match_job_v2(_strong_po_job(), _base_profile())
        self.assertTrue(CONSUMER_KEYS.issubset(res.keys()), CONSUMER_KEYS - set(res.keys()))
        self.assertIsInstance(res['score'], int)
        self.assertGreaterEqual(res['score'], 0)
        self.assertLessEqual(res['score'], 100)
        self.assertTrue(res['eligible'])
        self.assertEqual(res['score_source'], 'v2')
        self.assertNotEqual(res['score_source'], 'llm')
        self.assertEqual(res['calibration']['version'], VERSION)
        self.assertEqual(res['calibration']['calibrated'], res['score'])
        self.assertIn('v2', MATCHERS)
        self.assertIn('v2+sem', MATCHERS)

    def test_ledger_sums_exactly_to_raw_score(self) -> None:
        scenarios = [
            (_strong_po_job(), _base_profile(), None),
            (
                _strong_po_job(),
                _base_profile(),
                {'similarity': 0.86, 'score': 86, 'job_quote': 'Own product roadmap'},
            ),
            (
                _strong_po_job(
                    title='Senior Director of Legal Counsel',
                    description=(
                        'Requirements:\n'
                        '- 10+ years of corporate law and litigation experience.\n'
                        '- Staatsexamen and bar admission required.\n'
                        '- Fluent German requested.\n'
                    ),
                    employment_type='part-time',
                ),
                _base_profile(),
                None,
            ),
        ]
        for job, profile, semantic in scenarios:
            res = match_job_v2(job, profile, semantic=semantic)
            ledger = res['explanation']
            self.assertIsInstance(ledger, list)
            self.assertGreaterEqual(len(ledger), len(FEATURE_WEIGHTS))
            for entry in ledger:
                self.assertEqual(set(entry.keys()), LEDGER_ENTRY_KEYS)
            total = round(sum(float(e['contribution']) for e in ledger), 4)
            self.assertAlmostEqual(total, float(res['calibration']['raw']), places=4)

    def test_fit_tier_respects_unresolved_hard_review_flags(self) -> None:
        clean_res = match_job_v2(_strong_po_job(), _base_profile())
        self.assertTrue(clean_res['eligible'])
        self.assertGreaterEqual(clean_res['score'], 65)
        self.assertEqual(clean_res['fit_tier'], 'recommended')

        # Adding an unresolved qualitative language review flag prevents 'recommended'
        # even while keeping score >= 65 and eligible=True.
        flagged_job = _strong_po_job(
            description=_strong_po_job()['description'] + '\n- Business fluent German required.\n'
        )
        flagged_res = match_job_v2(flagged_job, _base_profile())
        self.assertTrue(flagged_res['eligible'])
        self.assertTrue(flagged_res['language_review'])
        self.assertGreaterEqual(flagged_res['score'], 65)
        self.assertEqual(flagged_res['fit_tier'], 'possible')

    def test_duplicate_requirements_counted_once(self) -> None:
        dup_job = _strong_po_job(
            description=(
                'Requirements:\n'
                '- Master degree in Law required.\n'
                '- Master degree in Law required!\n'
                '- Master degree in Law required\n'
            )
        )
        res = match_job_v2(dup_job, _base_profile())
        law_gaps = [g for g in res['requirement_gaps'] if 'law' in g.casefold()]
        self.assertEqual(len(law_gaps), 1)


class TestMatchingV2HardGates(unittest.TestCase):
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


class TestMatchingV2MustNotHideCases(unittest.TestCase):
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

    def test_incidental_tool_mentions_and_unrelated_specialist_domain(self) -> None:
        # Passive/other-actor/training tool mentions must classify as incidental.
        sent1 = 'Our engineers who write Python maintain the backend.'
        p1 = sent1.index('Python')
        self.assertEqual(classify_skill_mention(sent1, p1, p1 + 6, 'duties'), 'incidental')

        sent2 = 'Tickets are documented in Jira and our internal SAP portal.'
        p2 = sent2.index('Jira')
        self.assertEqual(classify_skill_mention(sent2, p2, p2 + 4, 'duties'), 'incidental')

        sent3 = 'Free SQL training provided on the job.'
        p3 = sent3.index('SQL')
        self.assertEqual(classify_skill_mention(sent3, p3, p3 + 3, 'duties'), 'incidental')

        # Broad duty concepts in an unrelated specialist profession (Legal Counsel vs Product Owner)
        # must stay low (< 30) and never get false duty credit.
        legal_job = {
            'title': 'Senior Legal Counsel (m/w/d)',
            'work_arrangement': 'remote',
            'is_remote': True,
            'employment_type': 'full-time',
            'description': (
                'Responsibilities:\n'
                '- Provide legal advice and draft and negotiate commercial contracts.\n'
                '- Represent the company in legal disputes and regulatory compliance audits.\n'
                '- Coordinate with stakeholders and document contract status in Jira.\n\n'
                'Requirements:\n'
                '- Volljurist with Zweites Staatsexamen and bar admission.\n'
                '- 5+ years of experience in corporate law and GDPR compliance.\n'
            ),
        }
        res = match_job_v2(legal_job, _base_profile())
        self.assertTrue(res['eligible'])
        self.assertLess(res['score'], 30)


class TestMatchingV2CalibrationAndBounds(unittest.TestCase):
    def test_monotone_calibration_and_pava_helpers(self) -> None:
        cleaned = validate_knots(CALIBRATION_KNOTS)
        self.assertGreaterEqual(len(cleaned), 4)

        xs = [-50.0 + 0.5 * i for i in range(341)]  # -50 .. 120
        floats = [calibrate_float(x) for x in xs]
        ints = [calibrate_score(x) for x in xs]
        for a, b in zip(floats, floats[1:]):
            self.assertLessEqual(a, b, f'calibrate_float not monotone: {a} > {b}')
        for a, b in zip(ints, ints[1:]):
            self.assertLessEqual(a, b, f'calibrate_score not monotone: {a} > {b}')
            self.assertGreaterEqual(a, 0)
            self.assertLessEqual(b, 100)

        # Strictly increasing across the interior knot span (no flat plateaus).
        x0, xn = cleaned[0][0], cleaned[-1][0]
        interior = [x for x in xs if x0 <= x <= xn]
        interior_f = [calibrate_float(x) for x in interior]
        for a, b in zip(interior_f, interior_f[1:]):
            self.assertLess(a, b, f'Interior plateau detected: {a} >= {b}')

        # PAVA and knot fitter return strictly increasing knots on noisy inputs.
        pava = fit_pava([10, 20, 30, 40, 50], [20, 15, 50, 45, 90])
        self.assertTrue(all(a[1] <= b[1] for a, b in zip(pava, pava[1:])))
        fitted = fit_monotone_knots(
            [float(i) for i in range(-20, 81, 2)],
            [10.0 if i < 15 else 45.0 if i < 40 else 72.0 if i < 60 else 92.0 for i in range(-20, 81, 2)],
        )
        validate_knots(fitted)

    def test_language_and_visa_combined_impact_at_most_five_points_no_bonus(self) -> None:
        self.assertLessEqual(SOFT_PENALTIES['language_visa_flag_max'], 5.0)
        profile = _base_profile(
            language_levels={'English': 'C1', 'German': 'B1'},
            application_preferences={
                'willing_to_work_onsite': True,
                'authorized_to_work': None,
                'sponsorship_required': None,
                'authorization_country': '',
            },
        )
        base_job = _strong_po_job(
            description=(
                'Your Responsibilities:\n'
                '- Own the product roadmap and backlog prioritization.\n'
                '- Write user stories and coordinate cross-functional stakeholders.\n'
                '- Analyze product KPIs using SQL.\n'
                'Your Profile:\n'
                '- 3+ years as a Product Owner.\n'
                '- Agile methods and product strategy.\n'
            )
        )
        with_lv_job = _strong_po_job(
            description=(
                base_job['description']
                + '- Verhandlungssichere Deutschkenntnisse erforderlich.\n'
                + '- Valid work permit for Germany is required.\n'
            )
        )
        base_res = match_job_v2(base_job, profile)
        lv_res = match_job_v2(with_lv_job, profile)
        self.assertTrue(base_res['eligible'])
        self.assertTrue(lv_res['eligible'])
        self.assertLessEqual(lv_res['score'], base_res['score'], 'Language/visa clauses must never give a bonus')
        delta = abs(base_res['score'] - lv_res['score'])
        self.assertLessEqual(delta, 5, f'Language+visa delta {delta} exceeded 5 points')
        self.assertLessEqual(abs(score_band(base_res['score']) - score_band(lv_res['score'])), 1)

    def test_no_plateaus_on_synthetic_sweep(self) -> None:
        # Synthetic sweep across a smooth gradient of raw scores and synthetic job profiles
        # verifying full range usage, spread >= 60, and no plateau bunching (> 5%).
        raw_sweep = [-25.0 + i * (110.0 / 99.0) for i in range(100)]
        cal_sweep = [calibrate_score(r) for r in raw_sweep]
        spread = score_spread(cal_sweep)
        counts = Counter(cal_sweep)
        max_share = max(counts.values()) / len(cal_sweep)
        self.assertLessEqual(max_share, 0.05, f'Plateau bunching on synthetic sweep: {counts.most_common(5)}')
        self.assertGreaterEqual(max(cal_sweep) - min(cal_sweep), 60)
        self.assertGreaterEqual(max(cal_sweep), 80)
        self.assertLess(min(cal_sweep), 30)
        self.assertLess(spread['plateau_fraction'], 0.10)

        # Also check realistic end-to-end synthetic gradient: irrelevant -> partial -> strong.
        profile = _base_profile()
        jobs = [
            {
                'title': 'Krankenpfleger Intensivpflege (m/w/d)',
                'description': 'Patientenversorgung, Krankenpflege, Medikamentengabe, Pflegeexamen erforderlich.',
            },
            {
                'title': 'Senior Legal Counsel Corporate Law',
                'description': 'Draft commercial contracts, represent in court, Staatsexamen required.',
            },
            {
                'title': 'Sales Account Executive B2B',
                'description': 'Cold calling, sales pipeline, closing deals, new business development.',
            },
            {
                'title': 'Project Coordinator Operations',
                'description': 'Coordinate project milestones, process improvement, stakeholder alignment.',
            },
            _strong_po_job(),
            _strong_po_job(
                description=_strong_po_job()['description']
                + '- Lead product strategy, product discovery, A/B testing, and OKR alignment.\n'
            ),
        ]
        scores = [match_job_v2(j, profile)['score'] for j in jobs]
        self.assertLess(scores[0], 30)
        self.assertLess(scores[1], 30)
        self.assertGreaterEqual(scores[-1], 80)
        self.assertGreaterEqual(scores[-1] - scores[0], 60)


class TestReviewRegressions(unittest.TestCase):
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

    def test_mixed_language_and_permit_ablation_retains_skill_evidence(self):
        from tests.eval.ablation import ablate_lang_visa

        profile = _base_profile(
            skills=['Kubernetes'], language_levels={'German': 'B2'}, application_preferences={},
        )
        for text in (
            'You must speak fluent German and have advanced Kubernetes experience.',
            'Du musst fließendes Deutsch sprechen und bringst Kubernetes-Erfahrung mit.',
            'You must have advanced Kubernetes experience and a valid German work permit.',
            'You must speak fluent German and have advanced Kubernetes experience and a valid German work permit.',
            'Fundierte Kubernetes-Erfahrung und eine gültige Arbeitserlaubnis für Deutschland erforderlich.',
        ):
            with self.subTest(text=text):
                job = _strong_po_job(description=text)
                ablated, changed = ablate_lang_visa(job)
                self.assertTrue(changed)
                self.assertIn('Kubernetes', ablated['description'])
                before = match_job_v2(job, profile)
                after = match_job_v2(ablated, profile)
                self.assertIn('Kubernetes', before['matched_skills'])
                self.assertEqual(before['qualification_fit'], after['qualification_fit'])
                self.assertGreaterEqual(after['score'] - before['score'], 0)
                self.assertLessEqual(after['score'] - before['score'], 5)

    def test_title_only_preference_is_uncertain_and_below_30(self):
        for title in ('Data Analyst', 'Datenanalyst'):
            with self.subTest(title=title):
                result = match_job_v2({'title': title}, {'desired_roles': [title]})
                self.assertLess(result['score'], 30)
                self.assertEqual(result['qualification_fit'], 0)
                self.assertTrue(result['uncertain'])
                self.assertTrue(result['unknowns'])

    def test_consumer_shapes_with_populated_evidence(self):
        import json

        from hiring_scraper.matching import match_job

        profile = _base_profile(skills=['Release orchestration', 'Releasekoordination'])
        for text, name in (
            ('Requirements:\n- Experience with release orchestration.\n- Valid German work permit required.\n- 5+ years of experience.', 'Release orchestration'),
            ('Ihr Profil:\n- Erfahrung mit Releasekoordination.\n- Gültige Arbeitserlaubnis für Deutschland erforderlich.\n- Mindestens 5 Jahre Berufserfahrung.', 'Releasekoordination'),
        ):
            with self.subTest(text=text):
                job = _strong_po_job(description=text)
                v1 = match_job(job, profile)
                v2 = match_job_v2(job, profile)
                for key, value in v1.items():
                    self.assertIn(key, v2)
                    self.assertIs(type(v2[key]), type(value), key)
                self.assertEqual(v1['local_context'], v2['local_context'])
                for gate, fields in v1['gates'].items():
                    for key, value in fields.items():
                        self.assertIs(type(v2['gates'][gate][key]), type(value))
                self.assertTrue(any(row['name'] == name for row in v1['custom_skill_evidence']))
                skill = next(row for row in v2['custom_skill_evidence'] if row['name'] == name)
                self.assertEqual(set(skill), {'name', 'evidence', 'source'})
                self.assertTrue(all(isinstance(value, str) and value for value in skill.values()))
                v1_obs = next(row for row in v1['requirement_observations'] if row.get('kind') == 'experience')
                v2_obs = next(row for row in v2['requirement_observations'] if row.get('kind') == 'experience')
                for key, value in v1_obs.items():
                    self.assertIn(key, v2_obs)
                    self.assertIs(type(v2_obs[key]), type(value), key)
                condition = next(row for row in v2['prerequisite_conditions'] if row['gate'] == 'eligibility')
                self.assertTrue(condition['job_quote'])
                self.assertTrue(condition['profile_quote'])
                for key in ('kind', 'status', 'reason'):
                    self.assertIs(type(condition[key]), type(v1['prerequisite_conditions'][0][key]))
                json.dumps(v2, allow_nan=False)


    def test_structured_mixed_duty_ablation_keeps_clean_citations(self):
        profile = {'skills': ['Python'], 'summary': 'Built Python data pipelines for billing.'}
        skill_duty = 'Build Python data pipelines for billing.'
        baseline = match_job_v2({'title': 'Data Engineer', 'raw_metadata': {'duties': [skill_duty]}}, profile)
        job = {'title': 'Data Engineer', 'raw_metadata': {
            'duties': ['Build Python data pipelines for billing and speak fluent German.'],
        }}
        actual = match_job_v2(job, profile)
        self.assertIn('Python', actual['matched_skills'])
        self.assertGreater(baseline['task_fit'], 0)
        self.assertEqual(actual['task_fit'], baseline['task_fit'])
        self.assertTrue(actual['duty_evidence']['duties'])
        self.assertTrue(all('German' not in row['job_quote'] for row in actual['duty_evidence']['duties']))


if __name__ == '__main__':
    unittest.main()
