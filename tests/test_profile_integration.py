import importlib
import importlib.util
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from fastapi import HTTPException

from hiring_scraper.app import api, models


class ProfileIntegrationTests(unittest.TestCase):
    def test_personalization_edit_preserves_main_work_history_and_tex_cv(self):
        profiles = self.profile_api()
        history = [{'id': 'role-1', 'company': 'Example GmbH', 'title': 'Engineer',
                    'start': '2021', 'current': True, 'description': 'Built reporting tools.'}]
        from test_tex_cv import source as marked_source
        source = marked_source().encode()
        with Session(self.engine) as session:
            created = profiles.create_profile(profiles.ProfileRequest(name='Engineer',
                application_details={'experiences': history, 'preferences': {'willing_to_work_onsite': False}}), session)
            profiles.store_profile_cv(session, created['id'], 'original.tex', source)
            saved = profiles.update_profile(created['id'], profiles.ProfileRequest(name='Engineer',
                application_personalization={'career_evidence': 'Built reliable reporting tools.'}), session)
            self.assertEqual(saved['application_details']['experiences'][0]['id'], 'role-1')
            self.assertEqual(saved['application_details']['experiences'][0]['description'], 'Built reporting tools.')
            self.assertIs(saved['application_details']['preferences']['willing_to_work_onsite'], False)
            self.assertEqual(saved['application_personalization']['career_evidence'], 'Built reliable reporting tools.')
            self.assertEqual(saved['cv']['filename'], 'original.tex')
            self.assertEqual(profiles.download_cv(created['id'], session).body, source)

    def test_application_personalization_is_saved_preserved_and_explicitly_cleared(self):
        profiles = self.profile_api()
        context = dict(career_evidence='Built an academic prototype; reduced test time by 20%.',
                       writing_samples='I explain the work in direct sentences.',
                       wording_to_avoid='passionate, perfect fit', career_goals='Move into research coordination.',
                       work_preferences='Small teams, clear expectations; no regular travel.')
        with Session(self.engine) as session:
            saved = profiles.create_profile(profiles.ProfileRequest(name='Alex', application_personalization=context), session)
            self.assertEqual(saved['application_personalization'], context)
            updated = profiles.update_profile(saved['id'], profiles.ProfileRequest(name='Renamed'), session)
            self.assertEqual(updated['application_personalization'], context)
            other = profiles.create_profile(profiles.ProfileRequest(name='Other'), session)
            self.assertTrue(all(value == '' for value in other['application_personalization'].values()))
            cleared = profiles.update_profile(saved['id'], profiles.ProfileRequest(name='Alex', application_personalization={}), session)
            self.assertTrue(all(value == '' for value in cleared['application_personalization'].values()))

    def test_application_personalization_rejects_unknown_and_oversized_fields(self):
        from pydantic import ValidationError
        profiles = self.profile_api()
        for context in [{'career_evidence': 'x' * 12001}, {'writing_samples': 'x' * 12001},
                        {'career_goals': 'x' * 5001}, {'work_preferences': 'x' * 5001},
                        {'wording_to_avoid': 'x' * 3001}, {'invented_credentials': True}]:
            with self.subTest(context=list(context)), self.assertRaises(ValidationError):
                profiles.ProfileRequest(name='Alex', application_personalization=context)

    def test_localized_content_roundtrip_and_legacy_update_preservation(self):
        profiles = self.profile_api()
        with Session(self.engine) as session:
            content = {'de': {'summary': 'Forschung und Entwicklung',
                'custom_instructions': 'Schreibe auf Deutsch.',
                'application_personalization': {'career_goals': 'Forschungskoordination'},
                'application_preferences': {'notice_period': 'Drei Monate'}}}
            saved = profiles.create_profile(profiles.ProfileRequest(name='Alex',
                summary='Research and development', localized_content=content), session)
            self.assertEqual(saved['localized_content']['de']['summary'], 'Forschung und Entwicklung')
            updated = profiles.update_profile(saved['id'], profiles.ProfileRequest(name='Renamed'), session)
            self.assertEqual(updated['localized_content'], saved['localized_content'])
            cleared = profiles.update_profile(saved['id'], profiles.ProfileRequest(name='Alex', localized_content={}), session)
            self.assertEqual(cleared['localized_content'], {})

    def test_legacy_english_updates_and_normalized_skills_reach_both_language_clients(self):
        profiles = self.profile_api()
        with Session(self.engine) as session:
            saved = profiles.create_profile(profiles.ProfileRequest(name='Alex', summary='Old summary',
                localized_content={'en': {'summary': 'Old summary', 'skills': ['postgres']},
                                   'de': {'summary': 'Deutsche Zusammenfassung'}}), session)
            self.assertEqual(saved['skills'], ['PostgreSQL'])
            self.assertEqual(saved['localized_content']['en']['skills'], ['PostgreSQL'])
            updated = profiles.update_profile(saved['id'], profiles.ProfileRequest(name='Alex',
                summary='Updated by older client', skills=['python'],
                application_details={'preferences': {'notice_period': 'Two months'}}), session)
            self.assertEqual(updated['localized_content']['en']['summary'], 'Updated by older client')
            self.assertEqual(updated['localized_content']['en']['skills'], ['Python'])
            self.assertEqual(updated['localized_content']['en']['application_preferences']['notice_period'], 'Two months')
            self.assertEqual(updated['localized_content']['de']['summary'], 'Deutsche Zusammenfassung')

    def test_application_only_edits_preserve_journey_matches_but_skill_edits_invalidate_them(self):
        from hiring_scraper.app.job_search import _profile_index_version
        profiles = self.profile_api()
        with Session(self.engine) as session:
            saved = profiles.create_profile(profiles.ProfileRequest(name='Developer', skills=['Python'],
                matching_defaults={'min_match_score': 0}), session)
            company = models.Company(source='test', source_id='journey-perf', name='Example')
            feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://example.org/jobs', status='parsed')
            job = models.Job(feed=feed, external_id='1', title='Python developer',
                url='https://example.org/jobs/1', description='Python development.', is_remote=True)
            session.add(company); session.commit()
            page = api.list_applications(profile_ids=str(saved['id']), statuses='new', sort='match', offset=0, limit=40, session=session)
            self.assertEqual(len(page['items']), 1)
            profile = session.get(models.UserProfile, saved['id'])
            version = profile.match_index_version
            scores = session.scalars(select(models.ProfileJobMatch).where(models.ProfileJobMatch.profile_id == profile.id)).all()
            self.assertEqual(len(scores), 1)
            score = scores[0].match_score
            # These inputs are read only while drafting applications, never by the matcher.
            profiles.update_profile(profile.id, profiles.ProfileRequest(name='Renamed', skills=['Python'],
                matching_defaults={'min_match_score': 0}, summary='Updated application summary',
                custom_instructions='Use direct prose.', application_details={'email': 'new@example.org'},
                application_personalization={'career_goals': 'Build useful tools.'},
                localized_content={'de': {'summary': 'Deutsche Zusammenfassung'}}), session)
            self.assertEqual(profile.match_index_version, version)
            self.assertEqual(_profile_index_version(profile), version)
            self.assertEqual(session.get(models.ProfileJobMatch, (profile.id, job.id)).match_score, score)
            profiles.update_profile(profile.id, profiles.ProfileRequest(name='Renamed', skills=['Java'],
                matching_defaults={'min_match_score': 0}), session)
            self.assertIsNone(profile.match_index_version)
            self.assertEqual(session.scalars(select(models.ProfileJobMatch).where(models.ProfileJobMatch.profile_id == profile.id)).all(), [])

    def test_localized_content_rejects_shared_facts_and_unknown_languages(self):
        from pydantic import ValidationError
        profiles = self.profile_api()
        for content in [{'fr': {}}, {'de': {'email': 'other@example.org'}},
                        {'de': {'summary': 'x' * 5001}},
                        {'de': {'application_preferences': {'salary_amount': '1'}}},
                        {'de': {'application_preferences': {'authorization_country': 'Austria'}}}]:
            with self.subTest(content=content), self.assertRaises(ValidationError):
                profiles.ProfileRequest(name='Alex', localized_content=content)

    def setUp(self):
        self.engine = create_engine('sqlite://')
        models.Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def profile_api(self):
        self.assertIsNotNone(importlib.util.find_spec('hiring_scraper.app.profiles'),
                             'Saved profiles are not implemented')
        return importlib.import_module('hiring_scraper.app.profiles')

    def test_saved_profile_sets_location_context_without_match_filtering(self):
        profiles = self.profile_api()
        with Session(self.engine) as session:
            profile = profiles.create_profile(profiles.ProfileRequest(name='Developer', skills=['python', 'postgres'],
                desired_roles=['Software developer'], work_styles=['remote']), session)
            self.assertEqual(profile['skills'], ['Python', 'PostgreSQL'])
            company = models.Company(source='test', source_id='1', name='Example')
            feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://example.org/jobs', status='parsed')
            for key,title,remote,style,location in [
                ('remote','Python Developer',True,'remote','Berlin'),
                ('local','Python Developer',False,'onsite','Karlsruhe'),
                ('unknown','Software Engineer',False,None,'Karlsruhe'),
                ('outside','Python Developer',False,None,'Hamburg')]:
                feed.jobs.append(models.Job(external_id=key,title=title,url=f'https://example.org/jobs/{key}',
                    description='Python and PostgreSQL development. '*20 if key != 'unknown' else None,
                    is_remote=remote,work_arrangement=style,locations=[models.JobLocation(label=location)]))
            session.add(company); session.commit()
            page = api.list_jobs(49.0068705,8.4034195,15,None,None,'Karlsruhe',0,1,
                session=session, profile_id=profile['id'], min_match_score=0, include_unknown=True)
            self.assertEqual(page['total'],3)
            self.assertEqual(len(page['items']), 1)
            self.assertTrue(all(item['profile_id'] == profile['id'] for item in page['items']))
            self.assertTrue(all('profile_match' not in item for item in page['items']))
            self.assertIn('enrichment',page['items'][0])
            strict = api.list_jobs(49.0068705,8.4034195,15,None,None,'Karlsruhe',0,10,
                session=session, profile_id=profile['id'], include_unknown=False)
            self.assertEqual({item['external_id'] for item in strict['items']}, {'remote','local','unknown'})

    def test_unknown_profile_does_not_silently_return_unfiltered_jobs(self):
        self.profile_api()
        with Session(self.engine) as session:
            with self.assertRaises(HTTPException) as raised:
                api.list_jobs(49.0,8.4,15,None,None,None,0,10,session=session,profile_id=999)
            self.assertEqual(raised.exception.status_code,404)

    def test_profile_delete_and_cv_preview_do_not_store_cv_text(self):
        profiles = self.profile_api()
        with Session(self.engine) as session:
            preview = profiles.preview_cv(profiles.CVPreview(text='My email is private@example.org. Python and PostgreSQL.'))
            self.assertEqual(set(preview['skills']), {'Python','PostgreSQL'})
            profile = profiles.create_profile(profiles.ProfileRequest(name='Test',skills=preview['skills']),session)
            self.assertNotIn('text',profile)
            profiles.delete_profile(profile['id'],session)
            self.assertEqual(profiles.list_profiles(session)['items'],[])

    def test_profile_edit_is_allowed_from_the_frontend_origin(self):
        import asyncio
        messages=[]
        async def send(message): messages.append(message)
        async def receive(): return {'type':'http.request','body':b''}
        scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'OPTIONS',
               'scheme':'http','path':'/api/v1/profiles/1','raw_path':b'/api/v1/profiles/1',
               'query_string':b'', 'headers':[(b'origin',b'http://localhost:5173'),
                   (b'access-control-request-method',b'PUT')], 'server':('test',80),'client':('test',1)}
        asyncio.run(api.app(scope,receive,send))
        start=next(item for item in messages if item['type']=='http.response.start')
        self.assertEqual(start['status'],200)

    def test_existing_sqlite_jobs_are_preserved_when_initializing_the_new_schema(self):
        from hiring_scraper.app import database
        self.assertTrue(callable(getattr(database,'initialize_sqlite_schema',None)),
                        'Existing SQLite databases need the enrichment column added')
        with Session(self.engine) as session:
            company=models.Company(source='test',source_id='legacy',name='Legacy Co')
            feed=models.JobFeed(company=company,provider='html_jobs',feed_url='https://example.org',status='parsed')
            feed.jobs.append(models.Job(external_id='1',title='Legacy Developer',url='https://example.org/jobs/1',description='Python'))
            session.add(company);session.commit()
        with self.engine.begin() as connection:
            connection.exec_driver_sql('ALTER TABLE jobs DROP COLUMN enrichment')
            connection.exec_driver_sql('DROP TABLE user_profiles')
        database.initialize_sqlite_schema(self.engine)
        database.initialize_sqlite_schema(self.engine)
        with Session(self.engine) as session:
            job=session.scalar(select(models.Job))
            self.assertEqual(job.description,'Python')
            self.assertEqual(job.enrichment,{})
            self.assertEqual(session.scalars(select(models.UserProfile)).all(),[])

    def test_application_instructions_persist_survive_older_clients_and_can_be_cleared(self):
        profiles = self.profile_api()
        instructions = "Write in German.\nUse a warm, direct voice and focus on research coordination."
        with Session(self.engine) as session:
            created = profiles.create_profile(profiles.ProfileRequest(name='Writer', custom_instructions=instructions), session)
            self.assertEqual(created['custom_instructions'], instructions)
            other = profiles.create_profile(profiles.ProfileRequest(name='Another profile'), session)
            self.assertEqual(other['custom_instructions'], '')
            session.expire_all()
            saved = next(p for p in profiles.list_profiles(session)['items'] if p['id'] == created['id'])
            self.assertEqual(saved['custom_instructions'], instructions)
            updated = profiles.update_profile(created['id'], profiles.ProfileRequest(name='Renamed'), session)
            self.assertEqual(updated['custom_instructions'], instructions)
            cleared = profiles.update_profile(created['id'], profiles.ProfileRequest(name='Renamed', custom_instructions=''), session)
            self.assertEqual(cleared['custom_instructions'], '')
            legacy = models.UserProfile(name='Legacy', preferences={})
            session.add(legacy); session.commit()
            self.assertEqual(profiles.profile_json(legacy)['custom_instructions'], '')

    def test_application_instructions_reject_oversized_input(self):
        from pydantic import ValidationError
        profiles = self.profile_api()
        self.assertEqual(len(profiles.ProfileRequest(name='Writer', custom_instructions='x' * 5000).custom_instructions), 5000)
        with self.assertRaises(ValidationError):
            profiles.ProfileRequest(name='Writer', custom_instructions='x' * 5001)

    def test_saved_application_preferences_preserve_explicit_onsite_answer(self):
        profiles = self.profile_api()
        with Session(self.engine) as session:
            for answer in [True, False, None]:
                profile = profiles.create_profile(profiles.ProfileRequest(name='Applicant',
                    application_details={'preferences': {'willing_to_work_onsite': answer}}), session)
                self.assertIs(profile['application_details']['preferences']['willing_to_work_onsite'], answer)

    def test_application_work_history_is_saved_once_and_returned_with_the_profile(self):
        profiles = self.profile_api()
        entries = [{'id':'role-1','company':'Example GmbH','title':'Engineer','location':'Berlin',
                    'description':'Built reporting tools.','start':'2021','current':True,'end':None,'cv_bullet_ids':[]}]
        with Session(self.engine) as session:
            created = profiles.create_profile(profiles.ProfileRequest(name='Engineer', application_details={'experiences':entries}), session)
            self.assertEqual(created['application_details']['experiences'], entries)
            session.expire_all()
            saved = profiles.list_profiles(session)['items'][0]
            self.assertEqual(saved['application_details']['experiences'], entries)
            updated = profiles.update_profile(created['id'], profiles.ProfileRequest(name='Engineer', application_details={'experiences':[]}), session)
            self.assertEqual(updated['application_details'].get('experiences', []), [])

    def test_application_work_history_rejects_invalid_dates_and_duplicate_ids(self):
        from pydantic import ValidationError
        profiles = self.profile_api()
        valid={'id':'role-1','company':'Example','title':'Engineer','start':'2021-03','end':'2022','current':False}
        # A valid control prevents unsupported-field rejection from masking the date assertions.
        profiles.ProfileRequest(name='Engineer', application_details={'experiences':[valid]})
        for entries in [[{**valid,'start':'2021-13'}], [{**valid,'end':'2020'}],
                        [{**valid,'current':True}], [valid,valid], [valid,{**valid,'id':'role-2'}]]:
            with self.subTest(entries=entries), self.assertRaises(ValidationError):
                profiles.ProfileRequest(name='Engineer', application_details={'experiences':entries})
