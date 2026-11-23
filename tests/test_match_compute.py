"""Tests for shared match computation, stored explanations and cheaper invalidation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy.dialects import postgresql

from hiring_scraper.app.enrichment import current_enrichment, job_input
from hiring_scraper.app import match_compute, profile_semantics, search_snapshots
from hiring_scraper.app.profile_semantics import SemanticDocuments, cached_semantic_evidence
from hiring_scraper.matching import match_job
from hiring_scraper.semantic_matching import (
    DIMENSIONS,
    VERSION as SEMANTIC_VERSION,
    job_sections,
    pack_vectors,
    profile_sections,
    source_hash as semantic_source_hash,
)


def _make_job(
    job_id: int = 101,
    *,
    title: str = 'Python Backend Developer',
    description: str = (
        'Your tasks:\n'
        '- Build Python services and maintain SQL databases.\n'
        '- Deploy Docker containers to production.\n'
        'Requirements:\n'
        '- 3 years of experience in backend development.\n'
        '- English C1 required.\n'
    ),
    location_text: str = 'Karlsruhe, Germany',
    is_remote: bool = False,
    work_arrangement: str | None = 'hybrid',
    employment_type: str | None = 'full_time',
    seniority: str | None = None,
    locations: list | None = None,
    raw_metadata: dict | None = None,
    enrichment: dict | None = None,
):
    company = SimpleNamespace(
        id=1, name='Example GmbH', domain='example.de', state='Baden-Württemberg',
        country_code='DE', category='tech', source='test',
        website_url='https://example.de', source_url='https://example.de/jobs',
    )
    feed = SimpleNamespace(
        id=1, company=company, provider='html_jobs', board_url='https://example.de/jobs',
        status='parsed', tenant=None, source_revision=None,
    )
    return SimpleNamespace(
        id=job_id,
        feed_id=1,
        feed=feed,
        employer_company_id=None,
        employer_company=None,
        external_id=f'ext-{job_id}',
        title=title,
        url=f'https://example.de/jobs/{job_id}',
        description=description,
        location_text=location_text,
        is_remote=is_remote,
        work_arrangement=work_arrangement,
        employment_type=employment_type,
        schedule=None,
        department='Engineering',
        seniority=seniority,
        date_posted='2026-05-01',
        salary=None,
        raw_metadata=raw_metadata or {},
        enrichment=enrichment or {},
        locations=locations if locations is not None else [
            SimpleNamespace(
                label='Karlsruhe, Germany', latitude=49.0069, longitude=8.4037,
                precision='city', country_code='DE',
            )
        ],
        first_seen_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        last_seen_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
        is_active=True,
        closed_at=None,
    )


def _make_profile(
    profile_id: int = 7,
    *,
    preferences: dict | None = None,
    low_afd_vote_share_weight: int = 0,
    match_index_version: str | None = None,
):
    from hiring_scraper.app.models import UserProfile

    prefs = {
        'skills': ['Python', 'SQL', 'Docker'],
        'desired_roles': ['Software developer'],
        'summary': 'Built Python services, maintained SQL databases, and deployed Docker containers.',
        'experience_years': 4,
        'language_levels': {'English': 'C1', 'German': 'B2'},
        'work_styles': ['remote', 'hybrid'],
        'employment_types': ['full_time'],
        'search_area': {
            'city': 'Karlsruhe', 'latitude': 49.0069, 'longitude': 8.4037,
            'radius_km': 35, 'country_code': 'DE',
        },
        'matching_defaults': {'min_match_score': 30, 'include_unknown': True},
    }
    if preferences:
        prefs.update(preferences)
    return UserProfile(
        id=profile_id,
        name='Developer',
        preferences=prefs,
        low_afd_vote_share_weight=low_afd_vote_share_weight,
        match_index_version=match_index_version,
    )


def _empty_documents():
    return SemanticDocuments()


def _semantic_documents(job, profile):
    docs = _empty_documents()
    j_sections = job_sections({'description': job.description, 'raw_metadata': job.raw_metadata})
    p_sections = profile_sections(profile.matching_preferences)
    vec = [1.0] + [0.0] * (DIMENSIONS - 1)
    j_hash = semantic_source_hash(j_sections)
    p_hash = semantic_source_hash(p_sections)
    docs[('job', job.id)] = SimpleNamespace(
        version=SEMANTIC_VERSION,
        source_hash=j_hash,
        sections=j_sections,
        vectors=pack_vectors([vec for _ in j_sections]),
    )
    docs[('profile', profile.id)] = SimpleNamespace(
        version=SEMANTIC_VERSION,
        source_hash=p_hash,
        sections=p_sections,
        vectors=pack_vectors([vec for _ in p_sections]),
    )
    docs.source_hashes[('job', job.id)] = j_hash
    docs.source_hashes[('profile', profile.id)] = p_hash
    return docs


def _snapshot_from_docs(job, profile, docs, payload, *, dirty_revision=None):
    from hiring_scraper.app import job_search

    job_doc = docs.get(('job', job.id))
    prof_doc = docs.get(('profile', profile.id))
    match_state = (
        match_compute.MatchRowState(
            match_score=payload['match_score'],
            match_tier=payload['match_tier'],
            match_uncertain=payload['match_uncertain'],
            explanation=payload['explanation'],
            score_version=payload['score_version'],
        )
        if payload is not None
        else None
    )
    return match_compute.MatchSnapshot(
        profile_id=profile.id,
        job_id=job.id,
        profile_index_version=(
            profile.match_index_version
            if profile.match_index_version is not None
            else job_search._profile_index_version(profile)
        ),
        profile_source_version=1,
        match=match_state,
        dirty_revision=dirty_revision,
        job_semantic_hash=getattr(job_doc, 'source_hash', None),
        profile_semantic_hash=getattr(prof_doc, 'source_hash', None),
    )


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)


class ComputeMatchEquivalenceTests(unittest.TestCase):
    """Verify compute_match produces byte-identical output to the direct rules+semantic path."""

    def _direct_compute(self, job, profile, documents, scope=None):
        source = job_input(job)
        enrichment = current_enrichment(job)
        semantic = cached_semantic_evidence(
            source, profile.matching_preferences, job.id, profile.id, documents,
        )
        result = match_job(
            source, profile.matching_preferences, enrichment,
            **({'semantic': semantic} if semantic else {}),
        )
        return search_snapshots._apply_profile_match_scope(result, scope)

    def test_byte_identical_across_representative_fixtures(self):
        base_profile = _make_profile()
        afd_profile = _make_profile(low_afd_vote_share_weight=10)
        conflict_profile = _make_profile(preferences={'excluded_terms': ['Docker']})

        fixtures = [
            ('local_only', _make_job(1), base_profile, _empty_documents(), None),
            ('local_with_afd_context', _make_job(2), afd_profile, _empty_documents(), None),
            ('ineligible_conflict', _make_job(3), conflict_profile, _empty_documents(), None),
            ('semantic_boosted', _make_job(
                4,
                title='Platform Specialist',
                description='Your tasks:\nDevelop server applications, manage relational stores and automate releases.\n' * 3,
            ), base_profile, None, None),
            ('scope_with_unknowns', _make_job(6, is_remote=True, work_arrangement='remote'), base_profile, None, {
                'eligible': True, 'reason': None,
                'unknowns': ['Remote country eligibility not stated'], 'match_kind': 'remote',
            }),
            ('scope_outside_area', _make_job(7), base_profile, _empty_documents(), {
                'eligible': False, 'reason': 'outside_area', 'unknowns': [], 'match_kind': None,
            }),
        ]

        for name, job, profile, docs_spec, scope in fixtures:
            with self.subTest(fixture=name):
                docs = _semantic_documents(job, profile) if docs_spec is None else docs_spec

                direct = self._direct_compute(job, profile, docs, scope)
                snapshot_result = search_snapshots.profile_match(job, profile, scope, docs)
                new_result = match_compute.compute_match(job, profile, docs, scope=scope)

                self.assertEqual(_canonical_json(new_result), _canonical_json(direct))
                self.assertEqual(_canonical_json(new_result), _canonical_json(snapshot_result))


class StoredExplanationTests(unittest.TestCase):
    """Tests for explanation projection shape, JSONB safety, final-wire size bound, and score version."""

    REQUIRED_KEYS = {
        'score', 'fit_tier', 'label', 'eligible', 'uncertain',
        'mandatory_review', 'language_review', 'reasons', 'unknowns', 'conflicts',
        'soft_conflicts', 'requirement_gaps', 'matched_skills', 'missing_skills',
        'requirement_observations', 'gates', 'task_fit', 'qualification_fit',
        'static_rank_basis', 'duty_evidence', 'semantic_match', 'local_context',
    }

    def test_projection_includes_requirement_observations_city_votes_and_strips_stamp_on_read(self):
        job = _make_job(
            11,
            description=(
                'Your tasks:\n- Build Python services and maintain SQL databases.\n'
                'Requirements:\n- Academic experience with Docker required.\n'
                '- Master degree in Computer Science required.\n'
            ),
        )
        profile = _make_profile(low_afd_vote_share_weight=15)
        docs = _semantic_documents(job, profile)
        result = match_compute.compute_match(job, profile, docs)
        score_version = match_compute.current_score_version()

        row = match_compute.match_row_payload(profile.id, job.id, result, score_version, job=job, documents=docs)
        explanation = row['explanation']

        self.assertTrue(self.REQUIRED_KEYS.issubset(explanation.keys()))
        self.assertEqual(explanation['score'], row['match_score'])
        self.assertEqual(explanation['score'], result['score'])
        self.assertEqual(row['score_version'], score_version)
        self.assertLessEqual(len(row['score_version']), 80)
        self.assertIn('_stamp', explanation)
        self.assertEqual(explanation['_stamp']['job'], match_compute.job_revision(job))
        # City vote evidence and requirement observations are preserved in stored cards.
        self.assertEqual(len(explanation['local_context']['city_election_results']),
                         len(result['local_context']['city_election_results']))
        self.assertTrue(explanation['local_context']['city_election_results'])
        self.assertEqual(explanation['local_context']['city_election_results'][0]['municipality'],
                         result['local_context']['city_election_results'][0]['municipality'])
        self.assertEqual(len(explanation['requirement_observations']),
                         len(result.get('requirement_observations') or []))
        if explanation['duty_evidence'] is not None:
            for duty in explanation['duty_evidence']['duties']:
                self.assertTrue(set(duty.keys()).issubset(set(match_compute._DUTY_FIELDS)))
        # public_explanation strips internal _stamp before returning to callers.
        public = match_compute.public_explanation(explanation)
        self.assertNotIn('_stamp', public)
        # Round-trips cleanly with default SQLAlchemy/psycopg json.dumps serializer.
        encoded = match_compute.serialize_explanation(explanation)
        self.assertLessEqual(len(encoded.encode('utf-8')), match_compute.EXPLANATION_MAX_BYTES)

    def test_projection_strips_nul_surrogates_non_finite_floats_and_bounds_final_wire_json(self):
        huge_result = {
            'score': 73,
            'fit_tier': 'possible',
            'label': 'L\x00äöüß\ud800' * 400,
            'eligible': True,
            'uncertain': True,
            'mandatory_review': False,
            'language_review': True,
            'task_fit': float('nan'),
            'qualification_fit': float('inf'),
            'static_rank_basis': 'duties\x00\udfff',
            'reasons': [f'Reason\x00 {i}: ' + ('äöüß🚀' * 200) for i in range(80)],
            'unknowns': [f'Unknown {i}: ' + ('ü' * 600) for i in range(80)],
            'conflicts': [f'Conflict {i}: ' + ('ß' * 600) for i in range(60)],
            'soft_conflicts': [f'Soft {i}: ' + ('ö' * 600) for i in range(60)],
            'requirement_gaps': [f'Gap {i}: ' + ('ä' * 600) for i in range(60)],
            'matched_skills': [f'Skill\x00{i}' for i in range(100)],
            'missing_skills': [f'Missing\ud83d{i}' for i in range(100)],
            'requirement_observations': [
                {
                    'kind': 'education',
                    'requirement': f'Degree\x00 {i}: ' + ('ä' * 400),
                    'status': 'unknown',
                    'job_source': 'description',
                    'job_quote': 'Q\ud800' * 400,
                    'evidence_source': 'cv',
                    'evidence_quote': 'E' * 400,
                    'evidence_context': 'academic',
                    'explanation': 'X' * 400,
                }
                for i in range(40)
            ],
            'gates': {
                f'gate\x00_{i}': {'status': 'unknown', 'reason': 'Räöü' * 500}
                for i in range(80)
            },
            'duty_evidence': {
                'score': 80,
                'coverage': 0.9,
                'duties': [
                    {
                        'job_quote': f'Duty {i}: ' + ('d' * 800),
                        'status': 'supported',
                        'evidence_source': 'profile.summary',
                        'evidence_quote': 'E' * 800,
                        'evidence_reference': 'ref',
                        'evidence_context': 'professional',
                        'extra_unused_blob': 'Z' * 2000,
                    }
                    for i in range(40)
                ],
            },
            'semantic_match': {
                'score': 77,
                'similarity': 0.88,
                'model': 'e5',
                'job_source': 'tasks',
                'profile_source': 'summary',
                'job_evidence': 'J' * 1500,
                'profile_evidence': 'P' * 1500,
                'raw_vectors': [0.1] * 1024,
            },
            'local_context': {
                'score_adjustment': -3,
                'weight': 15,
                'average_afd_vote_share_percent': 21.4,
                'city_election_results': [
                    {
                        'municipality_code': f'0821200{i}',
                        'municipality': f'Karlsruhe\x00 {i}',
                        'state': 'Baden-Württemberg',
                        'party': 'AfD',
                        'leading_party': 'CDU',
                        'leading_party_vote_share_percent': 30.1,
                        'vote_share_percent': 12.4,
                        'party_votes': 12345,
                        'valid_second_votes': 100000,
                        'postal_ballots_included': True,
                        'election': 'Bundestagswahl 2025',
                        'vote_type': 'second votes',
                        'source': 'Bundeswahlleiterin',
                        'source_url': 'https://example.org/election',
                    }
                    for i in range(50)
                ],
            },
            'custom_skill_evidence': [
                {'name': f'Custom {i}', 'evidence': 'V' * 800, 'source': 'cv'}
                for i in range(30)
            ],
        }
        explanation = match_compute.build_explanation(huge_result)
        wire = match_compute.serialize_explanation(explanation)
        self.assertLessEqual(len(wire.encode('utf-8')), match_compute.EXPLANATION_MAX_BYTES)
        # Must contain zero NUL bytes or lone surrogates in serialized JSONB wire form.
        self.assertNotIn('\\u0000', wire)
        self.assertNotIn('\x00', wire)
        for ch in wire:
            self.assertFalse(0xD800 <= ord(ch) <= 0xDFFF)
        self.assertTrue(explanation.get('truncated'))
        self.assertIsNone(explanation['task_fit'])
        self.assertIsNone(explanation['qualification_fit'])
        self.assertEqual(explanation['score'], 73)
        self.assertEqual(explanation['local_context']['score_adjustment'], -3)

        # Extreme fallback path must STILL obey max_bytes on the final wire JSON.
        tiny = match_compute.build_explanation(huge_result, max_bytes=384)
        tiny_wire = match_compute.serialize_explanation(tiny)
        self.assertLessEqual(len(tiny_wire.encode('utf-8')), 384)
        self.assertEqual(tiny['score'], 73)
        self.assertTrue(tiny.get('truncated'))

    def test_current_score_version_changes_with_matcher_and_semantic_versions(self):
        v1 = match_compute.current_score_version()
        self.assertLessEqual(len(v1), 80)

        with patch.object(match_compute, 'MATCHING_VERSION', 'matcher-next'):
            self.assertNotEqual(match_compute.current_score_version(), v1)
        with patch.object(match_compute, 'SEMANTIC_VERSION', 'semantic-next'):
            self.assertNotEqual(match_compute.current_score_version(), v1)


class PureDecisionLogicTests(unittest.TestCase):
    """Mock-free unit tests for currency validation, Journey cards, and indexed search."""

    def test_explanation_is_current_checks_all_invalidation_dimensions(self):
        v1 = match_compute.current_score_version()
        local_row = match_compute.MatchRowState(
            match_score=62,
            match_tier='possible',
            match_uncertain=False,
            score_version=v1,
            explanation={
                'score': 62,
                '_stamp': {'job': 'jobrev1', 'sem': 'jsem1:psem1'},
            },
        )

        self.assertTrue(match_compute.explanation_is_current(
            local_row, dirty=False, profile_stale=False,
            job_revision='jobrev1', semantic_hash='jsem1:psem1',
            expected_version=v1,
        ))
        self.assertFalse(match_compute.explanation_is_current(
            local_row, dirty=False, profile_stale=False,
            job_revision='jobrev1', semantic_hash='jsem1:psem1',
            expected_version='different-version',
        ))
        self.assertFalse(match_compute.explanation_is_current(
            local_row, dirty=True, profile_stale=False,
            job_revision='jobrev1', semantic_hash='jsem1:psem1',
            expected_version=v1,
        ))
        self.assertFalse(match_compute.explanation_is_current(
            local_row, dirty=False, profile_stale=True,
            job_revision='jobrev1', semantic_hash='jsem1:psem1',
            expected_version=v1,
        ))
        self.assertFalse(match_compute.explanation_is_current(
            local_row, dirty=False, profile_stale=False,
            job_revision='jobrev2', semantic_hash='jsem1:psem1',
            expected_version=v1,
        ))
        self.assertFalse(match_compute.explanation_is_current(
            local_row, dirty=False, profile_stale=False,
            job_revision='jobrev1', semantic_hash='jsem2:psem1',
            expected_version=v1,
        ))
        # Unfaithful score or missing _stamp rejects.
        unfaithful = match_compute.MatchRowState(
            match_score=90, match_tier='recommended', match_uncertain=False,
            score_version=v1, explanation=local_row.explanation,
        )
        self.assertFalse(match_compute.explanation_is_current(
            unfaithful, dirty=False, profile_stale=False,
            job_revision='jobrev1', semantic_hash='jsem1:psem1',
            expected_version=v1,
        ))

    def test_select_journey_card_keeps_rank_and_card_score_consistent(self):
        row = match_compute.MatchRowState(
            match_score=85,
            match_tier='recommended',
            match_uncertain=False,
            score_version='old-version',
            explanation={'score': 85, '_stamp': {'job': 'r1'}},
        )
        # Current row -> 'stored', exact score, no refreshing flag, _stamp stripped.
        mode, card = match_compute.select_journey_card(row, ranked_score=85, is_current=True)
        self.assertEqual(mode, 'stored')
        self.assertEqual(card['score'], 85)
        self.assertNotIn('refreshing', card)
        self.assertNotIn('_stamp', card)

        # Stale/dirty faithful row -> 'refreshing' with SAME score Journey ranked by.
        mode, card = match_compute.select_journey_card(row, ranked_score=85, is_current=False)
        self.assertEqual(mode, 'refreshing')
        self.assertEqual(card['score'], 85)
        self.assertTrue(card['refreshing'])
        self.assertNotIn('_stamp', card)

        # Legacy row (NULL explanation), missing row, or drifted rank -> 'recompute'.
        legacy = match_compute.MatchRowState(
            match_score=85, match_tier='recommended', match_uncertain=False,
            score_version=None, explanation=None,
        )
        self.assertEqual(match_compute.select_journey_card(legacy, ranked_score=85, is_current=False), ('recompute', None))
        self.assertEqual(match_compute.select_journey_card(None, ranked_score=-1, is_current=False), ('recompute', None))
        self.assertEqual(match_compute.select_journey_card(row, ranked_score=60, is_current=False), ('recompute', None))

    def test_indexed_missing_match_action_distinguishes_authoritative_vs_pending(self):
        self.assertEqual(
            match_compute.indexed_missing_match_action(profile_stale=False, job_dirty=False),
            'profile_conflict',
        )
        self.assertEqual(
            match_compute.indexed_missing_match_action(profile_stale=True, job_dirty=False),
            'recompute',
        )
        self.assertEqual(
            match_compute.indexed_missing_match_action(profile_stale=False, job_dirty=True),
            'recompute',
        )
        self.assertEqual(
            match_compute.indexed_missing_match_action(profile_stale=True, job_dirty=True),
            'recompute',
        )

    def test_evaluate_stored_new_application_checks_threshold_and_defers_stale(self):
        from hiring_scraper.app import job_search

        profile = _make_profile(preferences={
            'matching_defaults': {'min_match_score': 70, 'include_unknown': False},
        })
        job = _make_job(55)
        below_threshold = match_compute.MatchRowState(
            match_score=50, match_tier='possible', match_uncertain=False,
            score_version='v', explanation={'score': 50, 'eligible': True},
        )
        above_threshold = match_compute.MatchRowState(
            match_score=82, match_tier='recommended', match_uncertain=False,
            score_version='v', explanation={'score': 82, 'eligible': True},
        )
        self.assertEqual(
            job_search.evaluate_stored_new_application(job, profile, below_threshold, is_current=True),
            'retract',
        )
        self.assertEqual(
            job_search.evaluate_stored_new_application(job, profile, above_threshold, is_current=True),
            'qualifies',
        )
        # Dirty/stale/legacy row -> defer regardless of score.
        self.assertEqual(
            job_search.evaluate_stored_new_application(job, profile, above_threshold, is_current=False),
            'defer',
        )
        legacy = match_compute.MatchRowState(
            match_score=95, match_tier='recommended', match_uncertain=False,
            score_version=None, explanation=None,
        )
        self.assertEqual(
            job_search.evaluate_stored_new_application(job, profile, legacy, is_current=True),
            'defer',
        )

    def test_single_snapshot_query_compiles_for_postgresql_without_loading_blobs(self):
        stmt = match_compute.build_stored_match_snapshot_query([(1, 10), (1, 20), (2, 10)])
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        self.assertIn('FROM user_profiles', compiled)
        self.assertIn('LEFT OUTER JOIN profile_job_matches', compiled)
        self.assertIn('LEFT OUTER JOIN match_dirty_jobs', compiled)
        self.assertIn('LEFT OUTER JOIN match_semantic_documents', compiled)
        self.assertNotIn('profile_llm_scores', compiled)
        self.assertNotIn('ai_matching_settings', compiled)
        # Exact pair tuple predicate when spanning multiple profiles and jobs.
        self.assertIn('(user_profiles.id, jobs.id) IN', compiled)
        # Must NOT fetch vector blobs or section JSON.
        self.assertNotIn('.vectors', compiled)
        self.assertNotIn('.sections', compiled)


class StoredExplanationReadPathTests(unittest.TestCase):
    """Verify read paths reuse stored explanations with minimal reads and fall back safely."""

    def _indexed_row(self, job, profile, docs):
        result = match_compute.compute_match(job, profile, docs)
        score_version = match_compute.current_score_version()
        payload = match_compute.match_row_payload(profile.id, job.id, result, score_version, job=job, documents=docs)
        return SimpleNamespace(**payload), result, score_version, payload

    def test_indexed_jobs_page_reuses_stored_explanation_without_loading_vectors_on_hits(self):
        from hiring_scraper.app import job_search

        job = _make_job(21, description=(
            'Your tasks:\n- Build Python services and maintain SQL databases.\n'
            'Requirements:\n- Leadership experience required.\n'
        ))
        profile = _make_profile()
        profile.match_index_version = job_search._profile_index_version(profile)
        docs = _semantic_documents(job, profile)
        indexed, _, _, payload = self._indexed_row(job, profile, docs)

        snapshot = _snapshot_from_docs(job, profile, docs, payload)
        scope = {'eligible': True, 'reason': None,
                 'unknowns': ['Remote country eligibility not stated'], 'match_kind': 'remote'}
        with patch.object(profile_semantics, 'load_semantic_documents', side_effect=AssertionError('Must not load vectors on hit')), \
             patch.object(match_compute, 'match_job', side_effect=AssertionError('Should not recompute')):
            page = search_snapshots._jobs_page(
                [job], 'match', 0, 20, 49.0, 8.4, 35, profile, 0, True,
                match_snapshots={(profile.id, job.id): snapshot},
                precomputed_scopes={job.id: scope},
            )
        card_match = page['items'][0]['profile_match']
        self.assertEqual(card_match['score'], indexed.match_score)
        self.assertNotIn('_stamp', card_match)
        self.assertIn('Remote country eligibility not stated', card_match['unknowns'])
        # Stored explanation dict must not be mutated in place by scope application.
        self.assertNotIn('Remote country eligibility not stated', indexed.explanation['unknowns'])

    def test_indexed_jobs_page_loads_vectors_only_for_fallback_jobs_and_handles_pending_missing(self):
        from hiring_scraper.app import job_search

        job_hit = _make_job(31)
        job_pending_missing = _make_job(32)
        job_authoritative_missing = _make_job(33)
        profile = _make_profile()
        profile.match_index_version = job_search._profile_index_version(profile)
        docs = _empty_documents()
        _, _, _, payload_hit = self._indexed_row(job_hit, profile, docs)

        snapshots = {
            (profile.id, job_hit.id): _snapshot_from_docs(job_hit, profile, docs, payload_hit),
            # Dirty job with missing ProfileJobMatch -> pending -> recompute!
            (profile.id, job_pending_missing.id): _snapshot_from_docs(
                job_pending_missing, profile, docs, None, dirty_revision='rev-dirty',
            ),
            # Clean job + current profile with missing ProfileJobMatch -> authoritative profile_conflict!
            (profile.id, job_authoritative_missing.id): _snapshot_from_docs(
                job_authoritative_missing, profile, docs, None, dirty_revision=None,
            ),
        }
        scopes = {
            j.id: {'eligible': True, 'reason': None, 'unknowns': [], 'match_kind': 'area'}
            for j in (job_hit, job_pending_missing, job_authoritative_missing)
        }
        loaded_job_ids = []

        def fake_load(session, jobs_arg, profiles_arg):
            loaded_job_ids.extend(j.id for j in jobs_arg)
            return docs

        with patch.object(profile_semantics, 'load_semantic_documents', side_effect=fake_load):
            page = search_snapshots._jobs_page(
                [job_hit, job_pending_missing, job_authoritative_missing],
                'match', 0, 20, 49.0, 8.4, 35, profile, 0, True,
                match_snapshots=snapshots,
                precomputed_scopes=scopes,
            )
        # Only the pending-missing job triggered load_semantic_documents (NOT job_hit or authoritative missing).
        self.assertEqual(loaded_job_ids, [job_pending_missing.id])
        returned_ids = {item['id'] for item in page['items']}
        self.assertEqual(returned_ids, {job_hit.id, job_pending_missing.id})
        self.assertEqual(page['counts']['filtered']['profile_conflict'], 1)

    def test_profile_match_reuses_preloaded_stored_explanation_without_recomputing(self):
        from hiring_scraper.app import job_search

        job = _make_job(22)
        profile = _make_profile()
        profile.match_index_version = job_search._profile_index_version(profile)
        docs = _empty_documents()
        indexed, _, _, _ = self._indexed_row(job, profile, docs)
        docs.indexed_matches = {(profile.id, job.id): indexed}
        docs.dirty_job_ids = set()

        with patch.object(match_compute, 'match_job', side_effect=AssertionError('Should not recompute')):
            result = search_snapshots.profile_match(job, profile, None, docs)
        self.assertEqual(result['score'], indexed.match_score)
        self.assertNotIn('_stamp', result)

    def test_stored_explanation_falls_back_on_version_mismatch_missing_dirty_stale_or_unfaithful(self):
        from hiring_scraper.app import job_search

        job = _make_job(23)
        profile = _make_profile()
        profile.match_index_version = job_search._profile_index_version(profile)
        docs = _semantic_documents(job, profile)
        indexed, _, score_version, _ = self._indexed_row(job, profile, docs)

        # 1. Missing explanation
        no_expl = SimpleNamespace(**{**indexed.__dict__, 'explanation': None})
        self.assertIsNone(match_compute.stored_match_explanation(
            no_expl, job, profile, docs, expected_version=score_version, dirty_job_ids=set(), profile_stale=False))
        # 2. Version mismatch
        old_ver = SimpleNamespace(**{**indexed.__dict__, 'score_version': 'stale-version'})
        self.assertIsNone(match_compute.stored_match_explanation(
            old_ver, job, profile, docs, expected_version=score_version, dirty_job_ids=set(), profile_stale=False))
        # 3. Faithfulness mismatch (explanation.score != match_score)
        unfaithful = SimpleNamespace(**{**indexed.__dict__, 'match_score': indexed.match_score + 5})
        self.assertIsNone(match_compute.stored_match_explanation(
            unfaithful, job, profile, docs, expected_version=score_version, dirty_job_ids=set(), profile_stale=False))
        # 4. Dirty job
        self.assertIsNone(match_compute.stored_match_explanation(
            indexed, job, profile, docs, expected_version=score_version, dirty_job_ids={job.id}, profile_stale=False))
        # 5. Stale profile
        self.assertIsNone(match_compute.stored_match_explanation(
            indexed, job, profile, docs, expected_version=score_version, dirty_job_ids=set(), profile_stale=True))
        # 6. Stale semantic hash
        docs[('job', job.id)] = SimpleNamespace(**{**docs[('job', job.id)].__dict__, 'source_hash': '0' * 64})
        self.assertIsNone(match_compute.stored_match_explanation(
            indexed, job, profile, docs, expected_version=score_version, dirty_job_ids=set(), profile_stale=False))

        # End-to-end fallback via _jobs_page recomputes fresh local result when stored row is stale.
        with patch.object(profile_semantics, 'load_semantic_documents', return_value=docs):
            page = search_snapshots._jobs_page(
                [job], 'match', 0, 20, 49.0, 8.4, 35, profile, 0, True,
                indexed_matches={job.id: old_ver},
                precomputed_scopes={job.id: {'eligible': True, 'reason': None, 'unknowns': [], 'match_kind': 'area'}},
            )
        self.assertEqual(page['items'][0]['profile_match']['score'], indexed.match_score)


class CheaperInvalidationTests(unittest.TestCase):
    """Verify presentation-only preference split and New-row re-evaluation."""

    def test_profile_index_version_ignores_presentation_defaults(self):
        from hiring_scraper.app import job_search

        base = _make_profile(preferences={
            'matching_defaults': {'min_match_score': 30, 'include_unknown': True},
        })
        base_ver = job_search._profile_index_version(base)

        presentation_only = _make_profile(preferences={
            'matching_defaults': {'min_match_score': 85, 'include_unknown': False},
        })
        self.assertEqual(job_search._profile_index_version(presentation_only), base_ver)

        skills_changed = _make_profile(preferences={'skills': ['Java', 'Kotlin']})
        self.assertNotEqual(job_search._profile_index_version(skills_changed), base_ver)

        afd_changed = _make_profile(low_afd_vote_share_weight=25)
        self.assertNotEqual(job_search._profile_index_version(afd_changed), base_ver)

        area_changed = _make_profile(preferences={
            'search_area': {
                'city': 'Munich', 'latitude': 48.1351, 'longitude': 11.5820,
                'radius_km': 25, 'country_code': 'DE',
            },
        })
        self.assertNotEqual(job_search._profile_index_version(area_changed), base_ver)

    def test_profiles_save_passes_presentation_only_and_detects_profile_data_changes(self):
        from hiring_scraper.app import job_search, profiles, source_changes
        from hiring_scraper.app.models import UserProfile

        class FakeSession:
            def __init__(self):
                self.added = []
                self.flushed = 0
                self.committed = 0

            def add(self, obj):
                self.added.append(obj)

            def flush(self):
                self.flushed += 1

            def commit(self):
                self.committed += 1

        session = FakeSession()
        base_kwargs = dict(
            name='Developer',
            skills=['Python', 'SQL', 'Docker'],
            desired_roles=['Software developer'],
            summary='Built Python services, maintained SQL databases, and deployed Docker containers.',
            experience_years=4,
            language_levels={'English': 'C1', 'German': 'B2'},
            work_styles=['remote', 'hybrid'],
            employment_types=['full_time'],
            search_area={
                'city': 'Karlsruhe', 'latitude': 49.0069, 'longitude': 8.4037,
                'radius_km': 35, 'country_code': 'DE',
            },
            matching_defaults={'min_match_score': 30, 'include_unknown': True},
        )
        req_initial = profiles.ProfileRequest(**base_kwargs)
        profile = UserProfile(id=None, name='Developer', preferences={}, low_afd_vote_share_weight=0)
        profiles._save(profile, req_initial, session)
        profile.id = 7
        original_version = job_search._profile_index_version(profile)
        profile.match_index_version = original_version

        req_presentation = profiles.ProfileRequest(**{
            **base_kwargs,
            'matching_defaults': {'min_match_score': 75, 'include_unknown': False},
        })
        with patch.object(profiles, 'clear_profile_match_scores') as mock_clear, \
             patch.object(profiles, 'reevaluate_profile_new_applications', return_value={'evaluated': 2, 'added': 0, 'retracted': 1, 'deferred': 0}) as mock_reeval, \
             patch.object(source_changes, 'profile_changed') as mock_declared:
            profiles._save(profile, req_presentation, session)

        self.assertEqual(profile.match_index_version, original_version)
        mock_clear.assert_not_called()
        mock_declared.assert_called_once_with(session, profile.id, presentation_only=True)
        mock_reeval.assert_called_once_with(session, profile)

        # Changing career_evidence (part of matching_preferences / _profile_index_version) triggers full scoring rebuild.
        req_evidence = profiles.ProfileRequest(**{
            **base_kwargs,
            'matching_defaults': {'min_match_score': 75, 'include_unknown': False},
            'application_personalization': {'career_evidence': 'Built distributed systems in production.'},
        })
        with patch.object(profiles, 'clear_profile_match_scores') as mock_clear, \
             patch.object(profiles, 'reevaluate_profile_new_applications') as mock_reeval, \
             patch.object(source_changes, 'profile_changed') as mock_declared:
            profiles._save(profile, req_evidence, session)

        self.assertIsNone(profile.match_index_version)
        mock_clear.assert_called_once_with(session, profile.id)
        mock_declared.assert_called_once_with(session, profile.id, presentation_only=False)
        mock_reeval.assert_not_called()

    def test_reevaluate_profile_new_applications_defers_dirty_stale_and_legacy_rows(self):
        from hiring_scraper.app import job_search, source_changes

        profile = _make_profile(preferences={
            'matching_defaults': {'min_match_score': 60, 'include_unknown': False},
        })
        profile.match_index_version = job_search._profile_index_version(profile)
        docs = _empty_documents()
        score_version = match_compute.current_score_version()

        job_high = _make_job(101)
        job_low = _make_job(102)
        job_dirty = _make_job(103)
        job_legacy = _make_job(104)

        def _row_and_snap(job, score, *, dirty_rev=None, legacy=False):
            if legacy:
                payload = {
                    'profile_id': profile.id, 'job_id': job.id,
                    'match_score': score, 'match_tier': 'possible', 'match_uncertain': False,
                    'explanation': None, 'score_version': None,
                }
            else:
                res = {'score': score, 'fit_tier': 'possible', 'eligible': True, 'uncertain': False}
                payload = match_compute.match_row_payload(profile.id, job.id, res, score_version, job=job, documents=docs)
            row_ns = SimpleNamespace(**payload)
            snap = _snapshot_from_docs(job, profile, docs, payload, dirty_revision=dirty_rev)
            return (row_ns, job), snap

        row_high, snap_high = _row_and_snap(job_high, 80)
        row_low, snap_low = _row_and_snap(job_low, 40)
        row_dirty, snap_dirty = _row_and_snap(job_dirty, 35, dirty_rev='dirty-rev')
        row_legacy, snap_legacy = _row_and_snap(job_legacy, 30, legacy=True)

        select_batches = [[row_high, row_low, row_dirty, row_legacy], []]
        snapshots_map = {
            (profile.id, 101): snap_high,
            (profile.id, 102): snap_low,
            (profile.id, 103): snap_dirty,
            (profile.id, 104): snap_legacy,
        }
        delete_statements = []
        upserted_states = []
        queued_stale = []

        class FakeResult:
            def __init__(self, rows=None, rowcount=0):
                self._rows = rows or []
                self.rowcount = rowcount

            def unique(self):
                return self

            def all(self):
                return list(self._rows)

        class FakeSession:
            def flush(self):
                pass

            def refresh(self, instance, attribute_names=None):
                pass

            def execute(self, stmt):
                sql = str(stmt)
                if sql.startswith('DELETE'):
                    delete_statements.append(stmt)
                    return FakeResult(rowcount=1)
                return FakeResult(select_batches.pop(0))

            def scalars(self, stmt):
                if 'FROM profile_job_matches' in str(stmt):
                    self.batch = select_batches.pop(0)
                    return [match.job_id for match, _ in self.batch]
                return [job for _, job in self.batch]

        with patch.object(match_compute, 'match_job', side_effect=AssertionError('Must not recompute')), \
             patch.object(job_search, 'match_job', side_effect=AssertionError('Must not recompute')), \
             patch.object(job_search, 'load_stored_match_snapshots', return_value=snapshots_map), \
             patch.object(job_search, 'upsert_new_match_states', side_effect=lambda s, rows: upserted_states.extend(rows)), \
             patch.object(source_changes, 'queue_stale_rematch', side_effect=lambda s, ids: queued_stale.extend(ids)), \
             patch.object(job_search, 'consolidate_application_duplicates') as mock_consolidate:
            summary = job_search.reevaluate_profile_new_applications(FakeSession(), profile, batch_size=50)

        self.assertEqual(summary, {'evaluated': 2, 'added': 1, 'retracted': 1, 'deferred': 2})
        self.assertEqual([row['job_id'] for row in upserted_states], [101])
        # Only job 102 (current, non-qualifying) is retracted; dirty 103 and legacy 104 are untouched!
        self.assertEqual(len(delete_statements), 1)
        compiled_del = str(delete_statements[0].compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
        self.assertIn('job_id IN (102)', compiled_del)
        self.assertIn("status = 'new'", compiled_del)
        self.assertIn('auto_created IS true', compiled_del)
        # Non-dirty legacy row 104 is queued for rematching; already-dirty 103 is not re-queued.
        self.assertEqual(queued_stale, [104])
        mock_consolidate.assert_called_once_with(unittest.mock.ANY, {profile.id})


class MigrationSchemaTests(unittest.TestCase):
    """Verify migrations 0042 and 0043 metadata and ORM columns without needing a live database."""

    def test_migration_0042_adds_and_drops_explanation_score_version_and_pending_flag(self):
        from hiring_scraper.app.models import ProfileJobMatch

        migration = importlib.import_module(
            'hiring_scraper.app.migrations.versions.0042_profile_match_explanation'
        )
        self.assertEqual(migration.revision, '0042_profile_match_explanation')
        self.assertEqual(migration.down_revision, '0041_profile_source_version')

        table = ProfileJobMatch.__table__
        self.assertIn('explanation', table.c)
        self.assertIn('score_version', table.c)
        self.assertTrue(table.c.explanation.nullable)
        self.assertTrue(table.c.score_version.nullable)
        self.assertEqual(table.c.score_version.type.length, 80)

        added = []
        dropped = []
        with patch.object(migration.op, 'add_column', side_effect=lambda tbl, col: added.append((tbl, col.name, col.nullable))), \
             patch.object(migration.op, 'drop_column', side_effect=lambda tbl, col: dropped.append((tbl, col))):
            migration.upgrade()
            migration.downgrade()

        self.assertEqual(added, [
            ('profile_job_matches', 'explanation', True),
            ('profile_job_matches', 'score_version', True),
            ('user_profiles', 'presentation_refresh_pending', False),
        ])
        self.assertEqual(dropped, [
            ('user_profiles', 'presentation_refresh_pending'),
            ('profile_job_matches', 'score_version'),
            ('profile_job_matches', 'explanation'),
        ])

    def test_migration_0043_drops_ai_assessment_tables_and_recreates_on_downgrade(self):
        from sqlalchemy.dialects import postgresql
        from hiring_scraper.app import models

        migration = importlib.import_module(
            'hiring_scraper.app.migrations.versions.0043_drop_ai_assessment'
        )
        self.assertEqual(migration.revision, '0043_drop_ai_assessment')
        self.assertEqual(migration.down_revision, '0042_profile_match_explanation')

        dropped_tables = (
            'ai_matching_observed_config',
            'ai_matching_workers',
            'ai_matching_tasks',
            'ai_matching_settings',
            'ai_matching_selections',
            'ai_matching_calls',
            'profile_llm_scores',
        )
        for table_name in dropped_tables:
            self.assertNotIn(table_name, models.Base.metadata.tables)

        pg_dialect = postgresql.dialect()

        def compile_sql(stmt):
            if isinstance(stmt, str):
                return stmt
            return str(stmt.compile(dialect=pg_dialect, compile_kwargs={'literal_binds': True}))

        executed = []
        created_tables = []
        created_indexes = []
        fake_bind = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        with patch.object(migration.op, 'execute', side_effect=lambda sql: executed.append(compile_sql(sql))), \
             patch.object(migration.op, 'create_table', side_effect=lambda name, *cols: created_tables.append(name)), \
             patch.object(migration.op, 'create_index', side_effect=lambda name, tbl, cols: created_indexes.append((name, tbl))), \
             patch.object(migration.op, 'get_bind', return_value=fake_bind):
            migration.upgrade()
            upgrade_sql = list(executed)
            executed.clear()
            migration.downgrade()
            downgrade_sql = list(executed)

        self.assertEqual(
            upgrade_sql[:len(dropped_tables)],
            [f'DROP TABLE IF EXISTS {name}' for name in dropped_tables],
        )
        invalidation_sql = upgrade_sql[len(dropped_tables):]
        self.assertEqual(invalidation_sql, [
            'DELETE FROM profile_match_progress',
            'DELETE FROM profile_job_matches',
            "DELETE FROM job_applications WHERE job_applications.status = 'new' AND job_applications.auto_created IS true",
            'UPDATE user_profiles SET match_index_version=NULL, presentation_refresh_pending=false',
        ])
        self.assertEqual(created_tables, [
            'profile_llm_scores',
            'ai_matching_calls',
            'ai_matching_selections',
            'ai_matching_settings',
            'ai_matching_tasks',
            'ai_matching_workers',
        ])
        self.assertEqual(created_indexes, [
            ('ix_ai_matching_calls_reserved_at', 'ai_matching_calls'),
            ('ix_ai_matching_tasks_state', 'ai_matching_tasks'),
            ('ix_ai_matching_tasks_available_at', 'ai_matching_tasks'),
        ])
        self.assertTrue(any('GRANT SELECT, INSERT, UPDATE, DELETE ON ' in sql and 'TO hiring_app' in sql for sql in downgrade_sql))
        # Downgrade must be a no-op for profile_job_matches, job_applications,
        # profile_match_progress, and user_profiles rows.
        for sql in downgrade_sql:
            for preserved in ('profile_job_matches', 'job_applications', 'profile_match_progress', 'user_profiles'):
                self.assertNotIn(preserved, sql)


class ReviewFixTests(unittest.TestCase):
    """Mock-based regressions for Journey ordering, deferred presentation refresh and config drift."""

    def test_journey_pages_share_one_ranking_score_for_every_page_size(self):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        from hiring_scraper.app import journey, job_search
        from hiring_scraper.app.applications import CanonicalApplicationState

        ranks = [90, 70, 50, 85, 20, -1, 60, 55, 95]
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)
        states = [CanonicalApplicationState(
            id=index, profile_id=1, job_id=100 + index, vacancy_group_id=None, status='new',
            previous_status=None, created_at=now, updated_at=now, updated_rank=0.0, match_rank=rank,
            discovered_rank=float(index), posted_rank='')
            for index, rank in enumerate(ranks, start=1)]
        jobs = [SimpleNamespace(id=state.job_id) for state in states]
        profile = SimpleNamespace(id=1, name='P')
        # Even rows have faithful stored cards; odd rows force the fallback, whose
        # freshly computed score (99) must never replace the ranking score.
        snapshots = {(1, state.job_id): SimpleNamespace(match=match_compute.MatchRowState(
            match_score=state.match_rank, match_tier='possible', match_uncertain=False, score_version='v',
            explanation={'score': state.match_rank}))
            for state in states if state.id % 2 == 0 and state.match_rank >= 0}

        def page(offset, limit):
            session = MagicMock()
            session.scalar.return_value = None
            session.scalars.return_value.unique.return_value.all.return_value = jobs
            return journey.list_applications(
                profile_ids='1', statuses=None, sort='match', offset=offset, limit=limit, session=session)

        expected = [state.job_id for state in sorted(
            states, key=lambda row: (row.match_rank, row.discovered_rank, row.posted_rank, row.id), reverse=True)]
        with patch.object(journey, 'require_profile', return_value=profile), \
             patch.object(journey, 'canonical_application_states', return_value=states), \
             patch.object(journey, 'profile_match_index_stale', return_value=False), \
             patch.object(journey, 'application_json', side_effect=lambda row: {'id': row.id}), \
             patch.object(search_snapshots, 'job_json', side_effect=lambda job: {'id': job.id}), \
             patch.object(job_search, '_profile_index_version', return_value='v'), \
             patch.object(match_compute, 'load_stored_match_snapshots', return_value=snapshots), \
             patch.object(match_compute, 'snapshot_explanation_is_current', return_value=True), \
             patch.object(profile_semantics, 'load_semantic_documents', return_value=None), \
             patch.object(match_compute, 'compute_match', side_effect=lambda *a, **k: {'score': 99}):
            for size in (1, 2, 4, len(states)):
                items = []
                for offset in range(0, len(states), size):
                    items.extend(page(offset, size)['items'])
                self.assertEqual([item['id'] for item in items], expected, size)
                scores = [item['profile_match']['score'] for item in items if item['profile_match'] is not None]
                self.assertEqual(scores, sorted(scores, reverse=True), size)
                self.assertEqual(sorted(scores, reverse=True), sorted((r for r in ranks if r >= 0), reverse=True))
                unranked = [item for item in items if item['profile_match'] is None]
                self.assertEqual(len(unranked), 1)
                self.assertIs(items[-1], unranked[0])

    def test_presentation_edit_during_backfill_is_remembered_and_applied_at_completion(self):
        from unittest.mock import MagicMock
        from hiring_scraper.app import job_search

        profile = SimpleNamespace(id=7, match_index_version=None, presentation_refresh_pending=False)
        with patch.object(job_search, '_profile_index_version', return_value='current'):
            result = job_search.reevaluate_profile_new_applications(MagicMock(), profile)
        self.assertEqual(result['evaluated'], 0)
        self.assertTrue(profile.presentation_refresh_pending)

        # Once current, the same call evaluates and clears the pending flag.
        profile.match_index_version = 'current'
        session = MagicMock()
        session.scalars.return_value = []
        with patch.object(job_search, '_profile_index_version', return_value='current'):
            job_search.reevaluate_profile_new_applications(session, profile)
        self.assertFalse(profile.presentation_refresh_pending)

    def test_threshold_edit_pages_by_job_id_only(self):
        from unittest.mock import MagicMock
        from hiring_scraper.app import job_search

        profile = SimpleNamespace(id=7, match_index_version='current', presentation_refresh_pending=True)
        job = SimpleNamespace(id=5)
        statements = []

        def scalars(statement):
            statements.append(str(statement))
            return [5] if len(statements) == 1 else [job] if len(statements) == 2 else []

        session = MagicMock()
        session.scalars.side_effect = scalars
        with patch.object(job_search, '_profile_index_version', return_value='current'), \
             patch.object(job_search, 'load_stored_match_snapshots', return_value={}), \
             patch.object(job_search, 'snapshot_explanation_is_current', return_value=False), \
             patch.object(job_search, 'evaluate_stored_new_application', return_value='defer'), \
             patch.object(job_search.source_changes, 'queue_stale_rematch'):
            result = job_search.reevaluate_profile_new_applications(session, profile)
        self.assertEqual(result['deferred'], 1)
        first = statements[0]
        self.assertIn('profile_job_matches.job_id', first)
        for heavy in ('explanation', 'description', 'score_version'):
            self.assertNotIn(heavy, first)


@unittest.skipUnless(os.environ.get('TEST_POSTGRES_URL'), 'Requires PostgreSQL with pgvector (TEST_POSTGRES_URL)')
class MatchComputePostgresIntegrationTests(unittest.TestCase):
    """End-to-end DB tests exercising stored explanations, presentation edits, and dirty-queue repair."""

    def setUp(self):
        from sqlalchemy.orm import sessionmaker
        from tests import pg

        self.pg = pg
        self.engine = pg.engine(self)
        self.factory = sessionmaker(bind=self.engine, future=True)

    def test_stored_explanation_round_trip_presentation_edit_and_dirty_repair(self):
        from sqlalchemy import select
        from hiring_scraper.app import api, job_search, models, profiles, source_changes

        with self.factory() as session:
            saved = profiles.create_profile(profiles.ProfileRequest(
                name='Developer',
                skills=['Python', 'SQL', 'Docker'],
                desired_roles=['Software developer'],
                summary='Built Python services, maintained SQL databases, and deployed Docker containers.',
                experience_years=4,
                language_levels={'English': 'C1', 'German': 'B2'},
                work_styles=['remote', 'hybrid'],
                employment_types=['full_time'],
                search_area={'city': 'Karlsruhe', 'latitude': 49.0069, 'longitude': 8.4037,
                             'radius_km': 35, 'country_code': 'DE'},
                matching_defaults={'min_match_score': 0, 'include_unknown': True},
            ), session)
            profile_id = saved['id']
            company = models.Company(source='test', source_id='p2', name='Example GmbH', country_code='DE')
            feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://example.de/jobs', status='parsed')
            job1 = models.Job(
                feed=feed, external_id='j1', title='Platform Specialist',
                url='https://example.de/jobs/1',
                description='Your tasks:\nBuild Python services and maintain SQL databases.\nDeploy Docker containers.',
                is_remote=True, work_arrangement='remote', employment_type='full_time',
                locations=[models.JobLocation(label='Karlsruhe, Germany', latitude=49.0069, longitude=8.4037,
                                              precision='city', country_code='DE')],
            )
            job2 = models.Job(
                feed=feed, external_id='j2', title='Junior Coordinator',
                url='https://example.de/jobs/2',
                description='Your tasks:\nCoordinate office events and scheduling.',
                is_remote=True, work_arrangement='remote', employment_type='full_time',
                locations=[models.JobLocation(label='Karlsruhe, Germany', latitude=49.0069, longitude=8.4037,
                                              precision='city', country_code='DE')],
            )
            session.add(company)
            session.commit()
            job1_id, job2_id = job1.id, job2.id

        # 1. Seed initial local matches and verify stored explanation reuse.
        with self.factory() as session:
            profile = session.get(models.UserProfile, profile_id)
            job_search.ensure_profile_match_indexes(session, [profile])
            session.commit()
            index_version = profile.match_index_version
            score_version = match_compute.current_score_version()
            row1 = session.get(models.ProfileJobMatch, (profile_id, job1_id))
            row2 = session.get(models.ProfileJobMatch, (profile_id, job2_id))
            self.assertIsNotNone(row1)
            self.assertIsNotNone(row2)
            self.assertEqual(row1.explanation['score'], row1.match_score)
            self.assertEqual(row1.score_version, score_version)
            self.assertEqual(row2.score_version, score_version)

            with patch.object(match_compute, 'match_job', side_effect=AssertionError('Should reuse stored explanation')), \
                 patch.object(profile_semantics, 'load_semantic_documents', side_effect=AssertionError('Should not load vectors on hit')):
                detail = api.get_job(job1_id, session=session, profile_id=profile_id)
            self.assertEqual(detail['profile_match']['score'], row1.match_score)
            self.assertNotIn('_stamp', detail['profile_match'])

            # 2. Presentation-only edit preserves index version & ProfileJobMatch, updates New rows, preserves user-decided 'open'.
            api.set_job_application(profile_id, job2_id, api.ApplicationRequest(status='open'), session)
            # The fixture must score below the new threshold, otherwise keeping the
            # New row would be correct and retraction could not be observed.
            self.assertLess(row1.match_score, 99)
            self.assertIsNotNone(session.scalar(select(models.JobApplication).where(
                models.JobApplication.profile_id == profile_id, models.JobApplication.job_id == job1_id)))
            profiles.update_profile(profile_id, profiles.ProfileRequest(
                name='Developer',
                skills=['Python', 'SQL', 'Docker'],
                desired_roles=['Software developer'],
                summary='Built Python services, maintained SQL databases, and deployed Docker containers.',
                experience_years=4,
                language_levels={'English': 'C1', 'German': 'B2'},
                work_styles=['remote', 'hybrid'],
                employment_types=['full_time'],
                search_area={'city': 'Karlsruhe', 'latitude': 49.0069, 'longitude': 8.4037,
                             'radius_km': 35, 'country_code': 'DE'},
                matching_defaults={'min_match_score': 99, 'include_unknown': True},
            ), session)
            self.assertEqual(session.get(models.UserProfile, profile_id).match_index_version, index_version)
            self.assertIsNotNone(session.get(models.ProfileJobMatch, (profile_id, job1_id)))
            app1 = session.scalar(select(models.JobApplication).where(
                models.JobApplication.profile_id == profile_id, models.JobApplication.job_id == job1_id))
            app2 = session.scalar(select(models.JobApplication).where(
                models.JobApplication.profile_id == profile_id, models.JobApplication.job_id == job2_id))
            self.assertIsNone(app1)
            self.assertEqual(app2.status, 'open')

            # Lower min_match_score back to 0 so job1 returns as New, then edit job1 and mark it dirty.
            profiles.update_profile(profile_id, profiles.ProfileRequest(
                name='Developer',
                skills=['Python', 'SQL', 'Docker'],
                desired_roles=['Software developer'],
                summary='Built Python services, maintained SQL databases, and deployed Docker containers.',
                experience_years=4,
                language_levels={'English': 'C1', 'German': 'B2'},
                work_styles=['remote', 'hybrid'],
                employment_types=['full_time'],
                search_area={'city': 'Karlsruhe', 'latitude': 49.0069, 'longitude': 8.4037,
                             'radius_km': 35, 'country_code': 'DE'},
                matching_defaults={'min_match_score': 0, 'include_unknown': True},
            ), session)
            score_before_edit = row1.match_score
            local_row2_score = row2.match_score
            job1_row = session.get(models.Job, job1_id)
            job1_row.title = 'Office Receptionist'
            job1_row.description = 'Your tasks:\nAnswer phones and greet front desk visitors.'
            source_changes.jobs_changed(session, [job1_id])
            session.commit()

        # 3. Pre-drain: only job1 is dirty; Journey keeps rank == displayed card score for job1 (flagged refreshing),
        # while clean job2 stays current without refreshing or vector loads.
        with self.factory() as session:
            self.assertEqual(session.get(models.UserProfile, profile_id).match_index_version, index_version)
            dirty_ids = session.scalars(select(models.MatchDirtyJob.job_id)).all()
            self.assertEqual(dirty_ids, [job1_id])

            with patch.object(match_compute, 'match_job', side_effect=AssertionError('Journey should not recompute faithful stored cards')), \
                 patch.object(profile_semantics, 'load_semantic_documents', side_effect=AssertionError('Journey should not load vectors for stored cards')):
                journey_pre = api.list_applications(
                    profile_ids=str(profile_id), statuses='all', sort='match', session=session,
                )
            self.assertTrue(journey_pre['matching_pending'])
            by_job_pre = {item['id']: item['profile_match'] for item in journey_pre['items']}
            self.assertEqual(by_job_pre[job1_id]['score'], score_before_edit)
            self.assertTrue(by_job_pre[job1_id].get('refreshing'))
            self.assertEqual(by_job_pre[job2_id]['score'], local_row2_score)
            self.assertNotIn('refreshing', by_job_pre[job2_id])

            # Pre-drain: detail for clean job2 reuses stored explanation; detail for dirty job1 computes live local score.
            with patch.object(match_compute, 'match_job', side_effect=AssertionError('Clean row must stay reusable')):
                detail2 = api.get_job(job2_id, session=session, profile_id=profile_id)
            self.assertEqual(detail2['profile_match']['score'], local_row2_score)
            detail1 = api.get_job(job1_id, session=session, profile_id=profile_id)
            self.assertNotEqual(detail1['profile_match']['score'], score_before_edit)

        # 4. Post-drain: running the dirty worker once repairs job1 to its new local score and clears refreshing.
        self.assertTrue(job_search.refresh_dirty_job_matches_once(self.factory))
        with self.factory() as session:
            self.assertEqual(session.scalars(select(models.MatchDirtyJob.job_id)).all(), [])
            repaired_row1 = session.get(models.ProfileJobMatch, (profile_id, job1_id))
            self.assertEqual(repaired_row1.score_version, match_compute.current_score_version())
            self.assertEqual(repaired_row1.match_score, detail1['profile_match']['score'])

            with patch.object(match_compute, 'match_job', side_effect=AssertionError('Post-drain Journey must reuse stored cards')), \
                 patch.object(profile_semantics, 'load_semantic_documents', side_effect=AssertionError('Post-drain Journey must not load vectors')):
                journey_post = api.list_applications(
                    profile_ids=str(profile_id), statuses='all', sort='match', session=session,
                )
            self.assertFalse(journey_post['matching_pending'])
            by_job_post = {item['id']: item['profile_match'] for item in journey_post['items']}
            self.assertEqual(by_job_post[job1_id]['score'], repaired_row1.match_score)
            self.assertNotIn('refreshing', by_job_post[job1_id])


    # Production sessions (SessionLocal) disable autoflush and expiry on commit.
    # Mirror that so stale ORM instances and unflushed in-memory writes behave as in prod.
    def _prod_session(self):
        from sqlalchemy.orm import sessionmaker
        return sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    @staticmethod
    def _threshold_request(threshold):
        from hiring_scraper.app import profiles
        return profiles.ProfileRequest(
            name='Developer', skills=['Python', 'SQL', 'Docker'], desired_roles=['Software developer'],
            summary='Built Python services, maintained SQL databases, and deployed Docker containers.',
            experience_years=4, language_levels={'English': 'C1', 'German': 'B2'},
            work_styles=['remote', 'hybrid'], employment_types=['full_time'],
            search_area={'city': 'Karlsruhe', 'latitude': 49.0069, 'longitude': 8.4037,
                         'radius_km': 35, 'country_code': 'DE'},
            matching_defaults={'min_match_score': threshold, 'include_unknown': True})

    def _seed_backfill_profile(self, factory):
        from hiring_scraper.app import models, profiles
        with factory() as session:
            profile_id = profiles.create_profile(self._threshold_request(0), session)['id']
            company = models.Company(source='test', source_id='bf', name='Example GmbH', country_code='DE')
            feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://example.de/bf', status='parsed')
            location = dict(label='Karlsruhe, Germany', latitude=49.0069, longitude=8.4037,
                            precision='city', country_code='DE')
            jobs = [models.Job(
                feed=feed, external_id=f'bf{index}', title=title, url=f'https://example.de/bf/{index}',
                description='Your tasks:\nBuild Python services and maintain SQL databases.\nDeploy Docker containers.',
                is_remote=True, work_arrangement='remote', employment_type='full_time',
                locations=[models.JobLocation(**location)])
                for index, title in enumerate(('Platform Specialist', 'Platform Specialist Two'), start=1)]
            session.add(company)
            session.commit()
            return profile_id, jobs[0].id, jobs[1].id

    def _new_row(self, factory, profile_id, job_id):
        from sqlalchemy import select
        from hiring_scraper.app import models
        with factory() as session:
            return session.scalar(select(models.JobApplication).where(
                models.JobApplication.profile_id == profile_id, models.JobApplication.job_id == job_id))

    def test_presentation_edit_during_backfill_is_applied_when_backfill_completes(self):
        from hiring_scraper.app import job_search, models, profiles

        factory = self._prod_session()
        profile_id, first_id, second_id = self._seed_backfill_profile(factory)
        with patch.object(job_search, 'SessionLocal', factory), \
             patch.object(job_search, '_MATCH_WORK_PAGE_SIZE', 1):
            self.assertTrue(job_search.refresh_profile_match_indexes_once())  # page 1 (job 1)
            with factory() as session:
                self.assertIsNone(session.get(models.UserProfile, profile_id).match_index_version)
                self.assertLess(session.get(models.ProfileJobMatch, (profile_id, first_id)).match_score, 99)
            self.assertIsNotNone(self._new_row(factory, profile_id, first_id))
            with factory() as session:
                # Threshold edit mid-backfill: remembered, checkpoint and page-1 row untouched.
                profiles.update_profile(profile_id, self._threshold_request(99), session)
            with factory() as session:
                self.assertTrue(session.get(models.UserProfile, profile_id).presentation_refresh_pending)
            self.assertIsNotNone(self._new_row(factory, profile_id, first_id))
            # Page 2 completes the index; with autoflush off the version must be
            # flushed before re-evaluating or every row would be deferred.
            self.assertTrue(job_search.refresh_profile_match_indexes_once())
        with factory() as session:
            profile = session.get(models.UserProfile, profile_id)
            self.assertIsNotNone(profile.match_index_version)
            self.assertFalse(profile.presentation_refresh_pending)
        self.assertIsNone(self._new_row(factory, profile_id, first_id))
        self.assertIsNone(self._new_row(factory, profile_id, second_id))

    def test_threshold_edit_with_stale_profile_instance_after_backfill_applies_immediately(self):
        from hiring_scraper.app import job_search, models, profiles

        factory = self._prod_session()
        profile_id, first_id, second_id = self._seed_backfill_profile(factory)
        with patch.object(job_search, 'SessionLocal', factory), \
             patch.object(job_search, '_MATCH_WORK_PAGE_SIZE', 1):
            self.assertTrue(job_search.refresh_profile_match_indexes_once())  # page 1
            with factory() as request_session:
                # The request loads the profile while the index is still building...
                stale = profiles.require_profile(profile_id, request_session)
                self.assertIsNone(stale.match_index_version)
                # ...the last backfill page commits before its write lock is taken...
                self.assertTrue(job_search.refresh_profile_match_indexes_once())
                self.assertIsNone(stale.match_index_version)  # still stale in the ORM
                # ...so the edit must decide on the locked, current row, not its stale copy.
                profiles.update_profile(profile_id, self._threshold_request(99), request_session)
        with factory() as session:
            profile = session.get(models.UserProfile, profile_id)
            self.assertIsNotNone(profile.match_index_version)
            self.assertFalse(profile.presentation_refresh_pending)
        self.assertIsNone(self._new_row(factory, profile_id, first_id))
        self.assertIsNone(self._new_row(factory, profile_id, second_id))

    def test_worker_applies_stuck_presentation_flag_on_current_index(self):
        from copy import deepcopy
        from hiring_scraper.app import job_search, models

        factory = self._prod_session()
        profile_id, first_id, second_id = self._seed_backfill_profile(factory)
        with patch.object(job_search, 'SessionLocal', factory), \
             patch.object(job_search, '_MATCH_WORK_PAGE_SIZE', 1):
            while job_search.refresh_profile_match_indexes_once():
                pass
            self.assertIsNotNone(self._new_row(factory, profile_id, first_id))
            with factory() as session:
                profile = session.get(models.UserProfile, profile_id)
                self.assertIsNotNone(profile.match_index_version)
                preferences = deepcopy(profile.preferences)
                preferences['matching_defaults']['min_match_score'] = 99
                profile.preferences = preferences
                profile.presentation_refresh_pending = True  # as left by the old race
                session.commit()
            self.assertTrue(job_search.refresh_profile_match_indexes_once())
        with factory() as session:
            self.assertFalse(session.get(models.UserProfile, profile_id).presentation_refresh_pending)
        self.assertIsNone(self._new_row(factory, profile_id, first_id))
        self.assertIsNone(self._new_row(factory, profile_id, second_id))


if __name__ == '__main__':
    unittest.main()
