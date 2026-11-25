import json
import unittest
from sqlalchemy.orm import Session
from fastapi import HTTPException
from hiring_scraper.app import models, profiles
from tests import pg


class CVLanguageEvidenceTests(unittest.TestCase):
    def test_explicit_language_section_supplies_levels_without_header_or_duty_inference(self):
        from hiring_scraper.app.tex_cv import parse_tex_languages
        text = (r'\begin{document}' + '\nHeader: German C2.\n' +
                r'\section*{Languages}' + '\nEnglish C1; French A2; Spanish native.\n' +
                r'\section*{Experience}' + '\nTaught German C2 courses.\n' + r'\end{document}')
        self.assertEqual(parse_tex_languages(text), {'English': 'C1', 'French': 'A2', 'Spanish': 'native'})

    def test_hidden_or_commented_headings_do_not_establish_language_facts(self):
        from hiring_scraper.app.tex_cv import parse_tex_languages
        for prefix, suffix in ((r'\iffalse', r'\fi'), ('% ', ''),
                               (r'\newcommand{\hidden}{', '}')):
            with self.subTest(prefix=prefix):
                text = (r'\begin{document}' + '\n' + prefix + r'\section{Languages}' +
                        '\nGerman C2\n' + r'\section{Work}' + '\n' + suffix +
                        '\n' + r'\end{document}')
                self.assertEqual(parse_tex_languages(text), {})

    def test_conflicting_levels_and_tex_comments_do_not_create_verified_language_claims(self):
        from hiring_scraper.app.tex_cv import parse_tex_languages
        text = (r'\begin{document}' + '\n' + r'\section*{Sprachen}' +
                '\nDeutsch B1; Deutsch C1; Französisch A2.\n% English C2\n' + r'\end{document}')
        self.assertEqual(parse_tex_languages(text), {'French': 'A2'})


def source(language='en'):
    company = 'Labs'
    description = 'Built research tools.' if language == 'en' else 'Forschungswerkzeuge entwickelt.'
    body = ('\\documentclass{article}\n\\begin{document}\nAlex Example\\par\n'
            '\\section*{Experience}\nEngineer\\par\n'
            f'\\begin{{itemize}}\n\\item {description}\n\\end{{itemize}}\n\\end{{document}}\n')
    marker = '% radius:entry ' + json.dumps(dict(id='work-1', kind='work', company=company,
        title='Engineer', location='Bonn', start='2021', end=None, current=True)) + '\n'
    return body.replace('Engineer\\par\n', marker + 'Engineer\\par\n% radius:responsibilities\n').replace('\\end{document}', '% radius:end\n\\end{document}')


class TexCVTests(unittest.TestCase):
    def setUp(self):
        self.engine = pg.engine(self)
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

    def test_matching_projection_includes_cv_career_records_without_private_headers(self):
        profiles.store_profile_cv(self.session, self.id, 'cv.tex', source().encode(), language='en')
        profile = self.session.get(models.UserProfile, self.id)
        facts = profile.matching_preferences.get('cv_career_facts', {})
        self.assertEqual(facts.get('experiences', [{}])[0].get('description'), 'Built research tools.')
        self.assertNotIn('id', facts['experiences'][0])
        self.assertNotIn('Alex Example', json.dumps(facts))
        self.assertNotIn('cv_career_facts', profile.preferences)

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

    def test_language_migration_keeps_existing_cv_bytes(self):
        import importlib
        from sqlalchemy import inspect
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        engine = pg.isolated_engine(self)
        migration = importlib.import_module('hiring_scraper.app.migrations.versions.0031_cv_languages')
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE profile_cvs (profile_id INTEGER PRIMARY KEY, data BYTEA NOT NULL)')
            connection.exec_driver_sql("INSERT INTO profile_cvs VALUES (1, '\\x746578'::bytea)")
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertEqual(connection.exec_driver_sql('SELECT variants FROM profile_cvs').scalar(), {})
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
        self.assertIn('de', self.session.get(models.ProfileCV, self.id).variants)


class ProfileIntroductionTests(unittest.TestCase):
    def test_description_macro_is_read_as_data(self):
        from hiring_scraper.app.tex_cv import parse_tex_cv
        macro = source().replace('\\item Built research tools.', '\\newcommand{\\RadiusWorkOne}{Built research tools.}')
        self.assertEqual(parse_tex_cv(macro)['experiences'][0]['description'], 'Built research tools.')

    def test_profile_is_optional_unique_separate_and_inert(self):
        from hiring_scraper.app.tex_cv import parse_tex_cv
        original = source()
        self.assertEqual(len(parse_tex_cv(original)['experiences']), 1)
        block = '% radius:profile\nEngineer building useful tools.\\par\n% radius:endprofile\n'
        marked = original.replace('Alex Example\\par', 'Alex Example\\par\n' + block)
        self.assertEqual(parse_tex_cv(marked)['experiences'][0]['description'], 'Built research tools.')
        for altered in [marked.replace(block, block + block), marked.replace('Engineer building useful tools.', '\\input{secret}'), marked.replace('% radius:responsibilities', '% radius:responsibilities\n' + block), marked.replace('% radius:endprofile', '% radius:end')]:
            with self.assertRaises(ValueError):
                parse_tex_cv(altered)
