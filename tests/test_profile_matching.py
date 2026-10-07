import importlib
import importlib.util
import unittest


class PairedApplicantDetailTests(unittest.TestCase):
    duties = '<h3>Du passt zu uns, wenn folgende Aufgaben Dich begeistern:</h3>'
    qualifications = '<h3>Wenn Du folgende Voraussetzungen mitbringst:</h3>'
    role_text = ('<ul><li>Wir betreiben Linux Server und entwickeln Python Werkzeuge.</li>'
                 '<li>Wir betreuen die Infrastruktur unserer Kunden und dokumentieren '
                 'die technische Umsetzung der vereinbarten Lösungen.</li></ul>')
    applicant_text = ('<ul><li>Du verfügst über Erfahrung mit PostgreSQL.</li>'
                      '<li>Python Kenntnisse sind erforderlich.</li>'
                      '<li>Ruby Kenntnisse sind von Vorteil.</li></ul>')

    def body(self, title='Senior LINUX Administrator (all)', scope='main'):
        return (f'<{scope}><h1>{title}</h1>' + self.duties + self.role_text +
                self.qualifications + self.applicant_text + f'</{scope}>')

    def updates(self, body, title='Senior LINUX Administrator (all)',
                url='https://example.org/jobs/linux-administrator'):
        from hiring_scraper.matching import detail_updates
        return detail_updates({'title': title, 'url': url, 'description': 'Linux',
                               'raw_metadata': {'source_page_url': 'https://example.org/jobs'}},
                              body, url)

    def test_paired_applicant_sections_recover_both_seniority_levels_and_keep_bullets(self):
        for title, scope in [('Senior LINUX Administrator (all)', 'main'),
                             ('Junior LINUX Administrator (all)', 'article')]:
            with self.subTest(title=title, scope=scope):
                result = self.updates(self.body(title, scope), title)
                self.assertTrue(result, 'Both applicant cues should verify a matching role scope')
                description = result['description']
                self.assertIn('Du verfügst über Erfahrung mit PostgreSQL.', description.splitlines())
                self.assertIn('Python Kenntnisse sind erforderlich.', description.splitlines())
                self.assertIn('Ruby Kenntnisse sind von Vorteil.', description.splitlines())
                raw = result['raw_metadata']
                self.assertEqual(raw['description_method'], 'verified_detail_html')
                self.assertEqual(raw['description_evidence_url'], 'https://example.org/jobs/linux-administrator')
                self.assertEqual(raw['detail_listing_description'], 'Linux')
                self.assertEqual(raw['source_page_url'], 'https://example.org/jobs')

    def test_single_or_generic_cues_do_not_verify_boilerplate(self):
        for body in [self.body().replace(self.duties, ''),
                     self.body().replace(self.qualifications, ''),
                     self.body().replace(self.duties, '<h3>Aufgaben</h3>')
                                .replace(self.qualifications, '<h3>Voraussetzungen</h3>')]:
            with self.subTest(body=body):
                self.assertEqual(self.updates(body), {})

    def test_paired_sections_keep_role_qualifiers_and_reject_ambiguous_headings(self):
        for title in ['Junior LINUX Administrator (all)', 'LINUX Administrator (all)',
                      'Senior LINUX Administrator (Backend)']:
            with self.subTest(title=title):
                self.assertEqual(self.updates(self.body(), title), {})
        for extra_heading in ['Senior LINUX Administrator (all)', 'Junior LINUX Administrator (all)']:
            with self.subTest(extra_heading=extra_heading):
                body = self.body().replace('</main>', f'<h1>{extra_heading}</h1></main>')
                self.assertEqual(self.updates(body), {})

    def test_paired_sections_require_visible_heading_and_same_visible_narrative_scope(self):
        self.assertEqual(self.updates(self.body().replace('<h1>', '<h1 hidden>')), {})
        self.assertEqual(self.updates(self.body().replace('<main>', '<main hidden>')), {})
        for container in ['nav', 'footer', 'aside', 'form', 'div hidden']:
            with self.subTest(container=container):
                wrapped = f'<{container}>{self.qualifications}</{container.split()[0]}>'
                self.assertEqual(self.updates(self.body().replace(self.qualifications, wrapped)), {})
        body = self.body().replace(self.qualifications, '') + self.qualifications
        self.assertEqual(self.updates(body), {})

    def test_paired_sections_reject_overview_and_fragment_urls(self):
        for url in ['https://example.org/jobs', 'https://example.org/jobs#linux',
                    'https://example.org/jobs/linux-administrator#linux']:
            with self.subTest(url=url):
                self.assertEqual(self.updates(self.body(), url=url), {})

    def test_other_jobs_terminate_paired_role_and_cannot_supply_a_missing_cue(self):
        body = self.body().replace('</main>', '<h2>Other jobs</h2><p>Java accountants</p></main>')
        result = self.updates(body)
        self.assertTrue(result)
        self.assertNotIn('Java', result['description'])
        body = self.body().replace(self.qualifications, '<h2>Other jobs</h2>' + self.qualifications)
        self.assertEqual(self.updates(body), {})


class ProfileMatchingTests(unittest.TestCase):
    def engine(self):
        self.assertIsNotNone(importlib.util.find_spec('hiring_scraper.matching'),
                             'Profile matching and enrichment are not implemented')
        return importlib.import_module('hiring_scraper.matching')

    def test_skills_have_evidence_and_do_not_confuse_java_with_javascript(self):
        result = self.engine().enrich_job({'title': 'Senior Softwareentwickler',
            'description': 'Erfahrung mit JavaScript, React und C++. Mindestens 3 Jahre Berufserfahrung. Fließende Deutschkenntnisse.'})
        skills = {row['name']: row for row in result['skills']}
        self.assertIn('JavaScript', skills)
        self.assertNotIn('Java', skills)
        self.assertIn('C++', skills)
        self.assertIn('React', skills)
        self.assertIn('JavaScript', skills['JavaScript']['evidence'])
        self.assertEqual(result['seniority']['value'], 'senior')
        self.assertEqual(result['experience_years']['value'], 3)
        self.assertIn('German', [row['value'] for row in result['languages']])

    def test_unknown_job_data_is_not_treated_as_a_perfect_fit_or_a_conflict(self):
        engine = self.engine()
        job = {'title': 'Software Engineer', 'description': None}
        result = engine.match_job(job, {'skills': ['Python'], 'desired_roles': ['Software developer'],
                                       'work_styles': ['remote']})
        self.assertTrue(result['eligible'])
        self.assertTrue(result['uncertain'])
        self.assertIn('Work style not stated', result['unknowns'])
        self.assertLess(result['score'], 65)
        self.assertEqual(result['missing_skills'], [])

    def test_known_preferences_and_minimum_experience_produce_explicit_conflicts(self):
        result = self.engine().match_job({'title': 'Senior Python Developer',
            'work_arrangement': 'onsite', 'employment_type': 'FULL_TIME',
            'description': 'Python and SQL. At least 5 years of experience.'},
            {'skills': ['Python'], 'desired_roles': ['Developer'], 'work_styles': ['remote'],
             'employment_types': ['part_time'], 'experience_years': 2})
        self.assertFalse(result['eligible'])
        self.assertEqual(len(result['conflicts']), 3)
        self.assertIn('Requires at least 5 years; profile has 2', result['conflicts'])

    def test_role_and_skill_evidence_rank_developer_above_incidental_mention(self):
        engine = self.engine()
        profile = {'skills': ['Python', 'SQL'], 'desired_roles': ['Software developer']}
        developer = engine.match_job({'title': 'Python Softwareentwickler',
            'description': 'Develop backend services using Python and SQL.' * 8}, profile)
        accountant = engine.match_job({'title': 'Finance Manager',
            'description': 'Accounting and financial reporting; Python is a nice to have.' * 8}, profile)
        self.assertGreater(developer['score'], accountant['score'])
        self.assertEqual(developer['matched_skills'], ['Python', 'SQL'])
        self.assertIn('Role matches Software developer', developer['reasons'])
        self.assertLess(accountant['score'], 65)

    def test_cv_import_suggests_aliases_and_never_executes_embedded_content(self):
        result = self.engine().extract_profile_skills('Kenntnisse: JS, Postgres, Buchhaltung. <script>Java</script>')
        self.assertEqual(set(result), {'JavaScript', 'PostgreSQL', 'Accounting'})

    def test_enrichment_is_invalidated_when_the_description_changes(self):
        engine = self.engine()
        first = engine.enrich_job({'title': 'Developer', 'description': 'Python'})
        second = engine.enrich_job({'title': 'Developer', 'description': 'Java'})
        self.assertNotEqual(first['source_hash'], second['source_hash'])
        self.assertEqual([row['name'] for row in second['skills']], ['Java'])

    def test_detail_enrichment_requires_the_same_job_identity(self):
        engine = self.engine()
        import json
        url = 'https://company.example/jobs/123'
        job = {'title': 'Python Developer', 'url': url, 'description': None}
        posting = {'@type': 'JobPosting', 'title': 'Python Developer', 'url': url,
                   'description': 'Python and PostgreSQL for our backend team.',
                   'skills': 'Python, PostgreSQL', 'experienceRequirements': {'monthsOfExperience': 36}}
        body = '<script type="application/ld+json">' + json.dumps(posting) + '</script>'
        enriched = engine.detail_updates(job, body, url)
        self.assertEqual(enriched['description'], 'Python and PostgreSQL for our backend team.')
        self.assertEqual(enriched['raw_metadata']['detail_requirements']['experienceRequirements']['monthsOfExperience'], 36)
        posting['title'] = 'Unrelated Accountant'
        body = '<script type="application/ld+json">' + json.dumps(posting) + '</script>'
        self.assertEqual(engine.detail_updates(job, body, url), {})

    def test_optional_languages_do_not_become_hard_requirements(self):
        result = self.engine().match_job({'title': 'Developer',
            'description': 'Python. Fluent German is a nice to have.'},
            {'skills': ['Python'], 'languages': ['English']})
        self.assertTrue(result['eligible'])

    def test_employer_history_is_not_inferred_as_required_experience(self):
        result = self.engine().enrich_job({'title': 'Junior Developer',
            'description': 'We have 20 years of experience. Your role uses Python.'})
        self.assertIsNone(result['experience_years'])

    def test_verified_detail_refresh_accepts_a_shorter_updated_description(self):
        import json
        url = 'https://example.org/jobs/1'
        job = {'title': 'Developer', 'url': url, 'description': 'Python ' * 100,
               'raw_metadata': {'description_method': 'verified_detail_jsonld'}}
        posting = {'@type': 'JobPosting', 'title': 'Developer', 'url': url,
                   'description': 'Java and SQL. ' * 30}
        body = '<script type="application/ld+json">' + json.dumps(posting) + '</script>'
        result = self.engine().detail_updates(job, body, url)
        self.assertTrue(result, 'Verified detail refresh must replace outdated content')
        self.assertIn('Java', result['description'])

    def test_skill_only_profiles_can_reach_strong_overlap(self):
        result = self.engine().match_job({'title': 'Developer',
            'description': 'We build Python and SQL services. ' * 20}, {'skills': ['Python', 'SQL']})
        self.assertEqual(result['score'], 100)

    def test_malformed_structured_url_is_ignored(self):
        body = '<script type="application/ld+json">{"@type":"JobPosting","url":{"bad":true},"title":"Developer"}</script>'
        result = self.engine().detail_updates({'title':'Developer','url':'https://example.org/jobs/1'},body,'https://example.org/jobs/1')
        self.assertEqual(result,{})

    def test_detail_identity_keeps_role_qualifiers_in_parentheses(self):
        url = 'https://example.org/jobs/1'
        body = '<script type="application/ld+json">{"@type":"JobPosting","title":"Developer (Java)","description":"Java"}</script>'
        self.assertEqual(self.engine().detail_updates({'title':'Developer (Python)','url':url},body,url),{})

    def test_html_detail_uses_matching_heading_and_excludes_navigation_and_other_jobs(self):
        url = 'https://example.org/jobs/1'
        body = '<nav>Java</nav><main><h1>Python Developer</h1><h2>Your responsibilities</h2>'
        body += '<p>We build Python services and SQL databases. </p>' * 20
        body += '<h2>Other jobs</h2><p>Java accountants</p></main>'
        result = self.engine().detail_updates({'title':'Python Developer','url':url},body,url)
        self.assertTrue(result)
        self.assertNotIn('Java',result['description'])
        self.assertEqual(result['raw_metadata']['description_method'],'verified_detail_html')
        self.assertEqual(self.engine().detail_updates({'title':'Accountant','url':url},body,url),{})
        self.assertEqual(self.engine().detail_updates({'title':'Python Developer','url':url+'#role'},body,url+'#role'),{})

    def test_detail_requirements_do_not_overwrite_structured_feed_evidence(self):
        import json
        url = 'https://example.org/jobs/1'
        job = {'title':'Developer','url':url,'raw_metadata':{'skills':'Python'}}
        posting = {'@type':'JobPosting','title':'Developer','url':url,'description':'Java', 'skills':'Java'}
        body = '<script type="application/ld+json">' + json.dumps(posting) + '</script>'
        result = self.engine().detail_updates(job,body,url)
        self.assertEqual(result['raw_metadata']['skills'],'Python')
        self.assertEqual(result['raw_metadata']['detail_requirements']['skills'],'Java')

    def test_detail_supports_the_verified_final_url_after_a_redirect(self):
        import json
        source='http://example.org/jobs/1'
        final='https://www.example.org/jobs/1'
        posting={'@type':'JobPosting','title':'Developer','url':final,'description':'Python and SQL'}
        body='<script type="application/ld+json">'+json.dumps(posting)+'</script>'
        result=self.engine().detail_updates({'title':'Developer','url':source},body,final)
        self.assertTrue(result, 'Canonical redirect must not discard the same verified posting')

    def test_multiple_employment_types_accept_any_matching_preference(self):
        result=self.engine().match_job({'title':'Developer','employment_type':'FULL_TIME, PART_TIME',
            'description':'Python '*100},{'skills':['Python'],'employment_types':['full_time']})
        self.assertTrue(result['eligible'])

    def test_custom_skills_match_explicit_structured_fields(self):
        result=self.engine().match_job({'title':'Developer','raw_metadata':{'skills':'Elixir'}}, {'skills':['Elixir']})
        self.assertEqual(result['matched_skills'],['Elixir'])
        self.assertGreater(result['score'],0)

    def test_specific_backend_interest_does_not_match_a_frontend_title(self):
        result=self.engine().match_job({'title':'Frontend Developer', 'description':'JavaScript and React '*30},
            {'skills':['Python'],'desired_roles':['Backend']})
        self.assertEqual(result['score'],0)

    def test_multi_level_jobs_accept_a_matching_seniority(self):
        result=self.engine().match_job({'title':'Junior / Senior Python Developer','description':'Python '*100},
            {'skills':['Python'],'seniority_levels':['senior']})
        self.assertTrue(result['eligible'])

    def test_explicit_requirements_are_unknown_when_profile_evidence_is_missing(self):
        result=self.engine().match_job({'title':'Python Developer',
            'description':'Python. At least 5 years of experience. Fluent German. '*20}, {'skills':['Python']})
        self.assertTrue(result['uncertain'])
        self.assertIn('Profile experience not provided',result['unknowns'])
        self.assertIn('Profile languages not provided',result['unknowns'])
