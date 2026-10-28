"""Application tracking persists independently of vacancy and matching lifecycles."""
import unittest
import importlib
import sqlite3
from datetime import timedelta
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from hiring_scraper.app import api, job_search, models, profiles
from hiring_scraper.app.database import initialize_sqlite_schema


class JobApplicationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        models.Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.profile = profiles.create_profile(profiles.ProfileRequest(name='Developer'), self.session)
        self.other = profiles.create_profile(profiles.ProfileRequest(name='Designer'), self.session)
        company = models.Company(source='test', source_id='employer', name='Employer')
        feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://example.test/jobs', status='parsed')
        self.jobs = [models.Job(feed=feed, external_id=str(index), title=f'Role {index}',
                               url=f'https://example.test/jobs/{index}', is_remote=True)
                     for index in range(8)]
        self.session.add(company)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def save(self, job, status='new', profile=None):
        endpoint = getattr(api, 'set_job_application', None)
        self.assertIsNotNone(endpoint, 'Jobs must support saving application status')
        return endpoint((profile or self.profile)['id'], job.id,
                        api.ApplicationRequest(status=status), self.session)

    def overview(self, profile=None, **kwargs):
        endpoint = getattr(api, 'list_job_applications', None)
        self.assertIsNotNone(endpoint, 'Profiles must expose an application overview')
        return endpoint((profile or self.profile)['id'], session=self.session, **kwargs)

    def test_production_journey_returns_saved_roles_before_rebuilding_matches(self):
        self.save(self.jobs[0], 'open')
        with patch.object(api, 'IS_SQLITE', False):
            page = api.list_applications(profile_ids=str(self.profile['id']),
                                         statuses='all', session=self.session)
        self.assertEqual([item['id'] for item in page['items']], [self.jobs[0].id])
        self.assertEqual(page['counts']['new'], 0)
        self.assertTrue(page['matching_pending'])
        self.assertIsNone(self.session.get(models.UserProfile, self.profile['id']).match_index_version)

    def test_sqlite_journey_keeps_saved_roles_when_match_rebuild_conflicts(self):
        self.save(self.jobs[0], 'open')
        def conflict(session, profiles):
            session.add(models.JobApplication(profile_id=self.profile['id'], job_id=self.jobs[1].id,
                status='new', auto_created=True))
            session.flush()
            raise job_search.ProfileIndexChanged('inventory changed')
        with patch.object(api, 'IS_SQLITE', True), patch.object(api, 'ensure_profile_match_indexes', side_effect=conflict):
            page = api.list_applications(profile_ids=str(self.profile['id']), statuses='all', session=self.session)
        self.assertTrue(page['matching_pending'])
        self.assertEqual([item['id'] for item in page['items']], [self.jobs[0].id])
        self.assertEqual(page['items'][0]['application']['status'], 'open')
        self.assertEqual(self.session.scalar(select(func.count()).select_from(models.JobApplication)), 1)

    def duplicate_job(self, original, *, url=None, metadata=None, **fields):
        feed = models.JobFeed(company=original.feed.company, provider='greenhouse',
            tenant='translated', feed_url='https://example.test/translated', status='parsed')
        duplicate = models.Job(feed=feed, external_id='translated', title='Translated role',
            url=url or original.url + '?utm_source=another-feed', is_remote=True,
            raw_metadata=metadata or {}, **fields)
        self.session.add(duplicate)
        self.session.commit()
        return duplicate

    def journey(self, **kwargs):
        # Exercise the production read path without a synchronous match rebuild.
        with patch.object(api, 'IS_SQLITE', False):
            return api.list_applications(profile_ids=str(self.profile['id']),
                                         statuses='all', session=self.session, **kwargs)

    def test_journey_deduplicates_tracking_urls_before_counts_and_pagination(self):
        duplicate = self.duplicate_job(self.jobs[0])
        self.save(self.jobs[0])
        self.save(duplicate)
        self.save(self.jobs[1])
        for sort in ('match', 'posted', 'discovered'):
            with self.subTest(sort=sort):
                first = self.journey(sort=sort, limit=1)
                second = self.journey(sort=sort, offset=1, limit=1)
                third = self.journey(sort=sort, offset=2, limit=1)
                self.assertEqual(first['total'], 2)
                self.assertEqual(first['counts']['new'], 2)
                self.assertEqual({first['items'][0]['id'], second['items'][0]['id']},
                                 {self.jobs[0].id, self.jobs[1].id})
                self.assertEqual(third['items'], [])

    def test_saved_stage_wins_over_new_duplicate_before_status_filtering(self):
        duplicate = self.duplicate_job(self.jobs[0], description='Richer source copy. ' * 100)
        self.save(self.jobs[0], 'interview')
        self.session.add(models.JobApplication(profile_id=self.profile['id'], job_id=duplicate.id,
            status='new', auto_created=True, updated_at=models.utcnow() + timedelta(days=1)))
        self.session.commit()
        page = self.journey()
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['items'][0]['id'], self.jobs[0].id)
        self.assertEqual(page['items'][0]['application']['status'], 'interview')
        with patch.object(api, 'IS_SQLITE', False):
            new = api.list_applications(profile_ids=str(self.profile['id']), statuses='new', session=self.session)
        self.assertEqual(new['items'], [])
        self.assertEqual(new['total'], 0)
        self.assertEqual(new['counts']['new'], 0)
        self.assertEqual(new['counts']['interview'], 1)
        # Reads preserve every source row and its saved history.
        self.assertEqual(self.session.scalar(select(func.count()).select_from(models.JobApplication)), 2)

    def test_detail_and_save_on_archived_duplicate_use_the_canonical_application(self):
        from hiring_scraper.app.application_deduplication import (
            consolidate_application_duplicates, index_vacancy_identities,
        )

        duplicate = self.duplicate_job(self.jobs[0], description='Richer source copy. ' * 100)
        self.save(self.jobs[0], 'interview')
        index_vacancy_identities(self.session, [self.jobs[0].id, duplicate.id])
        consolidate_application_duplicates(self.session, archive=True)
        self.session.commit()
        self.assertEqual(self.session.scalar(select(func.count()).select_from(models.JobApplication)), 1)

        detail = api.get_job(duplicate.id, self.session, profile_id=self.profile['id'])
        self.assertEqual(detail['application']['status'], 'interview')
        company = api.get_company(self.jobs[0].feed.company_id, self.session,
                                  profile_id=self.profile['id'])
        company_cards = [item for item in company['jobs'] if item.get('application')]
        self.assertEqual(len(company_cards), 1)
        self.assertEqual(company_cards[0]['id'], duplicate.id)
        self.assertEqual(company_cards[0]['application']['status'], 'interview')
        board = api.list_jobs(49, 8, 35, None, None, None, 0, 100,
                              session=self.session, profile_id=self.profile['id'])
        board_cards = [item for item in board['items']
                       if item['id'] in {self.jobs[0].id, duplicate.id}]
        self.assertEqual(len(board_cards), 1)
        self.assertEqual(board_cards[0]['id'], duplicate.id)
        self.assertEqual(board_cards[0]['application']['status'], 'interview')
        saved = self.save(duplicate, 'accepted')
        self.assertEqual(saved['job_id'], self.jobs[0].id)
        self.assertEqual(saved['status'], 'accepted')
        self.assertEqual(self.session.scalar(select(func.count()).select_from(models.JobApplication)), 1)
        journey = self.journey()
        self.assertEqual(journey['total'], 1)
        self.assertEqual(journey['items'][0]['application']['status'], 'accepted')

    def test_save_during_identity_indexing_updates_existing_duplicate_state(self):
        duplicate = self.duplicate_job(self.jobs[0], description='Richer source copy. ' * 100)
        self.save(self.jobs[0], 'interview')
        detail = api.get_job(duplicate.id, self.session, profile_id=self.profile['id'])
        self.assertEqual(detail['application']['status'], 'interview')
        browse = api.list_jobs(49, 8, 35, None, None, None, 0, 100,
                               session=self.session, profile_id=self.profile['id'])
        self.assertEqual(browse['application_counts']['interview'], 1)
        board = api.list_jobs(49, 8, 35, None, None, None, 0, 100,
                              session=self.session, profile_id=self.profile['id'],
                              job_status='interview')
        self.assertEqual([item['id'] for item in board['items']], [duplicate.id])

        saved = self.save(duplicate, 'accepted')

        self.assertEqual(saved['job_id'], self.jobs[0].id)
        self.assertEqual(saved['status'], 'accepted')
        self.assertEqual(self.session.scalar(select(func.count()).select_from(models.JobApplication)), 1)

    def test_first_browse_snapshot_links_retained_copies_before_deduping(self):
        bridge = self.jobs[0]
        bridge.raw_metadata = {'requisition_id': 'bridge-42'}
        bridge.is_active = False
        self.session.commit()
        self.session.add(models.JobApplication(profile_id=self.profile['id'], job_id=bridge.id,
            status='interview', auto_created=False))
        self.session.commit()
        from hiring_scraper.app.application_deduplication import index_vacancy_identities
        index_vacancy_identities(self.session, [bridge.id])
        self.session.commit()
        by_url = self.duplicate_job(bridge, url=bridge.url + '?utm_source=retained-copy')
        feed = models.JobFeed(company=bridge.feed.company, provider='lever', tenant='other-source',
            feed_url='https://example.test/other-translated', status='parsed')
        by_requisition = models.Job(feed=feed, external_id='bridge-copy', title='Translated role',
            url='https://example.test/jobs/bridge-copy', is_remote=True,
            raw_metadata={'requisition_id': 'bridge-42'})
        self.session.add(by_requisition)
        self.session.commit()

        page = api.list_jobs(49, 8, 35, None, None, None, 0, 100,
                             session=self.session, profile_id=self.profile['id'],
                             location_scope='remote')

        retained_cards = [item for item in page['items'] if item['id'] in {by_url.id, by_requisition.id}]
        self.assertEqual(len(retained_cards), 1)
        self.assertEqual(page['application_counts']['interview'], 1)

    def test_index_rebuild_between_grouping_and_page_read_keeps_cards(self):
        self.save(self.jobs[0])
        original = api.canonical_application_states

        def rebuild_after_grouping(session, profile_ids):
            result = original(session, profile_ids)
            # A worker publishes a new set of automatic application IDs.
            session.query(models.JobApplication).delete(synchronize_session=False)
            session.add(models.JobApplication(id=1000, profile_id=self.profile['id'],
                job_id=self.jobs[0].id, status='new', auto_created=True))
            session.commit()
            return result

        with patch.object(api, 'canonical_application_states', side_effect=rebuild_after_grouping):
            page = self.journey()
        self.assertEqual(page['total'], 1)
        self.assertEqual([item['id'] for item in page['items']], [self.jobs[0].id])

    def test_large_journey_keeps_page_query_below_database_parameter_limit(self):
        jobs = [models.Job(feed=self.jobs[0].feed, external_id=f'unique-{index}',
            title=f'Unique role {index}', url=f'https://example.test/unique/{index}')
            for index in range(300)]
        self.session.add_all(jobs)
        self.session.flush()
        self.session.add_all([models.JobApplication(profile_id=self.profile['id'], job_id=job.id,
            status='new', auto_created=True) for job in jobs])
        self.session.commit()
        connection = self.session.connection().connection.driver_connection
        previous_limit = connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 250)
        try:
            for page in (self.journey(limit=1), self.overview(limit=1)):
                self.assertEqual(page['total'], 300)
                self.assertEqual(len(page['items']), 1)
        finally:
            connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous_limit)

    def test_journey_preserves_match_posted_and_discovered_sort_order(self):
        now = models.utcnow()
        for index, job in enumerate(self.jobs[:3]):
            job.first_seen_at = now - timedelta(days=index)
            job.date_posted = ['2026-01-01', None, '2026-02-01'][index]
            self.save(job)
            if index != 0:
                self.session.add(models.ProfileJobMatch(profile_id=self.profile['id'], job_id=job.id,
                    match_score=index * 10, match_tier='possible', match_uncertain=False))
        self.session.commit()
        for sort, expected in [('match', [2, 1, 0]), ('posted', [2, 0, 1]), ('discovered', [0, 1, 2])]:
            with self.subTest(sort=sort):
                self.assertEqual([item['id'] for item in self.journey(sort=sort)['items']],
                                 [self.jobs[index].id for index in expected])

    def test_reappearing_automatic_copy_wins_over_closed_automatic_copy(self):
        duplicate = self.duplicate_job(self.jobs[0])
        self.jobs[0].is_active = False
        self.session.add_all([
            models.JobApplication(profile_id=self.profile['id'], job_id=self.jobs[0].id,
                status='closed', previous_status='new', auto_created=True),
            models.JobApplication(profile_id=self.profile['id'], job_id=duplicate.id,
                status='new', auto_created=True),
        ])
        self.session.commit()
        page = self.journey()
        self.assertEqual(page['items'][0]['id'], duplicate.id)
        self.assertEqual(page['counts']['new'], 1)
        self.assertEqual(page['counts']['closed'], 0)

    def test_latest_saved_decision_is_shared_by_both_application_overviews(self):
        duplicate = self.duplicate_job(self.jobs[0])
        self.save(self.jobs[0], 'open')
        self.save(duplicate, 'not_interested')
        for page in (self.journey(), self.overview()):
            self.assertEqual(page['total'], 1)
            self.assertEqual(page['items'][0]['id'], self.jobs[0].id)
            self.assertEqual(page['counts']['open'], 0)
            self.assertEqual(page['counts']['not_interested'], 1)
        self.assertEqual(self.overview(status='open')['total'], 0)

    def test_undo_to_new_does_not_restore_an_older_duplicate_decision(self):
        duplicate = self.duplicate_job(self.jobs[0])
        self.save(self.jobs[0], 'open')
        self.save(duplicate, 'new')
        self.save(duplicate, 'not_interested')
        api.set_job_application(self.profile['id'], duplicate.id,
            api.ApplicationRequest(status='new', action='undo'), self.session)
        page = self.journey()
        self.assertEqual(page['items'][0]['id'], self.jobs[0].id)
        self.assertEqual(page['items'][0]['application']['status'], 'new')
        self.assertEqual(page['counts']['open'], 0)

    def test_requisition_language_variants_stay_separate_for_each_profile(self):
        self.jobs[0].feed.provider = 'greenhouse'
        self.jobs[0].raw_metadata = {'requisition_id': 'req-123'}
        duplicate = self.duplicate_job(self.jobs[0], url='https://example.test/en/jobs/translated',
                                       metadata={'requisition_id': 'req-123'})
        self.save(self.jobs[0], 'interview')
        self.save(duplicate, 'new')
        self.save(self.jobs[0], 'not_interested', self.other)
        self.save(duplicate, 'new', self.other)
        with patch.object(api, 'IS_SQLITE', False):
            page = api.list_applications(profile_ids=f"{self.profile['id']},{self.other['id']}",
                                         statuses='all', session=self.session)
        self.assertEqual(page['total'], 2)
        self.assertEqual({(item['profile_id'], item['application']['status']) for item in page['items']},
                         {(self.profile['id'], 'interview'), (self.other['id'], 'not_interested')})

    def test_same_title_without_vacancy_identity_remains_separate(self):
        self.jobs[0].title = self.jobs[1].title = 'Software engineer'
        self.jobs[0].url = self.jobs[1].url = 'https://example.test/careers'
        self.session.commit()
        self.save(self.jobs[0])
        self.save(self.jobs[1])
        self.assertEqual(self.journey()['total'], 2)

    def test_automatic_matches_deduplicate_after_rebuild_and_across_batches(self):
        duplicate = self.duplicate_job(self.jobs[0], description='Complete role description')
        copies = [models.Job(feed=self.jobs[0].feed, external_id=f'copy-{index}',
            title='Source copy', url=self.jobs[0].url, is_remote=True) for index in range(205)]
        self.session.add_all(copies)
        self.session.commit()
        page = api.list_applications(profile_ids=str(self.profile['id']), statuses='all', session=self.session)
        self.assertEqual(page['total'], 8)
        self.assertEqual(page['counts']['new'], 8)
        self.assertEqual({item['id'] for item in page['items']},
                         {duplicate.id, *(job.id for job in self.jobs[1:])})
        self.assertEqual(self.journey()['total'], 8)

    def test_transitive_vacancy_identities_merge_existing_groups(self):
        self.jobs[1].feed.provider = 'greenhouse'
        self.jobs[1].raw_metadata = {'requisition_id': 'bridge'}
        self.save(self.jobs[0])
        self.save(self.jobs[1], 'open')
        bridge = self.duplicate_job(self.jobs[0], metadata={'requisition_id': 'bridge'})
        self.save(bridge)
        page = self.journey()
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['items'][0]['id'], self.jobs[1].id)
        self.assertEqual(page['counts']['open'], 1)

    def test_background_rebuild_populates_journey_and_preserves_saved_stages(self):
        self.save(self.jobs[0], 'interview')
        refresh = job_search.refresh_profile_match_indexes_once
        with patch.object(job_search, 'SessionLocal', sessionmaker(bind=self.engine)):
            self.assertTrue(refresh())
            # The other saved profile is also stale and is processed next.
            self.assertTrue(refresh())
            self.assertFalse(refresh())
        self.session.expire_all()
        with patch.object(api, 'IS_SQLITE', False):
            page = api.list_applications(profile_ids=str(self.profile['id']),
                                         statuses='all', session=self.session)
        self.assertFalse(page['matching_pending'])
        self.assertEqual(page['counts']['interview'], 1)
        self.assertEqual(page['counts']['new'], 7)
        self.assertEqual(page['total'], 8)

    def test_every_status_roundtrips_without_creating_duplicate_applications(self):
        for status in ['new', 'open', 'in_gots_hand', 'not_interested', 'waiting_for_reply', 'automated_application', 'interview', 'rejected', 'accepted', 'closed']:
            with self.subTest(status=status):
                saved = self.save(self.jobs[0], status)
                self.session.expire_all()
                self.assertEqual(saved['status'], status)
                self.assertEqual(saved['job_id'], self.jobs[0].id)
                self.assertEqual(saved['profile_id'], self.profile['id'])
                self.assertTrue(saved['updated_at'])
                page = self.overview()
                self.assertEqual(page['total'], 1)
                self.assertEqual(page['items'][0]['application']['status'], status)
                self.assertEqual(page['counts'][status], 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(models.JobApplication)), 1)

    def test_profiles_track_the_same_job_independently(self):
        self.save(self.jobs[0], 'interview')
        self.save(self.jobs[0], 'not_interested', self.other)
        self.assertEqual(self.overview()['items'][0]['application']['status'], 'interview')
        self.assertEqual(self.overview(self.other)['items'][0]['application']['status'], 'not_interested')
        self.save(self.jobs[1], 'open', self.other)
        self.assertEqual(self.overview()['total'], 1)

    def test_filtering_precedes_pagination_and_counts_include_all_statuses(self):
        self.save(self.jobs[0], 'new')
        self.save(self.jobs[1], 'interview')
        self.save(self.jobs[2], 'interview')
        page = self.overview(status='interview', offset=1, limit=1)
        self.assertEqual(page['total'], 2)
        self.assertEqual(len(page['items']), 1)
        self.assertEqual(page['items'][0]['application']['status'], 'interview')
        self.assertEqual(page['counts'], {'new': 1, 'open': 0, 'not_interested': 0,
                         'in_gots_hand': 0, 'automated_application': 0,
                         'waiting_for_reply': 0, 'interview': 2, 'rejected': 0, 'accepted': 0, 'closed': 0})

    def test_parallel_stages_filter_independently_and_keep_undo_history(self):
        self.save(self.jobs[0], 'new')
        held = self.save(self.jobs[0], 'in_gots_hand')
        self.assertEqual(held['previous_status'], 'new')
        self.save(self.jobs[1], 'open')
        held = self.save(self.jobs[1], 'in_gots_hand')
        self.assertEqual(held['previous_status'], 'open')
        automated = self.save(self.jobs[0], 'automated_application')
        self.assertEqual(automated['previous_status'], 'in_gots_hand')
        self.assertEqual(self.overview(status='in_gots_hand')['total'], 1)
        page = api.list_applications(profile_ids=str(self.profile['id']),
                                     statuses='automated_application', session=self.session)
        self.assertEqual([item['id'] for item in page['items']], [self.jobs[0].id])
        self.assertEqual(page['counts']['in_gots_hand'], 1)
        restored = api.set_job_application(self.profile['id'], self.jobs[0].id,
            api.ApplicationRequest(status='in_gots_hand', action='undo'), self.session)
        self.assertEqual(restored['status'], 'in_gots_hand')
        self.assertEqual(self.overview(status='in_gots_hand')['total'], 2)

    def test_sent_autofill_applications_count_once_across_both_sent_stages(self):
        self.save(self.jobs[0], 'in_gots_hand')
        self.assertEqual(api.get_application_momentum(self.session)['lifetime_applied'], 0)
        self.save(self.jobs[0], 'automated_application')
        self.assertEqual(api.get_application_momentum(self.session)['lifetime_applied'], 1)
        self.save(self.jobs[0], 'waiting_for_reply')
        self.assertEqual(api.get_application_momentum(self.session)['lifetime_applied'], 1)
        self.assertEqual(api.get_application_momentum(self.session)['applied_this_week'], 1)
        self.save(self.jobs[0], 'automated_application')
        self.save(self.jobs[0], 'waiting_for_reply')
        self.assertEqual(api.get_application_momentum(self.session)['lifetime_applied'], 1)

    def test_momentum_counts_a_vacancy_once_after_switching_source_copies(self):
        duplicate = self.duplicate_job(self.jobs[0])
        self.save(self.jobs[0], 'waiting_for_reply')
        self.save(duplicate, 'automated_application')

        momentum = api.get_application_momentum(self.session)
        self.assertEqual(momentum['lifetime_applied'], 1)
        self.assertEqual(momentum['applied_this_week'], 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(
            models.ApplicationMomentumEvent).where(
                models.ApplicationMomentumEvent.event_type == 'applied')), 1)

    def test_closed_expired_and_out_of_scope_applications_remain_in_overview(self):
        self.save(self.jobs[0], 'waiting_for_reply')
        self.jobs[0].is_active = False
        self.jobs[0].raw_metadata = {'validThrough': '2000-01-01'}
        self.jobs[0].is_remote = False
        self.session.commit()
        page = self.overview()
        self.assertEqual(page['total'], 1)
        self.assertFalse(page['items'][0]['is_active'])
        self.assertTrue(page['items'][0]['expired'])
        self.assertEqual(page['items'][0]['company_name'], 'Employer')

    def test_tracking_is_visible_in_board_and_detail_without_changing_vacancy_status(self):
        before = api.get_job(self.jobs[0].id, self.session, profile_id=self.profile['id'])
        self.assertIsNone(before.get('application'))
        self.save(self.jobs[0], 'rejected')
        detail = api.get_job(self.jobs[0].id, self.session, profile_id=self.profile['id'])
        board = api.list_jobs(49, 8, 35, None, None, None, 0, 100,
                              session=self.session, profile_id=self.profile['id'])
        item = next(item for item in board['items'] if item['id'] == self.jobs[0].id)
        self.assertEqual(detail['application'], item['application'])
        self.assertEqual(detail['application']['status'], 'rejected')
        self.assertTrue(detail['is_active'])
        self.assertIsNone(api.get_job(self.jobs[0].id, self.session, profile_id=self.other['id']).get('application'))

    def test_untracked_jobs_are_not_silently_added_to_overview(self):
        page = self.overview()
        self.assertEqual(page['total'], 0)
        self.assertEqual(page['items'], [])
        self.assertEqual(sum(page['counts'].values()), 0)

    def test_invalid_status_and_missing_resources_are_rejected(self):
        self.assertIsNotNone(getattr(api, 'ApplicationRequest', None), 'Status payload must be validated')
        for value in ['applied', '', None, 'NEW']:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                api.ApplicationRequest(status=value)
        for profile_id, job_id in [(99999, self.jobs[0].id), (self.profile['id'], 99999)]:
            with self.subTest(profile_id=profile_id), self.assertRaises(HTTPException) as error:
                api.set_job_application(profile_id, job_id, api.ApplicationRequest(status='open'), self.session)
            self.assertEqual(error.exception.status_code, 404)
        with self.assertRaises(HTTPException) as error:
            self.overview(profile={'id': 99999})
        self.assertEqual(error.exception.status_code, 404)

    def test_deleting_profile_cleans_up_tracking_but_preserves_job(self):
        self.save(self.jobs[0], 'accepted')
        profiles.delete_profile(self.profile['id'], self.session)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(models.JobApplication)), 0)
        self.assertIsNotNone(self.session.get(models.Job, self.jobs[0].id))

    def test_sqlite_startup_adds_tracking_to_existing_database_idempotently(self):
        engine = create_engine('sqlite://')
        try:
            models.Base.metadata.create_all(engine, tables=[table for table in models.Base.metadata.sorted_tables
                                                          if table.name != 'job_applications'])
            initialize_sqlite_schema(engine)
            initialize_sqlite_schema(engine)
            with Session(engine) as session:
                endpoint = getattr(api, 'list_job_applications', None)
                self.assertIsNotNone(endpoint, 'SQLite startup must make application tracking available')
                profile = profiles.create_profile(profiles.ProfileRequest(name='Existing database'), session)
                self.assertEqual(endpoint(profile['id'], session=session)['total'], 0)
        finally:
            engine.dispose()

    def test_new_tracking_of_an_inactive_job_uses_closed_stage(self):
        self.jobs[0].is_active = False
        self.session.commit()
        saved = self.save(self.jobs[0], 'new')
        self.assertEqual(saved['status'], 'closed')
        page = api.list_applications(profile_ids=str(self.profile['id']), statuses='closed', session=self.session)
        self.assertEqual([item['id'] for item in page['items']], [self.jobs[0].id])

    def test_inactive_jobs_cannot_reenter_active_journey_stages(self):
        targets = {'new': 'closed', 'open': 'closed', 'in_gots_hand': 'closed',
                   'not_interested': 'closed', 'interview': 'closed', 'closed': 'closed',
                   'waiting_for_reply': 'rejected', 'automated_application': 'rejected'}
        for job, (requested, expected) in zip(self.jobs, targets.items()):
            job.is_active = False
            self.session.commit()
            self.assertEqual(self.save(job, requested)['status'], expected)
        self.assertEqual(api.get_application_momentum(self.session)['lifetime_applied'], 2)

    def test_stale_new_tracking_preserves_closed_jobs_completed_outcomes(self):
        for job, status in zip(self.jobs, ['accepted', 'rejected']):
            self.save(job, status)
            job.is_active = False
            self.session.commit()
            self.assertEqual(self.save(job, 'new')['status'], status)

    def legacy_application_table(self):
        for job in self.jobs:
            self.session.refresh(job)
        self.session.close()
        with self.engine.begin() as connection:
            connection.exec_driver_sql('DROP TABLE job_applications')
            connection.exec_driver_sql("""CREATE TABLE job_applications (
                id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                status VARCHAR(24) NOT NULL, previous_status VARCHAR(24),
                auto_created BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                CONSTRAINT uq_job_application_profile_job UNIQUE (profile_id, job_id),
                CONSTRAINT ck_job_application_status CHECK (status IN
                    ('new','open','not_interested','waiting_for_reply','interview','rejected','accepted'))
            )""")
            connection.exec_driver_sql('CREATE INDEX ix_job_applications_profile_status ON job_applications (profile_id,status)')
            connection.exec_driver_sql('CREATE INDEX ix_job_applications_job_id ON job_applications (job_id)')
        self.session = Session(self.engine)

    def test_sqlite_upgrade_preserves_old_applications_and_accepts_parallel_stages(self):
        self.legacy_application_table()
        old = self.save(self.jobs[0], 'open')
        initialize_sqlite_schema(self.engine)
        initialize_sqlite_schema(self.engine)
        self.assertEqual(self.overview()['items'][0]['application'], old)
        self.assertEqual(self.save(self.jobs[0], 'in_gots_hand')['previous_status'], 'open')
        self.assertEqual(self.save(self.jobs[1], 'automated_application')['status'], 'automated_application')

    def closed_stage_upgrade_fixture(self):
        self.legacy_application_table()
        previous_migration = importlib.import_module('hiring_scraper.app.migrations.versions.0029_parallel_application_stages')
        with self.engine.begin() as connection, patch.object(previous_migration, 'op', Operations(MigrationContext.configure(connection))):
            previous_migration.upgrade()
        for job, status in zip(self.jobs[:5], ['waiting_for_reply', 'open', 'accepted', 'rejected', 'automated_application']):
            self.save(job, status)
            self.session.get(models.Job, job.id).is_active = False
        self.session.commit()

    def assert_closed_stage_upgrade(self):
        self.session.expire_all()
        states = {row.job_id: (row.status, row.previous_status) for row in self.session.scalars(select(models.JobApplication))}
        self.assertEqual(states, {
            self.jobs[0].id: ('rejected', 'waiting_for_reply'), self.jobs[1].id: ('closed', 'open'),
            self.jobs[2].id: ('accepted', None), self.jobs[3].id: ('rejected', None),
            self.jobs[4].id: ('rejected', 'automated_application')})

    def test_sqlite_closed_stage_upgrade_backfills_existing_closed_jobs_once(self):
        self.closed_stage_upgrade_fixture()
        initialize_sqlite_schema(self.engine)
        self.assert_closed_stage_upgrade()
        initialize_sqlite_schema(self.engine)
        self.assert_closed_stage_upgrade()

    def test_alembic_closed_stage_upgrade_and_downgrade_preserve_tracking(self):
        self.closed_stage_upgrade_fixture()
        migration = importlib.import_module('hiring_scraper.app.migrations.versions.0030_closed_application_stage')
        with self.engine.begin() as connection, patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
            migration.upgrade()
        self.assert_closed_stage_upgrade()
        with self.engine.begin() as connection, patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
            migration.downgrade()
        self.session.expire_all()
        row = self.session.scalar(select(models.JobApplication).where(models.JobApplication.job_id == self.jobs[1].id))
        self.assertEqual(row.status, 'open')

    def test_alembic_upgrade_and_downgrade_preserve_parallel_stage_records(self):
        self.legacy_application_table()
        self.save(self.jobs[0], 'open')
        migration = importlib.import_module('hiring_scraper.app.migrations.versions.0029_parallel_application_stages')
        with self.engine.begin() as connection, patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
            migration.upgrade()
        self.save(self.jobs[0], 'in_gots_hand')
        self.save(self.jobs[0], 'automated_application')
        with self.engine.begin() as connection, patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
            migration.downgrade()
        self.session.expire_all()
        row = self.session.scalar(select(models.JobApplication))
        self.assertEqual((row.status, row.previous_status), ('waiting_for_reply', 'open'))
