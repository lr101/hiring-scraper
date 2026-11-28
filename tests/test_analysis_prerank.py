"""Pre-ranking gate, age cutoff, user-intent overrides, and queue reconciliation for LLM job analysis."""
from __future__ import annotations

import math
import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import api, companion, journey, models, semantic_search_worker, source_changes
from hiring_scraper.app import job_analysis_worker as worker
from hiring_scraper.app.analysis_gate import (
    enqueue_priority_analysis, profile_embedding_hash, profile_embedding_text,
)
from hiring_scraper.app.models import utcnow
from hiring_scraper.semantic_matching import MODEL_KEY
from tests import pg

LLM_OUTPUT = ('OCC software development\nIND software\nDUTIES\nD 1 | Build Python services.\n'
              'REQUIREMENTS\nR 2 must experience | Experience with PostgreSQL.\nEND')


def _unit_vec(angle_cos: float, dim: int = 1) -> list[float]:
    """Return a 384-d unit vector with cosine ``angle_cos`` to basis vector e_0."""
    angle_cos = max(-1.0, min(1.0, float(angle_cos)))
    ortho = math.sqrt(max(0.0, 1.0 - angle_cos * angle_cos))
    vec = [0.0] * 384
    vec[0] = angle_cos
    vec[dim] = ortho
    return vec


class AnalysisPrerankTests(unittest.TestCase):
    def setUp(self):
        self.engine = pg.engine(self)
        self.factory = sessionmaker(self.engine, autoflush=False, expire_on_commit=False)
        with self.factory.begin() as session:
            self.company = models.Company(source='test', source_id='emp', name='Employer')
            self.feed = models.JobFeed(
                company=self.company, provider='rss', status='parsed', feed_url='https://example.test/feed')
            profile = models.UserProfile(
                name='CountryWide',
                preferences={
                    'desired_roles': ['Backend Engineer'],
                    'skills': ['Python', 'PostgreSQL'],
                    'summary': 'Python backend engineer.',
                    'search_area': {'search_scope': 'country', 'country_code': 'DE', 'label': 'Germany'},
                },
            )
            session.add_all([self.company, self.feed, profile])
            session.flush()
            self.feed_id = self.feed.id
            self.profile_id = profile.id
            self._set_profile_vector(session, profile, _unit_vec(1.0))

        self.patches = [
            patch.object(worker, 'SessionLocal', self.factory),
            patch.object(worker, '_SCORED_PICKS', 0),  # exploration rotation starts fresh per test
            patch.object(semantic_search_worker, 'SessionLocal', self.factory),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        worker._PROFILE_CACHE.clear()

    def _set_profile_vector(self, session, profile, vector: list[float]) -> None:
        text = profile_embedding_text(profile.matching_preferences)
        session.merge(models.SemanticSearchDocument(
            entity_type='profile',
            entity_id=profile.id,
            content_hash=profile_embedding_hash(text),
            model_revision=MODEL_KEY,
            embedding=vector,
        ))

    def _add_job(
        self,
        ext_id: str,
        *,
        date_posted: str | None = None,
        first_seen_at=None,
        vector: list[float] | None = None,
        enqueue: bool = True,
        location: tuple[str, float, float] | None = None,
        is_active: bool = True,
        locations: list[tuple[str, str | None, float, float]] = (),
        **fields,
    ) -> int:
        with self.factory.begin() as session:
            job = models.Job(
                feed_id=self.feed_id,
                external_id=ext_id,
                url=f'https://example.test/{ext_id}',
                date_posted=date_posted,
                first_seen_at=first_seen_at or utcnow(),
                is_active=is_active,
                **{'title': f'Role {ext_id}', 'description': 'Build Python services.\nExperience with PostgreSQL.',
                   **fields},
            )
            if location is not None:
                label, lat, lon = location
                job.locations.append(models.JobLocation(label=label, latitude=lat, longitude=lon, country_code='DE'))
            job.locations.extend(models.JobLocation(label=label, latitude=lat, longitude=lon, country_code=code)
                                 for label, code, lat, lon in locations)
            session.add(job)
            session.flush()
            if vector is not None:
                session.add(models.SemanticSearchDocument(
                    entity_type='job',
                    entity_id=job.id,
                    content_hash=f'hash-{ext_id}',
                    model_revision=MODEL_KEY,
                    embedding=vector,
                ))
            if enqueue:
                source_changes.jobs_changed(session, [job.id])
            return job.id

    def test_age_cutoff_text_comparison_and_junk_date_safety(self):
        today = date.today()
        fresh_iso = (today - timedelta(days=3)).isoformat()
        expired_iso = (today - timedelta(days=30)).isoformat()
        old_seen = utcnow() - timedelta(days=30)

        fresh_id = self._add_job('fresh-iso', date_posted=fresh_iso, vector=_unit_vec(0.92))
        expired_id = self._add_job('expired-iso', date_posted=expired_iso, vector=_unit_vec(0.95))
        # Production holds invalid ISO-like dates such as 2026-98-12 that would crash ::date casts.
        junk_id = self._add_job('junk-date', date_posted='2026-98-12', vector=_unit_vec(0.91))
        fallback_fresh_id = self._add_job('fallback-fresh', date_posted='vor 2 Tagen', vector=_unit_vec(0.90))
        fallback_old_id = self._add_job(
            'fallback-old', date_posted='unparseable', first_seen_at=old_seen, vector=_unit_vec(0.96))

        # source_changes should not even enqueue expired postings without user intent.
        with self.factory() as session:
            queued = set(session.scalars(select(models.JobAnalysisQueue.job_id)).all())
        self.assertIn(fresh_id, queued)
        self.assertIn(junk_id, queued)
        self.assertIn(fallback_fresh_id, queued)
        self.assertNotIn(expired_id, queued)
        self.assertNotIn(fallback_old_id, queued)

        self.assertEqual(worker.refresh_prerank_scores_once(), 3)
        analysed = []
        while (job_id := worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT)) is not None:
            analysed.append(job_id)
        # Highest similarity among eligible fresh rows first (fresh 0.92, junk 0.91, fallback_fresh 0.90).
        self.assertEqual(analysed, [fresh_id, junk_id, fallback_fresh_id])

    def test_foreign_workplaces_skip_background_analysis(self):
        foreign_id = self._add_job('foreign', vector=_unit_vec(0.99), location_text='Kraków, Poland')
        german_id = self._add_job('german', vector=_unit_vec(0.95), location_text='Bad Vilbel')
        mixed_id = self._add_job('mixed', vector=_unit_vec(0.94), location_text='Berlin / Kraków, Poland')
        remote_id = self._add_job('remote', vector=_unit_vec(0.93), location_text='India', is_remote=True)
        unknown_id = self._add_job('unknown', vector=_unit_vec(0.92))
        self.assertEqual(worker.refresh_prerank_scores_once(), 5)

        with self.factory() as session:
            progress = journey.analysis_progress(session, [self.profile_id])['postings']
            self.assertEqual(progress['queued'], 4)
            self.assertEqual(progress['skipped'], 1)

        analyse = lambda _lines: LLM_OUTPUT
        self.assertEqual(worker.analyse_next_jobs(5, analyse=analyse),
                         [german_id, mixed_id, remote_id, unknown_id])
        with self.factory.begin() as session:
            enqueue_priority_analysis(session, foreign_id)
        self.assertEqual(worker.analyse_next_job(analyse=analyse), foreign_id)

    def test_structured_workplace_gate_uses_current_locations(self):
        job_id = self._add_job('structured', vector=_unit_vec(0.99), location_text='Kraków')
        with self.factory.begin() as session:
            job = session.get(models.Job, job_id)
            job.locations.append(models.JobLocation(label='Kraków', country_code='pl'))
            source_changes.jobs_changed(session, [job_id])
        self.assertEqual(worker.refresh_prerank_scores_once(), 1)
        analyse = lambda _lines: self.fail('The worker called the LLM for a foreign-only posting')
        self.assertIsNone(worker.analyse_next_job(analyse=analyse))

        with self.factory.begin() as session:
            job = session.get(models.Job, job_id)
            job.locations.append(models.JobLocation(label='Lindenberg'))
            source_changes.jobs_changed(session, [job_id])
        worker.refresh_prerank_scores_once()
        self.assertEqual(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT), job_id)

    def test_similarity_prerank_order_threshold_and_unembedded_waiting(self):
        high_id = self._add_job('high', vector=_unit_vec(0.95))
        mid_id = self._add_job('mid', vector=_unit_vec(0.84))
        low_id = self._add_job('low', vector=_unit_vec(0.65))
        unembedded_id = self._add_job('unembedded', vector=None)

        self.assertEqual(worker.refresh_prerank_scores_once(), 3)
        # Descending similarity; low-sim (< 0.80) and unembedded do not run and do not block.
        self.assertEqual(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT), high_id)
        self.assertEqual(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT), mid_id)
        self.assertIsNone(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT))

        # Unembedded posting stayed in the queue (not dropped) and runs once embedded.
        with self.factory() as session:
            self.assertIsNotNone(session.get(models.JobAnalysisQueue, unembedded_id))
            self.assertIsNotNone(session.get(models.JobAnalysisQueue, low_id))
            session.add(models.SemanticSearchDocument(
                entity_type='job', entity_id=unembedded_id, content_hash='hash-unembedded',
                model_revision=MODEL_KEY, embedding=_unit_vec(0.88),
            ))
            session.commit()

        self.assertEqual(worker.refresh_prerank_scores_once(), 1)
        self.assertEqual(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT), unembedded_id)
        self.assertIsNone(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT))

    def test_title_naming_a_target_role_and_freshness_lift_the_queue_order(self):
        plain_id = self._add_job('plain', vector=_unit_vec(0.86))
        named_id = self._add_job('named', vector=_unit_vec(0.83), title='Senior Backend Engineer (m/w/d)')
        partial_id = self._add_job('partial', vector=_unit_vec(0.85), title='Backend Developer')
        older_id = self._add_job('older', vector=_unit_vec(0.87), first_seen_at=utcnow() - timedelta(days=6))

        self.assertEqual(worker.refresh_prerank_scores_once(), 4)
        with self.factory() as session:
            stored = session.get(models.JobAnalysisQueue, named_id).similarity
        self.assertAlmostEqual(stored, 0.89, places=3)  # 0.83 + the full title boost
        analysed = []
        while (job_id := worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT)) is not None:
            analysed.append(job_id)
        # Full title match first; one of two role words lifts less; six days cost the older posting its lead.
        self.assertEqual(analysed, [named_id, partial_id, plain_id, older_id])

    def test_every_fourth_slot_reads_the_best_plain_similarity_despite_title_boosts(self):
        named = [self._add_job(f'named-{index}', vector=_unit_vec(0.82 + index / 100), title='Backend Engineer')
                 for index in range(4)]
        hidden_fit_id = self._add_job('hidden-fit', vector=_unit_vec(0.87), title='Platform Specialist')
        self.assertEqual(worker.refresh_prerank_scores_once(), 5)
        with self.factory() as session:
            raw = session.get(models.JobAnalysisQueue, hidden_fit_id).raw_similarity
        self.assertAlmostEqual(raw, 0.87, places=3)

        analysed = [worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT) for _ in range(4)]
        # Boosted titles (0.88-0.91) outrank the untitled fit (0.87), but the fourth slot
        # goes to the best plain similarity.
        self.assertEqual(analysed, [named[3], named[2], named[1], hidden_fit_id])
        with patch.dict(os.environ, {'RADIUS_ANALYSIS_EXPLORE_EVERY': '0'}):
            self.assertEqual(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT), named[0])

    def test_identical_posting_text_is_analysed_once(self):
        first_id = self._add_job('first', vector=_unit_vec(0.95))
        copy_id = self._add_job('copy', vector=_unit_vec(0.94), title='Role first')
        other_city_id = self._add_job('other-city', vector=_unit_vec(0.93), title='Role first (Hamburg)')
        different_id = self._add_job('different', vector=_unit_vec(0.92), description='Plan Python services.\nExperience with MySQL.')
        self.assertEqual(worker.refresh_prerank_scores_once(), 4)

        calls = []
        while worker.analyse_next_job(analyse=lambda lines: calls.append(lines) or LLM_OUTPUT) is not None:
            pass
        self.assertEqual(len(calls), 2)  # first and different; both copies reuse the first analysis
        with self.factory() as session:
            analyses = {row.job_id: row for row in session.scalars(select(models.JobAnalysis))}
            evidence = {row.job_id for row in session.scalars(select(models.MatchEvidenceQueue))}
        self.assertEqual({job_id: row.status for job_id, row in analyses.items()},
                         dict.fromkeys((first_id, copy_id, other_city_id, different_id), 'ok'))
        reused = analyses[other_city_id].payload
        self.assertEqual(reused['lines'][0], 'Job title: Role first (Hamburg)')
        self.assertEqual(reused['duties'], analyses[first_id].payload['duties'])
        self.assertEqual(reused['reused_from'], first_id)
        self.assertNotEqual(analyses[other_city_id].result_hash, analyses[first_id].result_hash)
        self.assertLessEqual({copy_id, other_city_id}, evidence)

    def test_local_area_preference_still_applies_for_real_area_only(self):
        with self.factory.begin() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            profile.preferences = {
                **profile.preferences,
                'search_area': {
                    'latitude': 49.0069, 'longitude': 8.4037, 'radius_km': 25,
                    'city': 'Karlsruhe', 'country_code': 'DE',
                },
            }
            profile.source_version += 1
        worker._PROFILE_CACHE.clear()

        far_high_id = self._add_job('far-high', vector=_unit_vec(0.96), location=('Hamburg', 53.55, 9.99))
        near_mid_id = self._add_job('near-mid', vector=_unit_vec(0.82), location=('Karlsruhe', 49.01, 8.40))
        near_high_id = self._add_job('near-high', vector=_unit_vec(0.90), location=('Karlsruhe', 49.01, 8.40))
        near_low_id = self._add_job('near-low', vector=_unit_vec(0.50), location=('Karlsruhe', 49.01, 8.40))

        self.assertEqual(worker.refresh_prerank_scores_once(), 4)
        # In-area eligible jobs come first in descending similarity, then out-of-area eligible; below-threshold skipped.
        self.assertEqual(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT), near_high_id)
        self.assertEqual(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT), near_mid_id)
        self.assertEqual(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT), far_high_id)
        self.assertIsNone(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT))
        with self.factory() as session:
            self.assertIsNone(session.get(models.JobAnalysis, near_low_id))

    def test_user_intent_overrides_age_and_similarity_with_priority(self):
        old_date = (date.today() - timedelta(days=45)).isoformat()
        regular_id = self._add_job('regular', vector=_unit_vec(0.95))
        saved_id = self._add_job('saved-old-low', date_posted=old_date, vector=_unit_vec(0.10), enqueue=False)
        moved_id = self._add_job('moved-old-low', date_posted=old_date, vector=_unit_vec(0.10), enqueue=False)
        opened_id = self._add_job('opened-old-low', date_posted=old_date, vector=_unit_vec(0.10), enqueue=False)
        auto_new_id = self._add_job('auto-new-old', date_posted=old_date, vector=_unit_vec(0.10), enqueue=False)

        with self.factory.begin() as session:
            # Auto-created 'new' application is NOT user intent.
            session.add(models.JobApplication(
                profile_id=self.profile_id, job_id=auto_new_id, status='new', auto_created=True))
            # Companion-saved ('new', auto_created=False) IS user intent.
            session.add(models.JobApplication(
                profile_id=self.profile_id, job_id=saved_id, status='new', auto_created=False))
        with self.factory() as session:
            # Stage move via Journey API IS user intent.
            journey.set_job_application(
                self.profile_id, moved_id, journey.ApplicationRequest(status='open'), session=session)
            # Opening job detail page with include_analysis=True IS user intent.
            api.get_job(opened_id, session, include_analysis=True)
            # Opening with include_analysis=False does NOT enqueue auto_new_id.
            api.get_job(auto_new_id, session, include_analysis=False)

        worker.reconcile_queues()
        worker.refresh_prerank_scores_once()

        # All three user-intent postings must run BEFORE the regular high-similarity posting.
        first_three = {
            worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT),
            worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT),
            worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT),
        }
        self.assertEqual(first_three, {saved_id, moved_id, opened_id})
        self.assertEqual(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT), regular_id)
        # auto_created='new' old/low-sim posting was never analysed or queued.
        self.assertIsNone(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT))
        with self.factory() as session:
            self.assertIsNone(session.get(models.JobAnalysis, auto_new_id))
            self.assertIsNone(session.get(models.JobAnalysisQueue, auto_new_id))

    def test_companion_save_priority_enqueues_old_posting(self):
        old_date = (date.today() - timedelta(days=60)).isoformat()
        job_id = self._add_job('companion-job', date_posted=old_date, vector=_unit_vec(0.05), enqueue=False)
        with self.factory() as session:
            job = session.get(models.Job, job_id)
            response = companion.save_companion_job(
                companion.SaveJobRequest(
                    profile_id=self.profile_id,
                    url=job.url,
                    title=job.title,
                    company_name='Employer',
                ),
                session=session,
            )
            self.assertEqual(response['job']['id'], job_id)
        with self.factory() as session:
            queued = session.get(models.JobAnalysisQueue, job_id)
            self.assertIsNotNone(queued)
            self.assertTrue(queued.priority)
        self.assertEqual(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT), job_id)

    def test_reconcile_prunes_expired_non_priority_and_gate_changes_reenable_postings(self):
        old_date = (date.today() - timedelta(days=30)).isoformat()
        expired_id = self._add_job('expired-queued', date_posted=old_date, vector=_unit_vec(0.95), enqueue=False)
        low_id = self._add_job('low-then-matched', vector=_unit_vec(0.0, dim=2))
        with self.factory.begin() as session:
            # Simulate a legacy pre-existing queue row for an old posting.
            session.add(models.JobAnalysisQueue(job_id=expired_id, priority=False))

        worker.reconcile_queues()
        with self.factory() as session:
            self.assertIsNone(session.get(models.JobAnalysisQueue, expired_id))
            self.assertIsNotNone(session.get(models.JobAnalysisQueue, low_id))

        # low_id has cosine 0.0 against current profile -> skipped.
        self.assertEqual(worker.refresh_prerank_scores_once(), 1)
        self.assertIsNone(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT))

        # Gate change 1: lowering RADIUS_ANALYSIS_MIN_SIMILARITY immediately unlocks low_id.
        with patch.dict(os.environ, {'RADIUS_ANALYSIS_MIN_SIMILARITY': '0.0'}):
            with self.factory() as session:
                profiles = session.scalars(select(models.UserProfile)).all()
                picks = worker.next_jobs_to_analyse(session, profiles, limit=5)
                self.assertEqual([job.id for job in picks], [low_id])

        # Gate change 2: adding a new profile whose vector matches dim=2 re-scores and unlocks low_id.
        with self.factory.begin() as session:
            second = models.UserProfile(name='Second', preferences={'desired_roles': ['ML Engineer']})
            session.add(second)
            session.flush()
            self._set_profile_vector(session, second, _unit_vec(0.0, dim=2))
        worker._PROFILE_CACHE.clear()
        self.assertEqual(worker.refresh_prerank_scores_once(), 1)
        self.assertEqual(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT), low_id)

        # Gate change 3: raising RADIUS_ANALYSIS_MAX_AGE_DAYS re-enqueues expired_id on reconcile.
        with patch.dict(os.environ, {'RADIUS_ANALYSIS_MAX_AGE_DAYS': '60'}):
            worker.reconcile_queues()
            self.assertEqual(worker.refresh_prerank_scores_once(), 1)
            self.assertEqual(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT), expired_id)

    def test_job_reembedding_clears_stale_prerank_score(self):
        job_id = self._add_job('reembed', vector=_unit_vec(0.10))
        self.assertEqual(worker.refresh_prerank_scores_once(), 1)
        self.assertIsNone(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT))

        # Updating the job's embedding via semantic_search_worker resets cached similarity.
        semantic_search_worker._save_vectors(
            [('job', job_id)],
            None,
            [_unit_vec(0.95)],
            {('job', job_id): ('text', 'new-hash')},
        )
        with self.factory() as session:
            row = session.get(models.JobAnalysisQueue, job_id)
            self.assertIsNone(row.similarity)
            self.assertIsNone(row.profile_set_hash)

        self.assertEqual(worker.refresh_prerank_scores_once(), 1)
        self.assertEqual(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT), job_id)

    def test_cheap_rules_keep_hidden_postings_from_the_llm(self):
        with self.factory.begin() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            profile.preferences = {**profile.preferences, 'excluded_terms': ['Werkstudent']}
            profile.source_version += 1
        kept_id = self._add_job('kept', vector=_unit_vec(0.95))
        # A small German town the gazetteer may not know is never treated as abroad.
        german_town_id = self._add_job('german-town', vector=_unit_vec(0.94), location_text='Pfinztal')
        foreign_id = self._add_job('foreign', vector=_unit_vec(0.93), location_text='Cebu',
                                   locations=[('Cebu', 'PH', 10.3, 123.9)])
        empty_id = self._add_job('empty', vector=_unit_vec(0.92), description='')
        excluded_id = self._add_job('excluded', vector=_unit_vec(0.91), title='Werkstudent Backend Engineer')
        saved_foreign_id = self._add_job('saved-foreign', vector=_unit_vec(0.90), location_text='Kraków, Poland')
        with self.factory.begin() as session:
            session.get(models.JobAnalysisQueue, saved_foreign_id).priority = True  # saved or opened by the user

        self.assertEqual(worker.refresh_prerank_scores_once(), 6)
        with self.factory() as session:
            similarity = dict(session.execute(select(models.JobAnalysisQueue.job_id,
                                                     models.JobAnalysisQueue.similarity)).all())
            progress = journey.analysis_progress(session, [self.profile_id])
        for job_id in (foreign_id, empty_id, excluded_id, saved_foreign_id):
            self.assertEqual(similarity[job_id], -2.0)
        self.assertGreater(similarity[german_town_id], 0.9)
        self.assertEqual(progress['postings']['skipped'], 3)

        analysed = []
        while (job_id := worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT)) is not None:
            analysed.append(job_id)
        # User intent still overrides every rule; ruled-out rows stay queued for a later re-check.
        self.assertEqual(analysed, [saved_foreign_id, kept_id, german_town_id])
        with self.factory() as session:
            self.assertIsNotNone(session.get(models.JobAnalysisQueue, excluded_id))

        # A profile edit changes the profile set hash, so ruled-out postings are checked again.
        with self.factory.begin() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            profile.preferences = {**profile.preferences, 'excluded_terms': []}
            profile.source_version += 1
        self.assertEqual(worker.refresh_prerank_scores_once(), 3)
        self.assertEqual(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT), excluded_id)
        self.assertIsNone(worker.analyse_next_job(analyse=lambda _lines: LLM_OUTPUT))

    def test_deleting_profile_purges_its_semantic_document_via_trigger(self):
        with self.factory.begin() as session:
            self.assertIsNotNone(session.get(models.SemanticSearchDocument, ('profile', self.profile_id)))
            session.delete(session.get(models.UserProfile, self.profile_id))
        with self.factory() as session:
            self.assertIsNone(session.get(models.SemanticSearchDocument, ('profile', self.profile_id)))


if __name__ == '__main__':
    unittest.main()
