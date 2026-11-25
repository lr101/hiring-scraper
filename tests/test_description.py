import json
import unittest

from hiring_scraper.ats import parse_feed
from hiring_scraper.description import description_blocks, description_preview, description_text


class DescriptionTextTests(unittest.TestCase):
    def test_html_keeps_headings_paragraphs_and_bullets(self):
        text = description_text(
            '<div><p><b>Our Company</b></p><p>We build data   infrastructure.</p>'
            '<h3>Job Responsibilities:**</h3><ul><li><p>Resolve storage issues.</p></li><li>Mentor analysts.</li></ul>'
            '<script>track()</script><p>Equal&nbsp;opportunity employer.</p></div>')
        self.assertEqual(text, '## Our Company\nWe build data infrastructure.\n\n'
                               '## Job Responsibilities\n- Resolve storage issues.\n- Mentor analysts.\n\n'
                               'Equal opportunity employer.')

    def test_escaped_html_and_plain_text_with_line_breaks(self):
        self.assertEqual(description_text('&lt;p&gt;First&lt;/p&gt;&lt;ul&gt;&lt;li&gt;One&lt;/li&gt;&lt;/ul&gt;'),
                         'First\n\n- One')
        self.assertEqual(description_text('Ihre Aufgaben:\r\n\r\n\r\n- Planung   \n- Umsetzung'),
                         'Ihre Aufgaben:\n\n- Planung\n- Umsetzung')
        self.assertIsNone(description_text('  '))
        self.assertEqual(description_text('<p>Apply today.</p><p>#LI-JT1 #LI-Remote</p><p><b>LI-JT1</b></p>'), 'Apply today.')

    def test_blocks_for_structured_and_legacy_flat_text(self):
        self.assertEqual(description_blocks('## Your tasks\n- Plan\n- Build\n\nLine one\nLine two'), [
            {'type': 'heading', 'text': 'Your tasks', 'section': 'duties'},
            {'type': 'list', 'items': ['Plan', 'Build']},
            {'type': 'paragraph', 'text': 'Line one\nLine two'},
        ])
        self.assertEqual(description_blocks('Ihr Profil:\n- Erfahrung mit SQL')[0],
                         {'type': 'heading', 'text': 'Ihr Profil', 'section': 'requirements'})
        flat = ('Wir sind ein wachsendes Team in Karlsruhe und entwickeln Software für Kunden. ' * 6 +
                'Deine Aufgaben • Planung von Projekten • Umsetzung mit Python Dein Profil • Erfahrung mit SQL')
        blocks = description_blocks(flat)
        self.assertIn({'type': 'heading', 'text': 'Deine Aufgaben', 'section': 'duties'}, blocks)
        self.assertIn({'type': 'list', 'items': ['Erfahrung mit SQL']}, blocks)

    def test_preview_drops_markers(self):
        self.assertEqual(description_preview('## About us\nWe build.\n\n- Python'), 'About us We build. Python')


class SourceDescriptionTests(unittest.TestCase):
    def test_lever_keeps_requirement_lists_and_closing_text(self):
        body = json.dumps([{
            'id': '1', 'text': 'Data Engineer', 'hostedUrl': 'https://jobs.lever.co/acme/1',
            'categories': {'location': 'Berlin'}, 'description': '<div>Join our data team.</div>',
            'lists': [{'text': 'What you will bring', 'content': '<li>SQL</li><li>Python</li>'}],
            'additional': '<div>We sponsor visas.</div>',
        }]).encode()
        job = parse_feed('lever', body, 'https://api.lever.co/v0/postings/acme?mode=json')['jobs'][0]
        self.assertEqual(job['description'],
                         'Join our data team.\n\n## What you will bring\n- SQL\n- Python\n\nWe sponsor visas.')


if __name__ == '__main__':
    unittest.main()
