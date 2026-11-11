"""Persisted model assessments replace numeric matching without network reads."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import importlib
import importlib.util
import unittest
import warnings
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from hiring_scraper.app import api, models, profiles
from hiring_scraper.app.job_search import ensure_profile_match_indexes, profile_match_index_stale


PROFILE = dict(name='Developer', desired_roles=['Software developer'], skills=['Python', 'SQL'],
               languages=['English'], summary='Build Python services and SQL databases.',
               matching_defaults={'min_match_score': 30})
DESCRIPTION = 'Your responsibilities:\nBuild Python services and SQL databases. ' * 12
USAGE = {'state': 'ready', 'allowed': True, 'used_percent': 10}


def assessment(score=90):
    return {'dimensions': {name: {'score': score, 'evidence': 'Python services are documented.',
                            'evidence_source': 'profile.summary',
                            'evidence_quote': 'Build Python services and SQL databases.'}
                           for name in ['skills', 'experience', 'behavioral', 'career_alignment']},
            'gates': {name: {'status': 'pass', 'reason': 'Explicitly compatible.'}
                      for name in ['location', 'language', 'eligibility']},
            'strengths': ['Documented service experience.'], 'gaps': [], 'unknowns': [],
            'requirements': [{'requirement': 'Python services', 'priority': 'required', 'importance': 'core', 'status': 'supported',
                'job_source': 'job.description', 'job_quote': 'Build Python services and SQL databases.',
                'evidence_source': 'profile.summary', 'evidence_quote': 'Build Python services and SQL databases.',
                'explanation': 'Direct service experience.'}]}


class AssessmentTests(unittest.TestCase):
    def overlay_prerequisite(self, condition='A completed bachelor degree in chemistry is mandatory.',
                             evidence='I graduated with a bachelor degree in chemistry.',
                             extra='', category='education', quote=None, source='profile.career_evidence',
                             source_extras=None, profile_changes=None, broad_job_quote=False):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from hiring_scraper.matching import match_job
        service = self.service()
        description = DESCRIPTION + '\nRequirements:\n' + condition + '\n' + extra
        sources = {'job.description': description, 'profile.summary': PROFILE['summary'],
                   source: evidence, **(source_extras or {})}
        data = assessment()
        data['requirements'].append({**data['requirements'][0], 'category': category,
            'requirement': quote or condition, 'job_quote': description if broad_job_quote else quote or condition,
            'evidence_source': source, 'evidence_quote': evidence, 'status': 'supported'})
        local = match_job({'title': 'Assistant', 'description': description},
                          {**PROFILE, 'career_evidence': evidence, **(profile_changes or {})})
        job, profile = SimpleNamespace(id=1), SimpleNamespace(id=2)
        row = SimpleNamespace(version=service.VERSION, source_hash=service._hash([service.VERSION, 'j', 'p']),
                              assessment=data, scored_at=datetime.now(timezone.utc))
        docs = SimpleNamespace(llm_scores={(2, 1): row}, llm_job_hashes={1: 'j'}, llm_profile_hashes={2: 'p'})
        with patch.object(service, 'assessment_sources', return_value=sources):
            return service.apply_cached_assessment(local, job, profile, docs)

    def test_grounded_completed_degree_discharges_only_corresponding_local_unknown(self):
        result = self.overlay_prerequisite()
        self.assertEqual(result['gates']['eligibility']['status'], 'pass')
        self.assertFalse(result['mandatory_review'])
        self.assertNotEqual(result['fit_tier'], 'unlikely')
        self.assertFalse(any('degree' in note.lower() and 'unverified' in note.lower()
                             for note in result['unknowns']))
        self.assertTrue(any(row.get('kind') == 'education' and row['status'] == 'supported'
                            for row in result['requirement_observations']))

    def test_unrelated_or_insufficient_degree_evidence_cannot_discharge_unknown(self):
        for changes in (
            {'quote': 'Build Python services and SQL databases.'},
            {'category': 'capability'},
            {'evidence': 'Bachelor degree in chemistry, 2018–2022.'},
            {'evidence': 'I completed a degree in chemistry.'},
            {'evidence': 'I graduated in history. I am pursuing a bachelor degree in chemistry.'},
            {'evidence': 'I graduated with a bachelor degree in history.'},
            {'condition': 'A completed master degree in chemistry is mandatory.'},
            {'evidence': 'I have not completed my bachelor degree in chemistry.'},
            {'source': 'profile.education[0].title', 'evidence': 'Bachelor degree in chemistry',
             'source_extras': {'profile.education[0].status': 'in_progress'}},
        ):
            with self.subTest(changes=changes):
                result = self.overlay_prerequisite(**changes)
                self.assertTrue(result['mandatory_review'])
                self.assertEqual(result['gates']['eligibility']['status'], 'unknown')

    def test_broad_citation_retains_matching_degree_clause_and_separate_completion(self):
        result = self.overlay_prerequisite(broad_job_quote=True)
        self.assertFalse(result['mandatory_review'])
        self.assertEqual(result['gates']['eligibility']['status'], 'pass')
        unrelated = self.overlay_prerequisite(
            evidence='I graduated with a bachelor degree in history. I completed a short course in chemistry.')
        self.assertTrue(unrelated['mandatory_review'])
        self.assertEqual(unrelated['gates']['eligibility']['status'], 'unknown')

    def test_broad_artifact_citation_ignores_unrelated_optional_material(self):
        data = assessment()
        text = 'Include a portfolio of three drawings; degree completion is not required. A cover letter is optional.'
        data['requirements'].append({**data['requirements'][0], 'category': 'capability',
            'requirement': 'Portfolio of drawings', 'job_quote': text, 'status': 'unknown',
            'evidence_source': '', 'evidence_quote': ''})
        result = self.service().validate_assessment(data)
        self.assertTrue(result['mandatory_review'])
        self.assertEqual(result['gates']['eligibility']['status'], 'unknown')
        # The same broad job excerpt does not make every capability an artifact.
        data['requirements'][-1].update(requirement='Build Python services', status='transferable',
            evidence_source='profile.summary', evidence_quote=PROFILE['summary'])
        ordinary = self.service().validate_assessment(data)
        self.assertFalse(ordinary['mandatory_review'])

    def test_structured_qualification_sources_reconcile_with_canonical_ai_paths(self):
        service = self.service()
        text = 'A completed bachelor degree in chemistry is mandatory.'
        evidence = 'I graduated with a bachelor degree in chemistry.'
        for local_source, ai_source in (
            ('structured.qualifications[0]', 'job.raw_metadata.qualifications[0]'),
            ('detail.qualifications[0]', 'job.raw_metadata.detail_requirements.qualifications[0]')):
            with self.subTest(source=local_source):
                condition = {'kind': 'education', 'status': 'unknown', 'job_quote': text,
                    'job_source': local_source, 'qualification': {'level': 'bachelor', 'fields': ['chemistry']}}
                row = {**assessment()['requirements'][0], 'category': 'education',
                    'job_source': ai_source, 'job_quote': text, 'evidence_source': 'profile.career_evidence',
                    'evidence_quote': evidence}
                self.assertIsNotNone(service._supported_local_condition(condition, [row],
                                     {'profile.career_evidence': evidence}))
                row['job_source'] += '.unrelated'
                self.assertIsNone(service._supported_local_condition(condition, [row],
                                  {'profile.career_evidence': evidence}))

    def test_grounded_credential_clears_only_same_credential(self):
        result = self.overlay_prerequisite(condition='PMP certification is mandatory.',
                                          evidence='I hold current PMP certification.', category='credential')
        self.assertFalse(result['mandatory_review'])
        self.assertEqual(result['gates']['eligibility']['status'], 'pass')
        other = self.overlay_prerequisite(condition='PMP certification is mandatory.',
                                         evidence='I hold current PRINCE2 certification.', category='credential')
        self.assertTrue(other['mandatory_review'])

    def test_waived_education_row_does_not_create_mandatory_review(self):
        data = assessment()
        data['requirements'].append({**data['requirements'][0], 'category': 'education',
            'requirement': 'Degree completion', 'job_quote': 'Degree completion is not required.',
            'status': 'unknown', 'evidence_source': '', 'evidence_quote': ''})
        result = self.service().validate_assessment(data)
        self.assertFalse(result['mandatory_review'])
        self.assertEqual(result['gates']['eligibility']['status'], 'pass')

    def test_independent_prerequisites_survive_degree_reconciliation(self):
        for extra in ('Current student enrollment is mandatory.',
                      'A valid PMP certificate is mandatory.',
                      'You must hold existing UK work rights.',
                      'Bring a portfolio of sample work.',
                      'You must reside in the UK.'):
            with self.subTest(extra=extra):
                result = self.overlay_prerequisite(extra=extra)
                self.assertTrue(result['mandatory_review'])
                self.assertEqual(result['fit_tier'], 'possible')
                self.assertEqual(result['label'], 'Verify mandatory eligibility')
        language = self.overlay_prerequisite(extra='Excellent German is required.')
        self.assertTrue(language['language_review'])
        self.assertEqual(language['gates']['language']['status'], 'unknown')
        failed = self.overlay_prerequisite(extra='Mandatory: seven years leading paid industrial programs.',
                                          profile_changes={'experience_years': 2})
        self.assertEqual(failed['gates']['eligibility']['status'], 'fail')
        self.assertFalse(failed['eligible'])
        self.assertEqual(failed['fit_tier'], 'unlikely')

    def test_required_artifact_capability_gets_review_without_gating_ordinary_capabilities(self):
        for text, priority, review in (
            ('Bring a portfolio of sample work.', 'required', True),
            ('A portfolio is optional.', 'required', False),
            ('No portfolio is required.', 'required', False),
            ('Bring a portfolio of sample work.', 'preferred', False),
            ('Build Python services.', 'required', False),
            ('Portfolio management experience is required.', 'required', False),
        ):
            with self.subTest(text=text, priority=priority):
                data = assessment()
                data['requirements'].append({**data['requirements'][0], 'category': 'capability',
                    'requirement': text, 'job_quote': text, 'status': 'unknown',
                    'priority': priority, 'evidence_source': '', 'evidence_quote': ''})
                result = self.service().validate_assessment(data)
                self.assertEqual(result['mandatory_review'], review)
                self.assertEqual(result['gates']['eligibility']['status'], 'unknown' if review else 'pass')

    def test_scored_dimensions_require_exact_profile_citations(self):
        data = assessment()
        sources = {'profile.summary': 'Build Python services and SQL databases.',
                   'job.description': DESCRIPTION}
        for dimension in data['dimensions'].values():
            dimension.pop('evidence_source', None)
            dimension.pop('evidence_quote', None)
        with self.assertRaisesRegex(ValueError, 'dimension citation'):
            self.service().validate_assessment(data, sources)
        for dimension in data['dimensions'].values():
            dimension.update(evidence_source='profile.summary',
                             evidence_quote=sources['profile.summary'])
        self.assertEqual(self.service().validate_assessment(data, sources)['score'], 90)
        data['dimensions']['skills']['evidence_quote'] = 'Invented professional expertise.'
        with self.assertRaisesRegex(ValueError, 'dimension citation'):
            self.service().validate_assessment(data, sources)

    def test_dimension_reference_resolves_to_bound_source_and_quote(self):
        data = assessment()
        for dimension in data['dimensions'].values():
            dimension.pop('evidence_source')
            dimension.pop('evidence_quote')
            dimension['evidence_ref'] = 'p001'
        resolved = self.service()._resolve_citations(data, {
            'p001': {'source': 'profile.summary', 'quote': 'Build Python services and SQL databases.'}})
        self.assertEqual(resolved['dimensions']['skills']['evidence_source'], 'profile.summary')
        self.assertEqual(resolved['dimensions']['skills']['evidence_quote'], 'Build Python services and SQL databases.')
        data['dimensions']['skills']['evidence_ref'] = 'absent'
        with self.assertRaisesRegex(ValueError, 'dimension citation'):
            self.service()._resolve_citations(data, {})

    def test_local_fallback_preserves_calibrated_fit_without_claiming_ai(self):
        from types import SimpleNamespace
        service = self.service()
        result = service.apply_cached_assessment(
            {'score': 82, 'eligible': True, 'fit_tier': 'recommended'},
            SimpleNamespace(id=1), SimpleNamespace(id=1), SimpleNamespace(llm_scores={}))
        self.assertEqual(result['score'], 82)
        self.assertEqual(result['score_source'], 'local')
        self.assertIsNone(result['llm_match'])
        self.assertEqual(result['fit_tier'], 'possible')

    def service(self):
        self.assertIsNotNone(importlib.util.find_spec('hiring_scraper.app.profile_llm'),
                             'Persisted profile LLM assessments are not implemented')
        return importlib.import_module('hiring_scraper.app.profile_llm')

    def test_helper_requires_explicit_provider_instead_of_unbudgeted_default(self):
        with self.assertRaises(TypeError):
            self.service().generate_assessment('No implicit provider permitted')

    def test_unverified_formal_prerequisite_changes_eligibility_not_task_fit(self):
        data = assessment()
        data['requirements'].append({**data['requirements'][0], 'category': 'education',
            'requirement': 'Completed doctorate', 'status': 'unknown',
            'evidence_source': '', 'evidence_quote': ''})
        result = self.service().validate_assessment(data)
        self.assertEqual(result['qualification_fit'], 90)
        self.assertEqual(result['gates']['eligibility']['status'], 'unknown')
        self.assertTrue(result['mandatory_review'])

    def test_supported_formal_row_cannot_erase_an_independent_eligibility_failure(self):
        data = assessment()
        data['requirements'].append({**data['requirements'][0], 'category': 'education',
            'requirement': 'Completed doctorate'})
        data['gates']['eligibility'] = {'status': 'fail', 'reason': 'Independent condition conflicts.'}
        result = self.service().validate_assessment(data)
        self.assertEqual(result['gates']['eligibility']['status'], 'fail')

    def test_unknown_dimensions_renormalize_without_inventing_neutral_scores(self):
        service = self.service()
        data = assessment()
        data['dimensions']['skills']['score'] = 100
        data['dimensions']['experience']['score'] = 40
        data['dimensions']['behavioral']['score'] = None
        data['dimensions']['career_alignment']['score'] = None
        result = service.validate_assessment(data)
        self.assertEqual(result['score'], 73)
        self.assertTrue(result['uncertain'])
        self.assertIsNone(result['dimensions']['career_alignment']['score'])

    def test_scores_expose_separate_qualification_preferences_and_completeness(self):
        data = assessment()
        data['dimensions']['skills']['score'] = 100
        data['dimensions']['experience']['score'] = 40
        data['dimensions']['behavioral']['score'] = None
        data['dimensions']['career_alignment']['score'] = 20
        result = self.service().validate_assessment(data)
        self.assertEqual(result['qualification_fit'], 73)
        self.assertEqual(result['preference_fit'], 20)
        self.assertEqual(result['dimension_completeness'], 85)

    def test_calculation_exposes_weighted_task_fit_before_evidence_ceiling(self):
        data = assessment()
        data['dimensions']['skills']['score'] = 90
        data['dimensions']['experience']['score'] = 80
        data['requirements'].append({**data['requirements'][0],
            'requirement': 'Additional core task', 'status': 'unknown',
            'evidence_source': '', 'evidence_quote': ''})
        result = self.service().validate_assessment(data)
        self.assertEqual(result['weighted_task_fit'], 85)
        self.assertEqual(result['role_evidence_score'], 50)
        self.assertEqual(result['score'], 50)

    def test_career_preferences_do_not_dilute_supported_task_qualification(self):
        data = assessment(90)
        data['dimensions']['behavioral']['score'] = 0
        data['dimensions']['career_alignment']['score'] = 0
        result = self.service().validate_assessment(data)
        self.assertEqual(result['score'], 90)
        self.assertEqual(result['qualification_fit'], 90)
        self.assertEqual(result['preference_fit'], 0)

    def test_unquoted_gate_failure_becomes_unknown_when_sources_are_verified(self):
        data = assessment()
        data['gates']['eligibility'] = {'status': 'fail', 'reason': 'Authorization is not stated.'}
        sources = {'job.description': 'Build Python services and SQL databases.',
                   'profile.summary': 'Build Python services and SQL databases.'}
        result = self.service().validate_assessment(data, sources)
        self.assertEqual(result['gates']['eligibility']['status'], 'unknown')
        self.assertTrue(result['eligible'])

    def test_unrelated_missing_capability_cannot_establish_logistics_failures(self):
        sources = {'job.description': 'Build Python services. Work authorization in Germany required. German C1 required. Onsite location required.',
                   'profile.summary': 'I have no Python experience.'}
        for gate in ('eligibility', 'language', 'location'):
            with self.subTest(gate=gate):
                data = assessment()
                for dimension in data['dimensions'].values():
                    dimension['evidence_quote'] = sources['profile.summary']
                data['requirements'][0].update(category='capability', status='missing',
                    job_quote=sources['job.description'], evidence_quote=sources['profile.summary'])
                data['gates'][gate] = {'status': 'fail', 'reason': 'Explicit incompatibility.'}
                result = self.service().validate_assessment(data, sources)
                self.assertEqual(result['gates'][gate]['status'], 'unknown')
                self.assertTrue(result['eligible'])

    def test_generation_repairs_unverifiable_quotes_before_returning_publishable_rows(self):
        service = self.service()
        invalid = assessment()
        invalid['requirements'][0]['evidence_quote'] = 'An invented translated quotation.'
        replies = iter([invalid, assessment()])
        sources = {'job.description': 'Build Python services and SQL databases.',
                   'profile.summary': 'Build Python services and SQL databases.'}
        prompt = 'Evaluate supplied facts.\nDATA:\n' + json.dumps({'evidence_sources': sources})
        result = service.generate_assessment(prompt, generate=lambda *args, **kwargs: next(replies))
        checked = service.validate_assessment(result, sources)
        self.assertEqual(checked['requirements'][0]['status'], 'supported')
        self.assertEqual(checked['score'], 90)

    def test_one_transport_timeout_can_recover_with_same_grounded_contract(self):
        from fastapi import HTTPException
        sources = {'job.description': 'Build Python services and SQL databases.',
                   'profile.summary': 'Build Python services and SQL databases.'}
        replies = iter([HTTPException(504, 'Timed out'), assessment()])
        def provider(*args, **kwargs):
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply
        result = self.service().generate_assessment('Evaluate.\nDATA:\n' +
            json.dumps({'evidence_sources': sources}), generate=provider)
        self.assertEqual(self.service().validate_assessment(result, sources)['score'], 90)

    def test_quota_rejection_is_never_retried(self):
        from fastapi import HTTPException
        sources = {'job.description': 'Build Python services and SQL databases.'}
        calls = []
        def provider(*args, **kwargs):
            calls.append(True)
            raise HTTPException(429, 'Quota guard')
        with self.assertRaises(HTTPException) as caught:
            self.service().generate_assessment('Evaluate.\nDATA:\n' +
                json.dumps({'evidence_sources': sources}), generate=provider)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(len(calls), 1)

    def test_generation_does_not_publish_repeatedly_ungrounded_claims(self):
        service = self.service()
        invalid = assessment()
        invalid['requirements'][0]['evidence_quote'] = 'Invented.'
        sources = {'job.description': 'Build Python services and SQL databases.',
                   'profile.summary': 'Build Python services and SQL databases.'}
        prompt = 'Evaluate supplied facts.\nDATA:\n' + json.dumps({'evidence_sources': sources})
        with self.assertRaises(ValueError):
            service.generate_assessment(prompt, generate=lambda *args, **kwargs: copy.deepcopy(invalid))

    def test_bounded_quote_options_preserve_late_fact_sections_and_original_text(self):
        sources = {f'profile.skills[{i}]': f'Tool {i}' for i in range(100)}
        sources.update({'profile.career_evidence': 'Maintained customer records.',
                        'profile.language_levels.German': 'B2'})
        keys, quotes = self.service()._citation_options(sources, 'profile.', 8)
        self.assertIn('profile.career_evidence', keys)
        self.assertIn('B2', quotes)
        self.assertLessEqual(len(keys), 8)
        self.assertLessEqual(len(quotes), 8)
        self.assertTrue(all(any(quote in value for value in sources.values()) for quote in quotes))

    def test_citation_options_retain_parent_scope_and_education_completion(self):
        sources = {'profile.cv_career_facts.education[0].title': 'Master of Chemistry',
                   'profile.cv_career_facts.education[0].current': 'True',
                   'profile.skill_evidence.Python.note': 'Built an assignment.',
                   'profile.skill_evidence.Python.context': 'academic',
                   'profile.skill_evidence.SQL.note': 'Mixed practice.',
                   'profile.skill_evidence.SQL.context': 'professional and academic',
                   'profile.skills[0]': 'Python'}
        catalog = self.service()._citation_catalog(sources, 'profile.', 32)
        by_source = {row['source']: row for row in catalog.values()}
        self.assertEqual(by_source['profile.cv_career_facts.education[0].title']['context']['completion'],
                         'in_progress')
        self.assertEqual(by_source['profile.skill_evidence.Python.note']['context']['scope'], 'academic')
        self.assertEqual(by_source['profile.skill_evidence.SQL.note']['context']['scope'], 'unspecified')
        self.assertEqual(by_source['profile.skills[0]']['context']['scope'], 'declared')

    def test_bound_citation_reference_resolves_original_source_and_quote_together(self):
        sources = {'job.description': 'Build Python services and SQL databases.',
                   'profile.summary': 'Build Python services and SQL databases.'}
        prompt = 'Evaluate supplied facts.\nDATA:\n' + json.dumps({'evidence_sources': sources})
        def provider(request, schema, **kwargs):
            catalog = json.loads(request.split('\nDATA:\n')[1])['citation_catalog']
            job_ref = next(key for key, value in catalog.items() if value['source'] == 'job.description')
            profile_ref = next(key for key, value in catalog.items() if value['source'] == 'profile.summary')
            raw = assessment()
            row = raw['requirements'][0]
            for key in ('job_source', 'job_quote', 'evidence_source', 'evidence_quote'):
                row.pop(key)
            row.update(job_ref=job_ref, evidence_ref=profile_ref)
            return raw
        result = self.service().generate_assessment(prompt, generate=provider)
        row = result['requirements'][0]
        self.assertEqual(row['job_source'], 'job.description')
        self.assertEqual(row['evidence_source'], 'profile.summary')
        self.assertEqual(row['evidence_quote'], 'Build Python services and SQL databases.')

    def test_incidental_skills_cannot_produce_high_overall_match(self):
        data = assessment(95)
        data['requirements'][0]['status'] = 'unknown'
        data['requirements'] += [{**data['requirements'][0], 'importance': 'supporting',
            'status': 'supported', 'requirement': f'Incidental tool {i}'} for i in range(20)]
        result = self.service().validate_assessment(data)
        self.assertEqual(result['score'], 15)
        self.assertEqual(result['qualification_fit'], 15)
        self.assertEqual(result['role_evidence_score'], 15)

    def test_malformed_scores_and_extra_output_are_rejected(self):
        service = self.service()
        for value in [True, '80', -1, 101, float('nan'), float('inf')]:
            data = assessment()
            data['dimensions']['skills']['score'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                service.validate_assessment(data)
        data = assessment(); data['score'] = 100
        with self.assertRaises(ValueError):
            service.validate_assessment(data)

    def test_failed_gate_vetoes_eligibility_unknown_gate_marks_uncertainty(self):
        service = self.service()
        data = assessment(); data['gates']['language'] = {'status': 'unknown', 'reason': 'No language level supplied.'}
        result = service.validate_assessment(data)
        self.assertTrue(result['eligible']); self.assertTrue(result['uncertain'])
        data['gates']['eligibility'] = {'status': 'fail', 'reason': 'Required authorization is absent.'}
        self.assertFalse(service.validate_assessment(data)['eligible'])


class PersistedAssessmentTests(unittest.TestCase):
    service = AssessmentTests.service

    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool,
                                    connect_args={'check_same_thread': False})
        models.Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        with self.factory() as session:
            self.profile_id = profiles.create_profile(profiles.ProfileRequest(**PROFILE), session)['id']
            company = models.Company(source='test', source_id='llm', name='Example')
            feed = models.JobFeed(company=company, provider='html_jobs', status='parsed', feed_url='https://example.org/jobs')
            job = models.Job(feed=feed, title='Software developer', description=DESCRIPTION,
                             external_id='one', url='https://example.org/jobs/one', is_remote=True)
            session.add(company); session.commit(); self.job_id = job.id
            ensure_profile_match_indexes(session, [session.get(models.UserProfile, self.profile_id)])
            session.commit()

    def tearDown(self):
        self.engine.dispose()

    def refresh(self, data=None, generate=None, usage=None):
        service = self.service()
        with patch.object(service, 'SessionLocal', self.factory):
            return service.refresh_llm_scores_once(
                generate=generate or (lambda prompt, schema, **kwargs: copy.deepcopy(data or assessment())),
                usage=usage or (lambda: dict(USAGE)))

    def test_deactivated_job_in_cached_pool_never_spends_another_call(self):
        self.assertTrue(self.refresh())
        with self.factory.begin() as session:
            session.get(models.Job, self.job_id).is_active = False
        calls = []
        self.assertFalse(self.refresh(generate=lambda *args, **kwargs: calls.append(kwargs) or assessment()))
        self.assertEqual(calls, [])

    def test_new_explicit_language_conflict_is_rechecked_inside_cached_pool(self):
        with self.factory.begin() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            profile.preferences = {**profile.preferences, 'language_levels': {'German': 'B2', 'English': 'C1'}}
        self.assertTrue(self.refresh())
        with self.factory.begin() as session:
            session.get(models.Job, self.job_id).description = DESCRIPTION + ' German C2 required.'
        calls = []
        self.assertFalse(self.refresh(generate=lambda *args, **kwargs: calls.append(kwargs) or assessment()))
        self.assertEqual(calls, [])

    def test_deleted_job_in_cached_selection_is_skipped_without_inference(self):
        self.assertTrue(self.refresh())
        with self.factory.begin() as session:
            session.delete(session.get(models.Job, self.job_id))
        def unexpected_provider(*args, **kwargs):
            self.fail('Deleted jobs must not invoke the provider')
        self.assertFalse(self.refresh(generate=unexpected_provider))

    def test_repeated_worker_passes_do_not_drain_inventory_after_ai_rejection(self):
        with self.factory.begin() as session:
            original = session.get(models.Job, self.job_id)
            for i in range(29):
                session.add(models.Job(feed_id=original.feed_id, title='Software developer',
                    description=DESCRIPTION, external_id=f'selective-{i}',
                    url=f'https://example.org/jobs/selective-{i}', is_remote=True))
        with self.factory() as session:
            before = self.service()._selected_job_ids(session, session.get(models.UserProfile, self.profile_id))
        self.assertEqual(len(before), 2)  # Employer diversity cap, within the 10% pool.
        for _ in before:
            self.assertTrue(self.refresh(assessment(0)))
        for _ in range(3):
            self.assertFalse(self.refresh())
        with self.factory() as session:
            after = self.service()._selected_job_ids(session, session.get(models.UserProfile, self.profile_id))
            self.assertEqual(before, after)
            self.assertEqual(len(session.scalars(select(models.ProfileLLMScore)).all()), 2)

    def test_ai_selection_does_not_spend_on_known_out_of_area_job(self):
        with self.factory.begin() as session:
            job = session.get(models.Job, self.job_id)
            job.is_remote = False
            job.work_arrangement = 'onsite'
            session.add(models.JobLocation(job_id=job.id, label='Berlin', country_code='DE',
                                           latitude=52.52, longitude=13.405))
            profile = session.get(models.UserProfile, self.profile_id)
            profile.preferences = {**profile.preferences, 'search_area': {
                'latitude': 49.0069, 'longitude': 8.4034, 'radius_km': 15, 'city': 'Karlsruhe', 'country_code': 'DE'}}
        calls = []
        self.assertFalse(self.refresh(generate=lambda *a, **k: calls.append(True)))
        self.assertEqual(calls, [])

    def test_disabled_rolling_budget_never_calls_provider(self):
        calls = []
        with patch.dict('os.environ', {'RADIUS_MATCH_AI_CALLS_PER_5H': '0'}):
            self.assertFalse(self.refresh(generate=lambda *a, **k: calls.append(True)))
        self.assertEqual(calls, [])

    def test_repair_attempts_consume_rolling_budget_before_provider_call(self):
        calls = []
        def provider(*args, **kwargs):
            calls.append(True)
            return {'invalid': 'response'}
        with patch.dict('os.environ', {'RADIUS_MATCH_AI_CALLS_PER_5H': '1'}):
            self.assertFalse(self.refresh(generate=provider))
        self.assertEqual(len(calls), 1)
        with self.factory() as session:
            self.assertEqual(len(session.scalars(select(models.AIMatchingCall)).all()), 1)

    def test_persisted_high_low_and_zero_scores_replace_detail_journey_and_index(self):
        for score in [91, 12, 0]:
            with self.subTest(score=score):
                if score != 91:
                    with self.factory() as session:
                        job = session.get(models.Job, self.job_id)
                        job.description += f'\nRevision {score}.'
                        session.commit()
                        ensure_profile_match_indexes(session, [session.get(models.UserProfile, self.profile_id)])
                        session.commit()
                self.assertTrue(self.refresh(assessment(score)))
                with self.factory() as session:
                    detail = api.get_job(self.job_id, session=session, profile_id=self.profile_id)
                    match = detail['profile_match']
                    self.assertEqual(match['score'], score)
                    self.assertEqual(match['score_source'], 'llm')
                    self.assertEqual(match['llm_match']['model'], 'gpt-6.1-sol')
                    row = session.get(models.ProfileJobMatch, (self.profile_id, self.job_id))
                    self.assertEqual(row.match_score, score)
                    if score == 91:
                        page = api.list_applications(profile_ids=str(self.profile_id), statuses='all', sort='match', offset=0, limit=40, session=session)
                        self.assertEqual(page['items'][0]['profile_match']['score'], 91)
                        api.set_job_application(self.profile_id, self.job_id, api.ApplicationRequest(status='open'), session)
                    else:
                        self.assertEqual(detail['application']['status'], 'open')

    def test_high_ai_task_score_cannot_erase_unresolved_mandatory_authorization(self):
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            job.description += '\nApplicant must already hold New Zealand work authorization.'
            session.commit()
            ensure_profile_match_indexes(session, [session.get(models.UserProfile, self.profile_id)])
            session.commit()
        self.assertTrue(self.refresh(assessment(91)))
        with self.factory() as session:
            result = api.get_job(self.job_id, session=session, profile_id=self.profile_id)['profile_match']
        self.assertEqual(result['score_source'], 'llm')
        self.assertEqual(result['score'], 91)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['fit_tier'], 'possible')
        self.assertTrue(result['mandatory_review'])
        self.assertTrue(any('authorization' in item.lower() for item in result['unknowns']))

    def test_ai_pass_cannot_contradict_known_local_language_threshold_failure(self):
        service = self.service()
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            profile.preferences = {**profile.preferences, 'language_levels': {'German': 'B2'}}
            job = session.get(models.Job, self.job_id)
            job.description += '\nGerman C1 required.'
            session.commit()
            ensure_profile_match_indexes(session, [profile])
            session.commit()
            session.add(models.ProfileLLMScore(profile_id=self.profile_id, job_id=self.job_id,
                source_hash=service.source_hash(job, profile), version=service.VERSION, assessment=assessment(91)))
            session.commit()
        with self.factory() as session:
            result = api.get_job(self.job_id, session=session, profile_id=self.profile_id)['profile_match']
        self.assertFalse(result['eligible'])
        self.assertEqual(result['llm_match']['gates']['language']['status'], 'fail')

    def test_repaired_unknowns_are_persisted_instead_of_uncited_claims(self):
        data = assessment()
        data['requirements'] = [{
            'requirement': 'Python services', 'priority': 'required', 'importance': 'core', 'status': 'supported',
            'job_source': 'job.description', 'job_quote': 'Build Python services and SQL databases.',
            'evidence_source': 'profile.summary', 'evidence_quote': 'Invented achievement.',
            'explanation': 'Unverifiable claim.'}]
        repaired = copy.deepcopy(data)
        repaired['requirements'][0].update(status='unknown', evidence_source='', evidence_quote='',
                                            explanation='No verified applicant excerpt was supplied.')
        repaired['strengths'] = []
        replies = iter([data, repaired])
        self.assertTrue(self.refresh(generate=lambda *args, **kwargs: next(replies)))
        with self.factory() as session:
            match = api.get_job(self.job_id, session=session, profile_id=self.profile_id)['profile_match']
            self.assertEqual(match['llm_match']['requirements'][0]['status'], 'unknown')
            self.assertEqual(match['llm_match']['requirement_completeness'], 0)
            self.assertTrue(match['uncertain'])

    def test_required_gaps_remain_visible_even_when_dimension_score_is_high(self):
        data = assessment(95)
        data['requirements'][0].update(status='missing', requirement='Required leadership experience')
        self.assertTrue(self.refresh(data))
        with self.factory() as session:
            match = api.get_job(self.job_id, session=session, profile_id=self.profile_id)['profile_match']
            self.assertIn('Required leadership experience', match['requirement_gaps'])
            self.assertLess(match['score'], 65)
            self.assertEqual(match['fit_tier'], 'unlikely')

    def test_high_saved_minimum_allows_grounded_local_fit_and_ai_preselection(self):
        with self.factory() as session:
            profiles.update_profile(self.profile_id, profiles.ProfileRequest(**{
                **PROFILE, 'matching_defaults': {'min_match_score': 65}}), session)
            page = api.list_applications(profile_ids=str(self.profile_id), statuses='new', sort='match',
                                         offset=0, limit=40, session=session)
            self.assertEqual(len(page['items']), 1)
            self.assertGreaterEqual(page['items'][0]['profile_match']['score'], 65)
            self.assertEqual(page['items'][0]['profile_match']['score_source'], 'local')
        self.assertTrue(self.refresh(assessment(91)))
        with self.factory() as session:
            page = api.list_applications(profile_ids=str(self.profile_id), statuses='new', sort='match',
                                         offset=0, limit=40, session=session)
            self.assertEqual(page['items'][0]['profile_match']['score'], 91)

    def test_local_results_are_provisional_shortlist_estimates(self):
        with self.factory() as session:
            match = api.get_job(self.job_id, session=session, profile_id=self.profile_id)['profile_match']
            self.assertEqual(match['score_source'], 'local')
            self.assertGreaterEqual(match['score'], 65)
            self.assertNotEqual(match['fit_tier'], 'recommended')

    def test_ai_cannot_clear_unverified_required_academic_scope(self):
        self.assertTrue(self.refresh(assessment(91)))
        with self.factory() as session:
            service = self.service()
            job = session.get(models.Job, self.job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            documents = service.load_matching_documents(session, [job], [profile])
            result = service.apply_cached_assessment({'score': 40, 'eligible': True,
                'fit_tier': 'possible', 'requirement_observations': [
                    {'kind': 'academic', 'status': 'unknown', 'requirement': 'Required academic tool exposure'}]},
                job, profile, documents)
            self.assertLessEqual(result['score'], 59)
            self.assertNotEqual(result['fit_tier'], 'recommended')
            self.assertTrue(result['uncertain'])

    def test_unknown_task_dimensions_survive_academic_scope_ceiling(self):
        data = assessment()
        for dimension in data['dimensions'].values():
            dimension['score'] = None
        self.assertTrue(self.refresh(data))
        with self.factory() as session:
            service = self.service()
            job = session.get(models.Job, self.job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            documents = service.load_matching_documents(session, [job], [profile])
            result = service.apply_cached_assessment({'score': 40, 'eligible': True,
                'fit_tier': 'possible', 'requirement_observations': [
                    {'kind': 'academic', 'status': 'unknown'}]}, job, profile, documents)
            self.assertIsNone(result['llm_match']['qualification_fit'])
            self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_sparse_ai_assessment_does_not_invent_requirements(self):
        with self.factory.begin() as session:
            session.get(models.Job, self.job_id).description = 'Details to follow.'
        data = assessment(95)
        data['requirements'] = []
        self.assertTrue(self.refresh(data))
        with self.factory() as session:
            match = api.get_job(self.job_id, session=session, profile_id=self.profile_id)['profile_match']
            self.assertEqual(match['score'], 0)
            self.assertTrue(match['uncertain'])

    def test_arithmetic_version_invalidates_ranking_without_discarding_grounded_ai_evidence(self):
        from hiring_scraper.app import job_search
        self.assertTrue(self.refresh())
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            ensure_profile_match_indexes(session, [profile]); session.commit()
            with patch.object(job_search, 'REQUIREMENT_SCORING_VERSION', 'next-arithmetic'):
                self.assertTrue(job_search.profile_match_index_stale(profile))
                result = api.get_job(self.job_id, session=session, profile_id=self.profile_id)
                self.assertEqual(result['profile_match']['score_source'], 'llm')
                self.assertTrue(result['profile_match']['llm_match']['role_evidence_groups'])

    def test_model_configuration_change_cannot_relabel_a_cached_provider_judgment(self):
        self.assertTrue(self.refresh())
        service = self.service()
        spec = importlib.util.spec_from_file_location('isolated_matching_model', service.__file__)
        other = importlib.util.module_from_spec(spec)
        with patch.dict('os.environ', {'RADIUS_MATCH_AI_MODEL': 'gpt-6-luna',
                                      'RADIUS_MATCH_AI_REASONING_EFFORT': 'medium'}):
            spec.loader.exec_module(other)
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            docs = other.load_matching_documents(session, [job], [profile])
            result = other.apply_cached_assessment({'score': 40, 'eligible': True}, job, profile, docs)
        self.assertEqual(result['score_source'], 'local')
        self.assertIsNone(result['llm_match'])

    def test_status_uses_the_persisted_worker_selection_after_process_cache_is_lost(self):
        self.assertTrue(self.refresh())
        service = self.service()
        service._SELECTION_CACHE.clear()
        with patch.object(service, 'SessionLocal', self.factory), patch.object(service.codex_runtime, 'subscription_usage', return_value=USAGE), patch.object(service, '_selected_job_ids', side_effect=AssertionError('Inventory matching on status read')):
            status = service.matching_status()
        self.assertFalse(status['pending'])
        self.assertEqual(status['state'], 'ready')

    def test_status_never_runs_inventory_matching_or_builds_a_prompt(self):
        service = self.service()
        with patch.object(service, 'SessionLocal', self.factory), patch.object(service.codex_runtime, 'subscription_usage', return_value=USAGE), patch.object(service, '_selected_job_ids', side_effect=AssertionError('Inventory matching on status read')), patch.object(service, 'build_prompt', side_effect=AssertionError('Prompt on status read')):
            self.assertTrue(service.matching_status()['pending'])

    def test_status_allows_matching_above_half_subscription_usage(self):
        service = self.service()
        quota = {**USAGE, 'allowed': True, 'used_percent': 75}
        with patch.object(service, 'SessionLocal', self.factory), patch.object(service.codex_runtime, 'subscription_usage', return_value=quota):
            self.assertEqual(service.matching_status()['state'], 'pending')
    def test_saved_journey_role_is_assessed_without_derived_index(self):
        with self.factory.begin() as session:
            session.query(models.ProfileJobMatch).delete()
            session.get(models.UserProfile, self.profile_id).match_index_version = None
            session.query(models.JobApplication).filter_by(profile_id=self.profile_id, job_id=self.job_id).update({'status': 'open'})
        self.assertTrue(self.refresh())
        with self.factory() as session:
            self.assertIsNotNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))

    def test_saved_role_fallback_still_checks_eligibility_before_inference(self):
        with self.factory.begin() as session:
            session.query(models.ProfileJobMatch).delete()
        service = self.service()
        with patch.object(service, 'match_job', return_value={'eligible': False, 'score': 59}):
            self.assertFalse(self.refresh(generate=lambda *args, **kwargs: self.fail('Ineligible role reached AI')))

    def test_status_is_pending_while_match_index_is_missing(self):
        with self.factory.begin() as session:
            session.query(models.ProfileJobMatch).delete()
            session.get(models.UserProfile, self.profile_id).match_index_version = None
        service = self.service()
        with patch.object(service, 'SessionLocal', self.factory), patch.object(service.codex_runtime, 'subscription_usage', return_value=USAGE):
            status = service.matching_status()
        self.assertEqual(status['state'], 'pending')
        self.assertTrue(status['indexing_pending'])

    def test_status_exposes_queue_and_success_without_claiming_worker_health(self):
        service = self.service()
        with patch.object(service, 'SessionLocal', self.factory), patch.object(service.codex_runtime, 'subscription_usage', return_value=USAGE):
            before = service.matching_status()
            self.assertEqual(before['assessed_pairs'], 0)
            self.assertEqual(before['queued_pairs'], 1)
            self.assertIsNone(before['last_assessed_at'])
            self.assertEqual(before['worker_health'], 'unknown')
            self.assertTrue(self.refresh())
            after = service.matching_status()
            self.assertEqual(after['assessed_pairs'], 1)
            self.assertEqual(after['queued_pairs'], 0)
            self.assertIsNotNone(after['last_assessed_at'])

    def test_status_does_not_queue_expired_jobs(self):
        with self.factory.begin() as session:
            session.get(models.Job, self.job_id).raw_metadata = {'validThrough': '2000-01-01'}
        service = self.service()
        with patch.object(service, 'SessionLocal', self.factory), patch.object(service.codex_runtime, 'subscription_usage', return_value=USAGE):
            status = service.matching_status()
            self.assertEqual(status['queued_pairs'], 0)
            self.assertFalse(status['pending'])

    def test_status_reports_pending_for_stale_cache_even_without_uncached_jobs(self):
        self.assertTrue(self.refresh())
        with self.factory.begin() as session:
            session.execute(models.Job.__table__.update().where(models.Job.id == self.job_id)
                            .values(description=DESCRIPTION + ' Updated duties.'))
        service = self.service()
        with patch.object(service, 'SessionLocal', self.factory), patch.object(service.codex_runtime, 'subscription_usage', return_value=USAGE):
            self.assertEqual(service.matching_status()['state'], 'pending')

    def test_stale_cache_refresh_is_not_starved_by_uncached_jobs(self):
        self.assertTrue(self.refresh())
        with self.factory.begin() as session:
            # Raw SQL deliberately bypasses ordinary ORM cache invalidation.
            session.execute(models.Job.__table__.update().where(models.Job.id == self.job_id)
                            .values(description=DESCRIPTION + ' New core duties.'))
            original = session.get(models.Job, self.job_id)
            for i in range(4):
                session.add(models.Job(feed_id=original.feed_id, title='Software developer', description=DESCRIPTION,
                    external_id=f'queued-{i}', url=f'https://example.org/jobs/queued-{i}', is_remote=True))
        with self.factory.begin() as session:
            ensure_profile_match_indexes(session, [session.get(models.UserProfile, self.profile_id)])
            from hiring_scraper.app.job_search import refresh_application_matches_for_jobs
            refresh_application_matches_for_jobs(session, list(session.scalars(select(models.Job.id))))
        service = self.service()
        with patch.object(service, 'SessionLocal', self.factory), patch.object(service.codex_runtime, 'subscription_usage', return_value=USAGE):
            self.assertTrue(service.matching_status()['pending'])
        self.assertTrue(self.refresh(assessment(82)))
        self.assertFalse(self.refresh(assessment(82)))
        with self.factory() as session:
            row = session.get(models.ProfileLLMScore, (self.profile_id, self.job_id))
            self.assertEqual(row.source_hash, service.source_hash(session.get(models.Job, self.job_id),
                                                                session.get(models.UserProfile, self.profile_id)))

    def test_unchanged_cache_never_runs_inference_and_contacts_do_not_invalidate(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            profiles.update_profile(self.profile_id, profiles.ProfileRequest(**PROFILE,
                application_details={'email': 'secret@example.org'}), session)
            self.assertFalse(profile_match_index_stale(session.get(models.UserProfile, self.profile_id)))
        def forbidden(*args, **kwargs):
            self.fail('Unchanged assessment must not call inference')
        self.assertFalse(self.refresh(generate=forbidden))

    def test_capture_bookkeeping_does_not_change_assessment_hash_or_invalidate_cache(self):
        service = self.service()
        with self.factory.begin() as session:
            job = session.get(models.Job, self.job_id)
            job.raw_metadata = {
                'source': 'arbeitsagentur', 'source_observed_at': '2026-10-01T08:00:00Z',
                'source_api_url': 'https://api.example.test/detail/one',
                'search_terms': ['Software developer'], 'detail_state': 'captured',
                'source_record': {'referenznummer': 'one', 'stellenangebotsTitel': 'Software developer',
                                  '_checked_at': '2026-10-01T08:00:00Z',
                                  '_detail_url': 'https://api.example.test/detail/one',
                                  '_search_terms': ['Software developer']},
            }
        self.assertTrue(self.refresh(assessment(91)))
        with self.factory.begin() as session:
            job = session.get(models.Job, self.job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            cached_hash = service.source_hash(job, profile)
            metadata = dict(job.raw_metadata)
            metadata.update(source_observed_at='2026-10-02T08:00:00Z',
                            source_api_url='https://api.example.test/detail/one?capture=2',
                            search_terms=['Software engineer'], detail_state='refreshed')
            metadata['source_record'] = {
                **metadata['source_record'], '_checked_at': '2026-10-02T08:00:00Z',
                '_detail_url': 'https://api.example.test/detail/one?capture=2',
                '_search_terms': ['Software engineer'],
            }
            job.raw_metadata = metadata

        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            self.assertEqual(service.source_hash(job, profile), cached_hash)
            self.assertIsNotNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))

        def forbidden(*args, **kwargs):
            self.fail('Capture bookkeeping alone must not cause another assessment')
        self.assertFalse(self.refresh(generate=forbidden))

    def test_career_goals_invalidate_llm_without_changing_local_projection(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            before = profile.matching_preferences
            profiles.update_profile(self.profile_id, profiles.ProfileRequest(**PROFILE,
                application_personalization={'career_goals': 'Lead technical teams.'}), session)
            self.assertEqual(profile.matching_preferences, before)
            self.assertTrue(profile_match_index_stale(profile))
            detail = api.get_job(self.job_id, session=session, profile_id=self.profile_id)
            self.assertEqual(detail['profile_match']['score_source'], 'local')
        self.assertTrue(self.refresh())

    def test_explicit_zero_experience_is_prompted_hashed_and_invalidates_cached_assessment(self):
        service = self.service()
        self.assertTrue(self.refresh(assessment(91)))
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            job = session.get(models.Job, self.job_id)
            unknown_hash = service.source_hash(job, profile)
            self.assertNotIn('experience_years', service.profile_data(profile))

            profiles.update_profile(self.profile_id, profiles.ProfileRequest(
                **PROFILE, experience_years=0), session)

            profile = session.get(models.UserProfile, self.profile_id)
            job = session.get(models.Job, self.job_id)
            self.assertEqual(service.profile_data(profile)['experience_years'], 0)
            self.assertNotEqual(service.source_hash(job, profile), unknown_hash)
            self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))
            self.assertIn('experience_years', service.build_prompt(job, profile))
            ensure_profile_match_indexes(session, [profile])
            session.commit()

        self.assertTrue(self.refresh(assessment(72)))
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            job = session.get(models.Job, self.job_id)
            zero_hash = service.source_hash(job, profile)
            self.assertEqual(profile.preferences['experience_years'], 0)

            profiles.update_profile(self.profile_id, profiles.ProfileRequest(**PROFILE), session)

            profile = session.get(models.UserProfile, self.profile_id)
            job = session.get(models.Job, self.job_id)
            self.assertNotIn('experience_years', service.profile_data(profile))
            self.assertNotEqual(service.source_hash(job, profile), zero_hash)
            self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))
            ensure_profile_match_indexes(session, [profile])
            session.commit()

    def test_source_changes_during_inference_discard_job_or_profile_snapshot(self):
        for kind in ['job', 'profile']:
            def generate(prompt, schema, **kwargs):
                with self.factory() as session:
                    if kind == 'job':
                        session.get(models.Job, self.job_id).description += '\nChanged duties.'
                    else:
                        profile = session.get(models.UserProfile, self.profile_id)
                        profile.preferences = {**profile.preferences, 'application_personalization': {'career_goals': 'Research systems.'}}
                    session.commit()
                return assessment()
            with self.subTest(kind=kind):
                self.assertFalse(self.refresh(generate=generate))
                with self.factory() as session:
                    self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))

    def test_worker_allows_usage_above_fifty(self):
        self.assertTrue(self.refresh(generate=lambda *a, **kw: assessment(),
                                    usage=lambda: {'allowed': True, 'used_percent': 75}))

    def test_rematch_requeues_saved_assessments(self):
        self.assertTrue(self.refresh())
        service = self.service()
        with patch.object(service, 'SessionLocal', self.factory):
            result = service.requeue_all_assessments()
        self.assertEqual(result['requeued_assessments'], 1)
        with self.factory() as session:
            self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))
        self.assertTrue(self.refresh())

    def test_rematch_rebuilds_local_scores_after_low_ai_score(self):
        self.assertTrue(self.refresh(data=assessment(score=12)))
        service = self.service()
        with patch.object(service, 'SessionLocal', self.factory):
            service.requeue_all_assessments()
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            self.assertIsNone(profile.match_index_version)
            ensure_profile_match_indexes(session, [profile])
            session.commit()
        self.assertTrue(self.refresh())

    def test_rematch_does_not_race_an_inflight_assessment(self):
        service = self.service()
        with self.factory() as session:
            engine = session.get_bind()
        with patch.object(service, 'SessionLocal', self.factory), service._inference_lease(engine):
            with self.assertRaises(api.HTTPException) as raised:
                service.requeue_all_assessments()
        self.assertEqual(raised.exception.status_code, 409)

    def test_usage_block_and_disconnection_never_start_inference(self):
        def forbidden(*args, **kwargs):
            self.fail('Blocked quota must not start inference')
        for state in [{'state': 'paused', 'allowed': False, 'used_percent': 50},
                      {'state': 'disconnected', 'allowed': False}]:
            with self.subTest(state=state):
                self.assertFalse(self.refresh(generate=forbidden, usage=lambda: state))

    def test_prompt_is_private_bounded_and_keeps_late_requirements(self):
        service = self.service()
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            profile.preferences = {**profile.preferences,
                'application_details': {'email': 'secret@example.org', 'birth_date': '1970-01-01',
                    'additional_information': 'Unrelated confidential note',
                    'experiences': [{'title': 'Engineer', 'description': 'Built services'}],
                    'preferences': {'authorization_country': 'Germany', 'sponsorship_required': True}},
                'application_personalization': {'career_goals': 'Grow as an engineer', 'writing_samples': 'PRIVATE SAMPLE'}}
            job = session.get(models.Job, self.job_id)
            job.description = 'About us:\n' + 'A large company. ' * 10000 + '\nRequirements:\nGerman C1 required.'
            prompt = service.build_prompt(job, profile)
            self.assertLess(len(prompt), 55000)
            for secret in ['secret@example.org', '1970-01-01', 'Unrelated confidential note', 'PRIVATE SAMPLE']:
                self.assertNotIn(secret, prompt)
            for fact in ['Grow as an engineer', 'German C1 required', 'Built services', 'sponsorship_required']:
                self.assertIn(fact, prompt)
            self.assertIn('truncated', prompt.lower())

    def test_malformed_candidate_backoff_does_not_starve_next_candidate(self):
        with self.factory() as session:
            original = session.get(models.Job, self.job_id)
            job = models.Job(feed_id=original.feed_id, title='Software developer', description=DESCRIPTION,
                external_id='two', url='https://example.org/jobs/two', is_remote=True)
            session.add(job); session.commit(); second_id = job.id
            for i in range(18):
                session.add(models.Job(feed_id=original.feed_id, title='Software developer',
                    description=DESCRIPTION, external_id=f'pool-{i}',
                    url=f'https://example.org/jobs/pool-{i}', is_remote=True))
            session.commit()
            ensure_profile_match_indexes(session, [session.get(models.UserProfile, self.profile_id)])
            # Source addition requires incremental refresh when profile is already indexed.
            from hiring_scraper.app.job_search import refresh_application_matches_for_jobs
            refresh_application_matches_for_jobs(session, [second_id]); session.commit()
        def malformed(prompt, schema, **kwargs):
            return {'score': 100}
        with self.assertLogs("hiring_scraper.app.profile_llm", level="WARNING"):
            self.assertFalse(self.refresh(generate=malformed))
        self.assertTrue(self.refresh())
        with self.factory() as session:
            rows = session.scalars(select(models.ProfileLLMScore)).all()
            self.assertEqual(len(rows), 1)
            self.assertNotEqual(rows[0].job_id, self.job_id)

    def test_ai_preselection_requires_current_static_score_strictly_over_35(self):
        service = self.service()
        real_match = service.match_job
        for score in (30, 35):
            service._SELECTION_CACHE.clear()  # Independent background-selection ticks.
            calls = []
            with patch.object(service, 'match_job', side_effect=lambda *a, **kw: {**real_match(*a, **kw), 'score': score}):
                self.assertFalse(self.refresh(generate=lambda *a, **kw: calls.append(kw) or assessment()))
            self.assertEqual(calls, [])
            with patch.object(service, 'SessionLocal', self.factory), patch.object(service.codex_runtime, 'subscription_usage', return_value=USAGE):
                self.assertEqual(service.matching_status()['queued_pairs'], 0)
        service._SELECTION_CACHE.clear()
        with patch.object(service, 'match_job', side_effect=lambda *a, **kw: {**real_match(*a, **kw), 'score': 36}):
            self.assertTrue(self.refresh())

    def test_ai_preselection_rechecks_current_base_score(self):
        service = self.service()
        calls = []
        with patch.object(service, 'match_job', return_value={'eligible': True, 'score': 35}):
            self.assertFalse(self.refresh(generate=lambda *a, **kw: calls.append(kw) or assessment()))
        self.assertEqual(calls, [])
        service._SELECTION_CACHE.clear()  # The next scheduled static-pool refresh.
        self.assertTrue(self.refresh())

    def test_ineligible_closed_and_implausible_candidates_are_not_scored(self):
        with self.factory() as session:
            original = session.get(models.Job, self.job_id)
            original.is_active = False
            session.add(models.Job(feed_id=original.feed_id, title='Nurse', description='Provide bedside care.',
                external_id='nurse', url='https://example.org/nurse', is_remote=True))
            session.commit()
        def forbidden(*args, **kwargs):
            self.fail('No plausible active candidate')
        self.assertFalse(self.refresh(generate=forbidden))

    def test_low_local_score_stays_outside_ai_pool_despite_unproven_specialist_similarity(self):
        description = 'Your responsibilities:\nImprove relational query plans and diagnose data access bottlenecks.'
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            job.title, job.description = 'Data Operations Specialist', description
            session.commit()
            ensure_profile_match_indexes(session, [session.get(models.UserProfile, self.profile_id)])
            session.commit()
            from hiring_scraper.app.enrichment import job_input
            profile = session.get(models.UserProfile, self.profile_id)
            self.assertLess(self.service().match_job(job_input(job), profile.matching_preferences)['score'], 30)
        data = assessment()
        data['requirements'][0].update(requirement='Relational query tuning',
            job_quote='Improve relational query plans and diagnose data access bottlenecks.')
        self.assertFalse(self.refresh(data))
        self.service()._SELECTION_CACHE.clear()
        semantic = {'score': 80, 'job_evidence': 'Relational query plans.', 'profile_evidence': 'SQL databases.'}
        with patch('hiring_scraper.app.profile_semantics.cached_semantic_evidence', return_value=semantic):
            self.assertFalse(self.refresh(data))
        with self.factory() as session:
            self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))

    def test_exploration_still_respects_known_language_incompatibility(self):
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            profile.preferences = {**profile.preferences, 'language_levels': {'German': 'B2'}}
            job = session.get(models.Job, self.job_id)
            job.description = 'German C1 required. Handle specialized platform setup requests.'
            job.title = 'Platform Liaison'
            session.commit()
            ensure_profile_match_indexes(session, [profile])
            session.commit()
        def forbidden(*args, **kwargs):
            self.fail('Explicit incompatibility must not consume inference capacity')
        self.assertFalse(self.refresh(generate=forbidden))

    def test_country_agnostic_authorization_flags_are_not_matching_evidence(self):
        service = self.service()
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            profile.preferences = {**profile.preferences, 'application_details': {'preferences': {
                'authorized_to_work': True, 'sponsorship_required': True, 'willing_to_work_onsite': True}}}
            prompt = service.build_prompt(session.get(models.Job, self.job_id), profile)
            data = json.loads(prompt.split('\nDATA:\n')[1])
            self.assertNotIn('authorized_to_work', data['profile']['application_preferences'])
            self.assertNotIn('profile.application_preferences.sponsorship_required', data['evidence_sources'])
            self.assertTrue(data['profile']['application_preferences']['willing_to_work_onsite'])
            self.assertTrue(profile.preferences['application_details']['preferences']['sponsorship_required'])

    def test_named_authorization_remains_citable_in_matching_context(self):
        service = self.service()
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            profile.preferences = {**profile.preferences, 'application_details': {'preferences': {
                'authorization_country': 'New Zealand', 'authorized_to_work': True, 'sponsorship_required': False}}}
            data = json.loads(service.build_prompt(session.get(models.Job, self.job_id), profile).split('\nDATA:\n')[1])
            self.assertEqual(data['evidence_sources']['profile.application_preferences.authorization_country'], 'New Zealand')
            self.assertEqual(data['evidence_sources']['profile.application_preferences.authorized_to_work'], 'True')

    def test_cv_language_evidence_is_citable_and_invalidates_assessment_hash(self):
        service = self.service()
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            text = r'\begin{document}\section*{Languages}French A2.\end{document}'
            profile.cv = models.ProfileCV(filename='cv.tex', content_type='application/x-tex', size_bytes=len(text),
                data=text.encode(), extracted_text=text, variants={'en': {'text': text}})
            job = session.get(models.Job, self.job_id)
            before = service.source_hash(job, profile)
            self.assertEqual(service.assessment_sources(job, profile)['profile.cv_language_levels.French'], 'A2')
            profile.cv.variants = {'en': {'text': text.replace('A2', 'B1')}}
            self.assertNotEqual(service.source_hash(job, profile), before)
            self.assertNotIn('French', profile.preferences.get('language_levels', {}))

    def test_ai_pass_cannot_clear_unverified_required_language_level(self):
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            job.description += '\nDutch B2 required.'
            session.commit()
            ensure_profile_match_indexes(session, [session.get(models.UserProfile, self.profile_id)])
            session.commit()
        self.assertTrue(self.refresh(assessment(91)))
        with self.factory() as session:
            result = api.get_job(self.job_id, session=session, profile_id=self.profile_id)['profile_match']
        self.assertEqual(result['gates']['language']['status'], 'unknown')
        self.assertEqual(result['fit_tier'], 'possible')

    def test_assessment_reads_are_batched_on_unindexed_cards(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            jobs = session.scalars(select(models.Job)).all()
            profile = session.get(models.UserProfile, self.profile_id)
            statements = []
            def observe(conn, cursor, statement, parameters, context, many):
                if 'FROM profile_llm_scores' in statement:
                    statements.append(statement)
            event.listen(self.engine, 'before_cursor_execute', observe)
            try:
                page = api._jobs_page(jobs, 'match', 0, 40, 49., 8.4, 35, profile, 0, True,
                    precomputed_scopes={job.id: {'eligible': True, 'reason': None, 'unknowns': [], 'match_kind': 'remote'} for job in jobs})
            finally:
                event.remove(self.engine, 'before_cursor_execute', observe)
            self.assertEqual(page['items'][0]['profile_match']['score'], 90)
            self.assertEqual(len(statements), 1)

    def test_goal_edit_locks_profile_before_deleting_cached_assessment(self):
        self.assertTrue(self.refresh())
        operations = []
        def observe(conn, cursor, statement, parameters, context, many):
            compiled = getattr(context, 'compiled', None)
            query = getattr(compiled, 'statement', None)
            if getattr(query, '_for_update_arg', None) is not None and 'user_profiles' in statement:
                operations.append('profile_lock')
            if statement.startswith('DELETE FROM profile_llm_scores'):
                operations.append('cache_delete')
        event.listen(self.engine, 'before_cursor_execute', observe)
        try:
            with self.factory() as session:
                profiles.update_profile(self.profile_id, profiles.ProfileRequest(**PROFILE,
                    application_personalization={'career_goals': 'Lead a research team.'}), session)
        finally:
            event.remove(self.engine, 'before_cursor_execute', observe)
        self.assertIn('profile_lock', operations)
        self.assertIn('cache_delete', operations)
        self.assertLess(operations.index('profile_lock'), operations.index('cache_delete'))

    def test_external_scope_uncertainty_is_preserved_after_llm_authority_on_every_read(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            indexed = session.get(models.ProfileJobMatch, (self.profile_id, self.job_id))
            scope = {'eligible': True, 'reason': None,
                     'unknowns': ['Work location is not verified'], 'match_kind': 'remote'}
            with patch.object(api, '_result_scope', return_value=scope):
                detail = api.get_job(self.job_id, session=session, profile_id=self.profile_id,
                                     latitude=49., longitude=8.4, radius_km=35)['profile_match']
            self.assertEqual(detail['fit_tier'], 'possible')
            self.assertTrue(detail['uncertain'])
            for indexed_matches in [None, {job.id: indexed}]:
                with self.subTest(indexed=indexed_matches is not None):
                    page = api._jobs_page([job], 'match', 0, 40, 49., 8.4, 35, profile, 0, True,
                        indexed_matches=indexed_matches, precomputed_scopes={job.id: scope})
                    match = page['items'][0]['profile_match']
                    self.assertEqual(match['score'], 90)
                    self.assertEqual(match['fit_tier'], detail['fit_tier'])
                    self.assertEqual(match['uncertain'], detail['uncertain'])
                    self.assertIn('Work location is not verified', match['unknowns'])
                    hidden = api._jobs_page([job], 'match', 0, 40, 49., 8.4, 35, profile, 0, False,
                        indexed_matches=indexed_matches, precomputed_scopes={job.id: scope})
                    self.assertEqual(hidden['total'], 0)

    def test_matching_status_is_reachable_through_actual_asgi_routing(self):
        service = self.service()
        quota = {'available': True, 'allowed': False, 'reason': 'usage_limit_reached',
                 'used_percent': 50, 'resets_at': 1800000000, 'window_minutes': 300,
                 'limit_percent': 50, 'private_token': 'must-not-be-exposed'}
        async def request():
            messages = []
            async def receive():
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            async def send(message):
                messages.append(message)
            await api.app({'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
                'http_version': '1.1', 'method': 'GET', 'scheme': 'http',
                'path': '/api/v1/profile-matching/status', 'raw_path': b'/api/v1/profile-matching/status',
                'query_string': b'', 'root_path': '', 'headers': [],
                'server': ('test', 80), 'client': ('test', 1234)}, receive, send)
            status = next(item['status'] for item in messages if item['type'] == 'http.response.start')
            body = b''.join(item.get('body', b'') for item in messages if item['type'] == 'http.response.body')
            return status, json.loads(body)
        with patch.object(service, 'SessionLocal', self.factory), \
             patch.object(service.codex_runtime, 'subscription_usage', return_value=quota):
            status, body = asyncio.run(request())
        self.assertEqual(status, 200)
        self.assertEqual(body['state'], 'paused')
        self.assertEqual(body['usage']['reason'], 'usage_limit_reached')
        self.assertEqual(body['usage']['used_percent'], 50)
        self.assertNotIn('private_token', body['usage'])

    def test_indexed_cards_include_assessment_provenance(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            indexed = session.get(models.ProfileJobMatch, (self.profile_id, self.job_id))
            page = api._jobs_page([job], 'match', 0, 40, 49., 8.4, 35, profile, 0, True,
                indexed_matches={job.id: indexed}, precomputed_scopes={job.id: {
                    'eligible': True, 'reason': None, 'unknowns': [], 'match_kind': 'remote'}})
            self.assertEqual(page['items'][0]['profile_match']['score_source'], 'llm')
            self.assertEqual(page['items'][0]['profile_match']['llm_match']['dimensions']['skills']['score'], 90)

    def test_independent_memory_databases_do_not_contend_for_inference_lease(self):
        service = self.service()
        for url in ['sqlite://', 'sqlite:///:memory:']:
            first, second = create_engine(url, poolclass=StaticPool), create_engine(url, poolclass=StaticPool)
            try:
                with self.subTest(url=url), service._inference_lease(first) as acquired:
                    self.assertTrue(acquired)
                    with service._inference_lease(first) as same_database:
                        self.assertFalse(same_database)
                    with service._inference_lease(second) as distinct_database:
                        self.assertTrue(distinct_database)
            finally:
                first.dispose(); second.dispose()

    def test_named_shared_memory_database_engines_share_data_and_inference_lease(self):
        service = self.service()
        url = f'sqlite:///file:llm-{uuid4().hex}?mode=memory&cache=shared&uri=true'
        first, second = create_engine(url, poolclass=StaticPool), create_engine(url, poolclass=StaticPool)
        try:
            with first.begin() as connection:
                connection.exec_driver_sql('CREATE TABLE shared_facts (value INTEGER)')
                connection.exec_driver_sql('INSERT INTO shared_facts VALUES (42)')
            with second.connect() as connection:
                self.assertEqual(connection.exec_driver_sql('SELECT value FROM shared_facts').scalar_one(), 42)
            with service._inference_lease(first) as acquired:
                self.assertTrue(acquired)
                with service._inference_lease(second) as same_database:
                    self.assertFalse(same_database)
        finally:
            first.dispose(); second.dispose()

    def test_named_private_memory_database_engines_have_independent_leases(self):
        service = self.service()
        url = f'sqlite:///file:llm-{uuid4().hex}?mode=memory&cache=private&uri=true'
        first, second = create_engine(url, poolclass=StaticPool), create_engine(url, poolclass=StaticPool)
        try:
            with service._inference_lease(first) as acquired:
                self.assertTrue(acquired)
                with service._inference_lease(second) as distinct_database:
                    self.assertTrue(distinct_database)
        finally:
            first.dispose(); second.dispose()

    def test_memory_query_without_uri_flag_keeps_file_database_lease_shared(self):
        service = self.service()
        with tempfile.TemporaryDirectory() as folder:
            url = 'sqlite:///' + str(Path(folder) / 'file:ordinary.db') + '?mode=memory&cache=shared'
            # This intentionally ignored query models a real filename, not a URI.
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', message='Query string argument.*not accepted.*', category=SAWarning)
                first, second = create_engine(url, poolclass=StaticPool), create_engine(url, poolclass=StaticPool)
            try:
                with service._inference_lease(first) as acquired:
                    self.assertTrue(acquired)
                    with service._inference_lease(second) as same_database:
                        self.assertFalse(same_database)
            finally:
                first.dispose(); second.dispose()

    def test_same_file_database_engines_share_inference_lease(self):
        service = self.service()
        with tempfile.TemporaryDirectory() as folder:
            url = 'sqlite:///' + str(Path(folder) / 'shared.db')
            first, second = create_engine(url, poolclass=StaticPool), create_engine(url, poolclass=StaticPool)
            try:
                with service._inference_lease(first) as acquired:
                    self.assertTrue(acquired)
                    with service._inference_lease(second) as same_database:
                        self.assertFalse(same_database)
            finally:
                first.dispose(); second.dispose()

    def test_inference_lease_prevents_concurrent_duplicate_work(self):
        calls = []
        def generate(prompt, schema, **kwargs):
            calls.append(kwargs)
            self.assertFalse(self.refresh(generate=lambda *a, **kw: self.fail('Duplicate inference')))
            return assessment()
        self.assertTrue(self.refresh(generate=generate))
        self.assertEqual(calls, [{'model': 'gpt-6.1-sol', 'reasoning_effort': 'high', 'usage_limit_percent': 100}])

    def test_full_rebuild_rejects_concurrently_changed_assessment(self):
        self.assertTrue(self.refresh())
        service = self.service()
        from hiring_scraper.app import job_search
        changed = False
        original = job_search.match_job
        def match(*args, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                with self.factory() as writer:
                    row = writer.get(models.ProfileLLMScore, (self.profile_id, self.job_id))
                    row.assessment = assessment(1)
                    writer.commit()
            return original(*args, **kwargs)
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            profile.match_index_version = None; session.commit()
            with patch.object(job_search, 'match_job', match), self.assertRaises(job_search.ProfileIndexChanged):
                ensure_profile_match_indexes(session, [profile])

    def test_cv_career_facts_are_used_without_contacts_and_fact_edits_invalidate(self):
        service = self.service()
        from tests.test_tex_cv import source
        cv_text = source().replace('Built research tools.', 'Built Python research systems.')
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            session.add(models.ProfileCV(profile=profile, filename='cv.tex', content_type='text/plain',
                size_bytes=len(cv_text), data=cv_text.encode(), extracted_text=cv_text,
                variants={'en': {'text': cv_text, 'filename': 'cv.tex'}}))
            session.commit()
        def generate(prompt, schema, **kwargs):
            self.assertIn('Built Python research systems.', prompt)
            self.assertNotIn('Alex Example', prompt)
            return assessment()
        self.assertTrue(self.refresh(generate=generate))
        with self.factory() as session:
            cv = session.get(models.ProfileCV, self.profile_id)
            cv.extracted_text = cv_text.replace('Python research', 'Clinical research')
            cv.variants = {'en': {'text': cv.extracted_text, 'filename': 'cv.tex'}}
            session.commit()
            self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))
            self.assertTrue(profile_match_index_stale(session.get(models.UserProfile, self.profile_id)))

    def test_prompt_bounds_wide_metadata_and_profile_lists(self):
        service = self.service()
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            job.raw_metadata = {f'key-{index}': 'content ' * 100 for index in range(1500)}
            profile = session.get(models.UserProfile, self.profile_id)
            prompt = service.build_prompt(job, profile)
            self.assertLess(len(prompt), 55000)

    def test_deleting_sources_cascades_assessment_on_sqlite(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            session.delete(session.get(models.Job, self.job_id)); session.commit()
            self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))

    def test_existing_local_exclusions_veto_model_eligibility(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            # Cached result used only with matching inputs; apply an independent
            # objective local veto to prove numerical authority cannot bypass it.
            job = session.get(models.Job, self.job_id)
            service = self.service()
            documents = service.load_matching_documents(session, [job], [profile])
            result = service.apply_cached_assessment({'eligible': False, 'score': 0,
                'conflicts': ['Excluded user term'], 'unknowns': []}, job, profile, documents)
            self.assertEqual(result['score'], 90)
            self.assertFalse(result['eligible']); self.assertEqual(result['fit_tier'], 'unlikely')


class ManagedWorkerTests(unittest.TestCase):
    def test_worker_keeps_rule_and_semantic_cycles_when_llm_pauses(self):
        from hiring_scraper.app import profile_match_worker as worker
        self.assertTrue(hasattr(worker, 'refresh_llm_scores_once'), 'Managed worker does not schedule LLM scoring')
        events = []
        def stop(_seconds):
            raise InterruptedError('Stop managed loop')
        with patch.object(worker, 'refresh_profile_match_indexes_once', lambda: events.append('rules')), \
             patch.object(worker, 'refresh_semantic_documents_once', lambda: events.append('semantics')), \
             patch.object(worker, 'refresh_llm_scores_once', lambda: events.append('llm') or False), \
             patch.object(worker, 'prepare_once', lambda: events.append('preparation')), \
             patch.object(worker.time, 'sleep', stop):
            with self.assertRaises(InterruptedError):
                worker.main()
        self.assertEqual(events, ['rules', 'semantics', 'llm', 'preparation'])

class FurtherSourceTests(unittest.TestCase):
    setUp = PersistedAssessmentTests.setUp
    tearDown = PersistedAssessmentTests.tearDown
    service = PersistedAssessmentTests.service
    refresh = PersistedAssessmentTests.refresh

    def test_cv_contact_layout_edits_retain_assessment(self):
        from tests.test_tex_cv import source
        cv_text = source()
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            session.add(models.ProfileCV(profile=profile, filename='cv.tex', content_type='text/plain',
                size_bytes=len(cv_text), data=cv_text.encode(), extracted_text=cv_text,
                variants={'en': {'text': cv_text, 'filename': 'cv.tex'}}))
            session.commit()
        self.assertTrue(self.refresh())
        with self.factory() as session:
            cv = session.get(models.ProfileCV, self.profile_id)
            text = cv_text.replace('Alex Example', 'New Contact Name')
            cv.extracted_text = text
            cv.variants = {'en': {'text': text, 'filename': 'cv.tex'}}
            session.commit()
            self.assertIsNotNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))

    def test_new_location_invalidates_cached_assessment_and_index(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            session.add(models.JobLocation(job_id=self.job_id, label='Berlin', country_code='DE'))
            session.commit()
            self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))
            self.assertTrue(profile_match_index_stale(session.get(models.UserProfile, self.profile_id)))

    def test_source_removal_during_inference_does_not_publish(self):
        def generate(prompt, schema, **kwargs):
            with self.factory() as session:
                session.delete(session.get(models.Job, self.job_id)); session.commit()
            return assessment()
        self.assertFalse(self.refresh(generate=generate))
        with self.factory() as session:
            self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))

    def test_incremental_refresh_rejects_concurrent_assessment_change(self):
        self.assertTrue(self.refresh())
        from hiring_scraper.app.job_search import ProfileIndexChanged, refresh_application_matches_for_jobs
        def change_assessment(_session):
            with self.factory() as writer:
                row = writer.get(models.ProfileLLMScore, (self.profile_id, self.job_id))
                row.assessment = assessment(2); writer.commit()
        with self.factory() as session:
            with self.assertRaises(ProfileIndexChanged):
                refresh_application_matches_for_jobs(session, [self.job_id], before_publish=change_assessment)

    def test_enrichment_only_updates_do_not_repeat_inference(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            session.get(models.Job, self.job_id).enrichment = {'version': 'new extractor'}
            session.commit()
            self.assertIsNotNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))
        self.assertFalse(self.refresh(generate=lambda *args, **kwargs: self.fail('Unchanged submitted data')))

    def test_hash_mismatch_in_current_version_is_rescored_after_bulk_source_edit(self):
        self.assertTrue(self.refresh(assessment(4)))
        # Bulk ingestion bypasses ORM invalidation: the audit queue must still
        # inspect cached low scores instead of excluding them permanently.
        with self.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE jobs SET description = description || ' New Python duties.'")
        self.assertTrue(self.refresh(assessment(94)))
        with self.factory() as session:
            result = api.get_job(self.job_id, session=session, profile_id=self.profile_id)['profile_match']
            self.assertEqual(result['score'], 94)

    def test_indexed_cards_recompute_local_baseline_when_assessment_hash_is_stale(self):
        self.assertTrue(self.refresh(assessment(4)))
        with self.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE jobs SET description = description || ' More Python services.'")
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            indexed = session.get(models.ProfileJobMatch, (self.profile_id, self.job_id))
            page = api._jobs_page([job], 'match', 0, 40, 49., 8.4, 35, profile, 0, True,
                indexed_matches={job.id: indexed}, precomputed_scopes={job.id: {
                    'eligible': True, 'reason': None, 'unknowns': [], 'match_kind': 'remote'}})
            self.assertGreaterEqual(page['items'][0]['profile_match']['score'], 65)
            self.assertEqual(page['items'][0]['profile_match']['score_source'], 'local')

    def test_late_requirement_before_long_benefits_is_retained_in_prompt(self):
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            job.description = ('About us:\n' + 'A large organization. ' * 1800 +
                '\nRequirements:\nGerman C1 required for customer presentations.\nBenefits:\n' +
                'We offer a friendly workplace and lunch. ' * 1800)
            prompt = self.service().build_prompt(job, session.get(models.UserProfile, self.profile_id))
            self.assertIn('German C1 required', prompt)
            self.assertLess(len(prompt), 55000)

    def test_paused_quota_keeps_cached_assessments_readable(self):
        self.assertTrue(self.refresh())
        self.assertFalse(self.refresh(generate=lambda *a, **kw: self.fail('Quota blocked'),
            usage=lambda: {'allowed': False, 'reason': 'usage_limit_reached', 'used_percent': 50}))
        with self.factory() as session:
            self.assertEqual(api.get_job(self.job_id, session=session, profile_id=self.profile_id)['profile_match']['score'], 90)

    def test_cached_assessment_hash_mismatch_falls_back_without_inference(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            row = session.get(models.ProfileLLMScore, (self.profile_id, self.job_id))
            row.source_hash = '0' * 64; session.commit()
            result = api.get_job(self.job_id, session=session, profile_id=self.profile_id)['profile_match']
            self.assertEqual(result['score_source'], 'local')
            self.assertIsNone(result['llm_match'])
