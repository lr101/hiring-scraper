import json
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi import HTTPException
from hiring_scraper.app import models, profiles
from hiring_scraper.app.latex_documents import latex_source


def source(language='en'):
    company = 'Labs'
    body = latex_source('Alex Example\n## Experience\nEngineer\n- ' + ('Built research tools.' if language == 'en' else 'Forschungswerkzeuge entwickelt.'))
    marker = '% radius:entry ' + json.dumps(dict(id='work-1', kind='work', company=company,
        title='Engineer', location='Bonn', start='2021', end=None, current=True)) + '\n'
    return body.replace('Engineer\\par\n', marker + 'Engineer\\par\n% radius:responsibilities\n').replace('\\end{document}', '% radius:end\n\\end{document}')


class TexCVTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        models.Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.id = profiles.create_profile(profiles.ProfileRequest(name='Alex'), self.session)['id']

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_separate_language_sources_survive_reload_and_extract_history(self):
        for language in ('en', 'de'):
            profiles.store_profile_cv(self.session, self.id, f'cv-{language}.tex', source(language).encode(), language=language)
        self.session.expire_all()
        en = json.loads(profiles.cv_text(self.id, self.session, language='en').body)
        de = json.loads(profiles.cv_text(self.id, self.session, language='de').body)
        self.assertIn('Labs', en['text'])
        self.assertIn('Forschungswerkzeuge entwickelt.', de['text'])
        self.assertEqual(en['experiences'][0]['description'], 'Built research tools.')
        self.assertEqual(en['experiences'][0]['id'], 'work-1')
        self.assertEqual(set(profiles.profile_json(self.session.get(models.UserProfile, self.id))['cv']['languages']), {'en', 'de'})
        self.assertNotIn('experiences', self.session.get(models.UserProfile, self.id).preferences.get('application_details', {}))

    def test_rejects_non_tex_and_malformed_history_without_replacing_saved_cv(self):
        profiles.store_profile_cv(self.session, self.id, 'cv.tex', source().encode(), language='en')
        for name, data in [('cv.pdf', b'%PDF-'), ('cv.docx', b'zip'), ('cv.txt', b'Alex'), ('cv.tex', b'Alex'),
                           ('cv.tex', source().replace('% radius:end', '').encode())]:
            with self.subTest(name=name), self.assertRaises(HTTPException):
                profiles.store_profile_cv(self.session, self.id, name, data, language='en')
        self.assertIn('Labs', json.loads(profiles.cv_text(self.id, self.session, language='en').body)['text'])
        with self.assertRaises(HTTPException) as raised:
            profiles.cv_text(self.id, self.session, language='de')
        self.assertEqual(raised.exception.status_code, 422)

    def test_cv_exports_only_allow_latex_pdf_or_source(self):
        from hiring_scraper.app.application_documents import ApplicationDocumentRequest, application_document
        from hiring_scraper.app.models import Company, Job
        profiles.store_profile_cv(self.session, self.id, 'cv.tex', source().encode(), language='en')
        company = Company(name='Labs', source='test', source_id='labs')
        self.session.add(company)
        self.session.flush()
        feed = models.JobFeed(company=company, provider='html_jobs', status='parsed', feed_url='https://example.org/jobs')
        job = Job(feed=feed, external_id='1', title='Engineer', url='https://example.org/job')
        self.session.add(job)
        self.session.commit()
        revision = profiles.cv_metadata(self.session.get(models.ProfileCV, self.id))['updated_at']
        request = ApplicationDocumentRequest(job_id=job.id, cv_updated_at=revision, cv='Finished CV', cover_letter='Dear team', latex_documents={'cv': source()})
        for format in ('pdf', 'docx'):
            with self.subTest(format=format), self.assertRaises(HTTPException) as raised:
                application_document(self.id, request, kind='cv', format=format, session=self.session)
            self.assertEqual(raised.exception.status_code, 422)
        exported = application_document(self.id, request, kind='cv', format='tex', session=self.session)
        self.assertIn(b'radius:entry', exported.body)
        for unmarked in (None, {'cv': latex_source('Unmarked CV')}):
            invalid = request.model_copy(update={'latex_documents': None}) if unmarked is None else ApplicationDocumentRequest(**{**request.model_dump(), 'latex_documents': unmarked})
            with self.assertRaises(HTTPException):
                application_document(self.id, invalid, kind='cv', format='tex', session=self.session)

    def test_marked_canvas_can_render_but_metadata_cannot_inject_tex(self):
        from hiring_scraper.app.latex_documents import validate_latex_source
        self.assertEqual(validate_latex_source(source()), source())
        with self.assertRaises(ValueError):
            validate_latex_source(source().replace('Labs', r'Labs^^0a\\input{/etc/passwd}'))

    def test_language_migration_keeps_existing_cv_bytes(self):
        import importlib
        from sqlalchemy import inspect
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        engine = create_engine('sqlite://')
        migration = importlib.import_module('hiring_scraper.app.migrations.versions.0031_cv_languages')
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE profile_cvs (profile_id INTEGER PRIMARY KEY, data BLOB NOT NULL)')
            connection.exec_driver_sql("INSERT INTO profile_cvs VALUES (1, X'746578')")
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertEqual(connection.exec_driver_sql('SELECT variants FROM profile_cvs').scalar(), '{}')
                migration.downgrade()
            self.assertEqual(connection.exec_driver_sql('SELECT data FROM profile_cvs').scalar(), b'tex')
            self.assertNotIn('variants', {column['name'] for column in inspect(connection).get_columns('profile_cvs')})
        engine.dispose()

    def test_translations_must_keep_history_identity_and_dates(self):
        profiles.store_profile_cv(self.session, self.id, 'en.tex', source().encode(), language='en')
        for altered in (source('de').replace('work-1', 'different-id'), source('de').replace('Labs', 'Different employer'), source('de').replace('2021', '2019')):
            with self.assertRaises(HTTPException):
                profiles.store_profile_cv(self.session, self.id, 'de.tex', altered.encode(), language='de')
        self.assertNotIn('de', self.session.get(models.ProfileCV, self.id).variants)
        profiles.store_profile_cv(self.session, self.id, 'de.tex', source('de').encode(), language='de')
        from hiring_scraper.app.application_documents import ApplicationDocumentRequest, application_document
        company = models.Company(name='Labs', source='test', source_id='german-export')
        feed = models.JobFeed(company=company, provider='html_jobs', status='parsed', feed_url='https://example.org/jobs')
        job = models.Job(feed=feed, external_id='1', title='Engineer', url='https://example.org/job')
        self.session.add(job)
        self.session.commit()
        request = ApplicationDocumentRequest(job_id=job.id, cv_updated_at=profiles.cv_metadata(self.session.get(models.ProfileCV, self.id))['updated_at'], cv='Deutscher Lebenslauf', cover_letter='Sehr geehrtes Team', latex_documents={'cv': source('de')})
        exported = application_document(self.id, request, kind='cv', format='tex', session=self.session)
        self.assertEqual(exported.body.decode(), source('de'))
