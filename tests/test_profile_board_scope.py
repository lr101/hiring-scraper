"""Profile persistence and vacancy geography regressions against a real SQLite store."""
import unittest
from unittest.mock import patch
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from hiring_scraper.app import api, models, profiles

AREA = dict(label='Heidelberg', city='Heidelberg', latitude=49.3988, longitude=8.6724,
            radius_km=35, country_code='DE')


class ProfileBoardScopeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        models.Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def profile(self, **fields):
        return profiles.create_profile(profiles.ProfileRequest(name='Anonymous',
            desired_roles=['Project manager'], skills=['Project management'], **fields), self.session)

    def job(self, key, *, url=None, description='Project management and coordination. '*30,
            metadata=None, remote=False, style=None, locations=None, tenant='test'):
        company = models.Company(source='test', source_id=key, name='Employer '+key)
        feed = models.JobFeed(company=company, provider='html_jobs', tenant=tenant,
            feed_url='https://example.org/feed/'+key, status='parsed')
        job = models.Job(feed=feed, external_id=key, title='Project manager',
            url=url or 'https://example.org/jobs/'+key, description=description,
            raw_metadata=metadata or {}, is_remote=remote, work_arrangement=style,
            locations=locations or [models.JobLocation(label='Heidelberg', country_code='DE')])
        self.session.add(company)
        self.session.commit()
        return job

    def board(self, profile=None, **kwargs):
        return api.list_jobs(49.3988, 8.6724, 35, None, None, 'Heidelberg', 0, 100,
            session=self.session, profile_id=profile['id'] if profile else None, **kwargs)

    def test_new_profile_evidence_roundtrips_and_legacy_update_preserves_it(self):
        profile = self.profile(language_levels={'German':'B2', 'Spanish':'native'},
            skill_evidence={'CAD':{'context':'academic','note':'University project'}},
            summary='Anonymized skills profile', search_area=AREA, secondary_roles=['PMO'],
            education=[{'level':'master','field':'Industrial design','note':'Completed'}],
            certifications=['Scrum'], matching_defaults={'min_match_score':30,'include_unknown':True})
        self.assertEqual(profile['language_levels']['German'], 'B2')
        self.assertEqual(profile['search_area']['country_code'], 'DE')
        updated = profiles.update_profile(profile['id'], profiles.ProfileRequest(name='Changed'), self.session)
        self.assertEqual(updated['education'], profile['education'])
        self.assertEqual(updated['skill_evidence'], profile['skill_evidence'])
        self.assertEqual(updated['matching_defaults']['min_match_score'], 30)

    def test_new_fields_reject_invalid_or_unexpected_structures(self):
        for fields in [dict(language_levels={'German':'B3'}), dict(skill_evidence={'CAD':'expert'}),
                       dict(search_area={**AREA,'latitude':91}), dict(search_area={**AREA,'radius_km':0}),
                       dict(search_area={'latitude':49}), dict(search_area={**AREA,'unexpected':True}),
                       dict(education=[{'level':'invented'}]), dict(certifications='Scrum'),
                       dict(matching_defaults={'min_match_score':101})]:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                profiles.ProfileRequest(name='Test', **fields)

    def test_remote_country_scope_and_unknown_gap_are_consistent_in_detail(self):
        profile = self.profile(search_area=AREA)
        self.job('us', remote=True, style='remote', metadata={'remote_country_codes':['US']})
        unknown = self.job('unknown', remote=True, style='remote', locations=[models.JobLocation(label='Remote')])
        de = self.job('de', remote=True, style='remote', metadata={'applicantLocationRequirements':
            [{'@type':'Country','name':'Germany'}]})
        page = self.board(profile)
        self.assertEqual({x['external_id'] for x in page['items']}, {'de','unknown'})
        item = next(x for x in page['items'] if x['external_id']=='unknown')
        self.assertTrue(item['profile_match']['uncertain'])
        self.assertEqual(item['profile_match']['fit_tier'], 'possible')
        self.assertTrue(any('country' in x.lower() for x in item['profile_match']['unknowns']))
        detail = api.get_job(unknown.id, self.session, profile_id=profile['id'])
        self.assertEqual(detail['profile_match'], item['profile_match'])
        self.assertEqual(page['counts']['filtered']['remote_country'], 1)

    def test_hybrid_and_bad_city_labels_do_not_override_coordinates_or_country(self):
        profile = self.profile(search_area=AREA)
        self.job('hybrid', remote=True, style='hybrid', locations=[models.JobLocation(
            label='Berlin', latitude=52.52, longitude=13.4, country_code='DE')])
        self.job('wrong-coordinates', locations=[models.JobLocation(label='Heidelberg',
            latitude=40.7, longitude=-74, country_code='DE')])
        self.job('wrong-country', locations=[models.JobLocation(label='Heidelberg', country_code='US')])
        self.job('valid', locations=[models.JobLocation(label='Heidelberg', country_code='DE')])
        self.assertEqual([x['external_id'] for x in self.board(profile)['items']], ['valid'])

    def test_expired_jobs_are_excluded_without_mutating_lifecycle(self):
        expired = self.job('expired', metadata={'validThrough':'2000-01-01'})
        self.job('valid', metadata={'valid_through':'2099-01-01'})
        self.assertEqual([x['external_id'] for x in self.board()['items']], ['valid'])
        detail = api.get_job(expired.id, self.session)
        self.assertTrue(detail['expired'])
        self.assertEqual(detail['valid_through'], '2000-01-01')
        self.session.refresh(expired)
        self.assertTrue(expired.is_active)
        self.assertIsNone(expired.closed_at)

    def test_verified_tracking_duplicate_prefers_description_across_establishments(self):
        self.job('summary', url='https://example.org/jobs/123?utm_source=x&gh_src=z', description=None)
        full = self.job('full', url='https://example.org/jobs/123')
        page = self.board()
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['items'][0]['id'], full.id)
        self.assertEqual(page['counts']['duplicates_removed'], 1)

    def test_distinct_identity_queries_fragments_and_overview_urls_stay_distinct(self):
        for key, url in [('a','https://example.org/jobs?id=1'),('b','https://example.org/jobs?id=2'),
                         ('c','https://example.org/careers#project-manager'),
                         ('d','https://example.org/careers#program-manager'),
                         ('e','https://example.org/careers'),('f','https://example.org/careers'),
                         ('g','https://example.org/careers/open-positions'),
                         ('h','https://example.org/careers/open-positions')]:
            self.job(key, url=url)
        self.assertEqual(self.board()['total'], 8)

    def test_tenant_requisition_identity_deduplicates_without_title_collapse(self):
        self.job('a', metadata={'requisition_id':'one'})
        self.job('b', metadata={'requisition_id':'one'}, description=None)
        self.job('c', metadata={'requisition_id':'two'})
        self.assertEqual(self.board()['total'], 2)

    def test_explicit_onsite_and_hybrid_are_not_full_remote_despite_legacy_flag(self):
        profile = self.profile(search_area=AREA)
        job = self.job('onsite', remote=True, style='onsite', locations=[models.JobLocation(
            label='Berlin', latitude=52.52, longitude=13.4, country_code='DE')])
        self.assertEqual(self.board(profile)['total'], 0)
        self.assertEqual(api.get_job(job.id, self.session)['remote_country_codes'], [])

    def test_identity_ref_query_is_preserved_when_deduplicating(self):
        self.job('a', url='https://example.org/jobs/123?ref=one&utm_source=x')
        self.job('b', url='https://example.org/jobs/123?ref=two&utm_source=x')
        self.assertEqual(self.board()['total'], 2)

    def test_board_override_area_is_preserved_in_detail_explanation(self):
        profile = self.profile(search_area=AREA)
        job = self.job('berlin', locations=[models.JobLocation(label='Berlin',
            latitude=52.52, longitude=13.4, country_code='DE')])
        board = api.list_jobs(52.52, 13.4, 20, None, None, 'Berlin', 0, 10,
            session=self.session, profile_id=profile['id'], country_code='DE')
        detail = api.get_job(job.id, self.session, profile_id=profile['id'],
            latitude=52.52, longitude=13.4, radius_km=20, place='Berlin', country_code='DE')
        self.assertEqual(board['total'], 1)
        self.assertEqual(detail['geography'], board['items'][0]['geography'])
        self.assertEqual(detail['profile_match'], board['items'][0]['profile_match'])

    def test_remote_country_query_override_is_consistent_with_details(self):
        profile = self.profile(search_area=AREA)
        job = self.job('us-only', remote=True, style='remote', metadata={'remote_country':'United States'})
        self.assertEqual(self.board(profile)['total'], 0)
        board = self.board(profile, country_code='US')
        self.assertEqual(board['total'], 1)
        detail = api.get_job(job.id, self.session, profile_id=profile['id'], country_code='US')
        self.assertEqual(detail['profile_match'], board['items'][0]['profile_match'])
        self.assertEqual(detail['geography'], board['items'][0]['geography'])

    def test_localized_overview_queries_do_not_establish_vacancy_identity(self):
        for suffix in ['lang=en', 'locale=de&page=1', 'sort=newest&search=project']:
            self.job('a'+suffix, url='https://example.org/careers?'+suffix)
            self.job('b'+suffix, url='https://example.org/careers?'+suffix)
        self.assertEqual(self.board()['total'], 6)

    def test_explicit_canada_nested_country_and_unparsed_restrictions_exclude_germany(self):
        profile = self.profile(search_area=AREA)
        for key, restriction in [('canada', {'@type':'Country', 'name':'Canada'}),
                                 ('nested', {'addressCountry':{'@type':'Country', 'name':'Canada'}}),
                                 ('unknown-restriction', {'@type':'Country', 'name':'Unrecognized foreign country'})]:
            self.job(key, remote=True, style='remote',
                metadata={'applicantLocationRequirements':[restriction]})
        self.assertEqual(self.board(profile)['total'], 0)

    def test_present_malformed_applicant_restrictions_do_not_become_absent_scope(self):
        profile = self.profile(search_area=AREA)
        for index, value in enumerate([{}, False, '', '   ', 0]):
            self.job('malformed-'+str(index), remote=True, style='remote',
                metadata={'applicantLocationRequirements':value})
        self.assertEqual(self.board(profile)['total'], 0)
        self.assertEqual(self.board(profile)['counts']['filtered']['remote_country'], 5)

    def test_canonical_empty_remote_and_applicant_scope_remain_visible_unknown_leads(self):
        profile = self.profile(search_area=AREA)
        for index, metadata in enumerate([{}, {'remote_country_codes':[]}, {'remote_country_codes':None},
                                          {'applicantLocationRequirements':None},
                                          {'applicantLocationRequirements':[]} ]):
            self.job('absent-'+str(index), remote=True, style='remote', metadata=metadata)
        board = self.board(profile)
        self.assertEqual(board['total'], 5)
        for item in board['items']:
            self.assertTrue(item['geography']['unknowns'])
            self.assertEqual(item['profile_match']['fit_tier'], 'possible')

    def test_regional_full_homeoffice_office_country_does_not_prove_remote_permission(self):
        from hiring_scraper.app.regional import import_snapshot
        profile = self.profile(search_area=AREA)
        for code in ['DEUTSCHLAND', 'USA']:
            import_snapshot(self.session, [{'referenznummer':'remote-'+code,
                'stellenangebotsTitel':'Project manager', 'firma':'Remote employer '+code,
                'stellenangebotsBeschreibung':'Project management and coordination. '*30,
                'homeofficemoeglich':True, 'homeofficetyp':'ANGABE_IN_PROZENT', 'homeofficeprozent':100,
                'stellenlokationen':[{'adresse':{'ort':'Office', 'land':code}}]}])
        self.session.commit()
        board = self.board(profile)
        self.assertEqual(board['total'], 2)
        for item in board['items']:
            self.assertEqual(item['remote_country_codes'], [])
            self.assertTrue(item['geography']['unknowns'])
            self.assertEqual(item['profile_match']['fit_tier'], 'possible')

    def test_saved_area_and_matching_defaults_apply_and_can_be_overridden(self):
        profile = self.profile(search_area=AREA,
            matching_defaults={'min_match_score':100, 'include_unknown':True})
        self.job('sparse', description=None)
        page = api.list_jobs(None, None, None, None, None, None, 0, 1,
            session=self.session, profile_id=profile['id'])
        self.assertEqual(page['location']['latitude'], 49.3988)
        self.assertEqual(page['location']['radius_km'], 35)
        self.assertEqual(page['total'], 0)
        self.assertEqual(page['counts']['filtered']['below_score'], 1)
        broad = api.list_jobs(None, None, None, None, None, None, 0, 1,
            session=self.session, profile_id=profile['id'], min_match_score=0)
        self.assertEqual(broad['total'], 1)
        self.assertEqual(broad['items'][0]['external_id'], 'sparse')

    def test_explicit_empty_new_fields_clear_saved_evidence(self):
        profile = self.profile(education=[{'level':'bachelor'}], language_levels={'German':'B2'})
        updated = profiles.update_profile(profile['id'], profiles.ProfileRequest(name='Anonymous',
            education=[], language_levels={}), self.session)
        self.assertEqual(updated['education'], [])
        self.assertEqual(updated['language_levels'], {})

    def test_backend_geography_is_identical_and_filters_before_pagination(self):
        profile = self.profile(search_area=AREA)
        self.job('outside', locations=[models.JobLocation(label='Heidelberg', latitude=52, longitude=13)])
        self.job('inside')
        sqlite = self.board(profile)
        with patch.object(api, 'IS_SQLITE', False):
            postgres_path = self.board(profile)
        self.assertEqual(sqlite['items'], postgres_path['items'])
        self.assertEqual(sqlite['total'], 1)
