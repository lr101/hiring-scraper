import json
import unittest
from urllib.parse import parse_qs, urlsplit

from hiring_scraper.osm_websites import (
    fetch_wikidata_entities,
    resolve_osm_email_websites,
    resolve_osm_websites,
    select_website_enrichments,
)


class OSMWebsiteResolutionTests(unittest.TestCase):
    def test_direct_osm_email_domain_with_name_overlap_is_an_eligible_homepage_lead(self):
        candidates = [{
            'source_id': 'node/20', 'name': 'kr3m media GmbH', 'website': '',
            'email': 'info@kr3m.com',
        }]

        rows = resolve_osm_email_websites(candidates)

        self.assertEqual(rows[0]['website_url'], 'https://kr3m.com/')
        self.assertEqual(rows[0]['relation'], 'osm_email_tag')
        self.assertEqual(rows[0]['method'], 'osm_email_domain_name_match')
        self.assertTrue(rows[0]['eligible_for_enrichment'])

    def test_partially_matching_email_domain_is_retained_for_review_but_not_auto_enriched(self):
        candidates = [{
            'source_id': 'node/24', 'name': 'MatSec Security & Transport GmbH', 'website': '',
            'email': 'info@matsec-sec.de',
        }]

        row = resolve_osm_email_websites(candidates)[0]

        self.assertFalse(row['eligible_for_enrichment'])
        self.assertEqual(row['ineligible_reason'], 'email_domain_does_not_identify_candidate')

    def test_free_mail_email_is_retained_as_a_suggestion_but_never_auto_enriched(self):
        candidates = [{
            'source_id': 'node/21', 'name': 'Jürgen Hemmann Malerbetrieb', 'website': '',
            'email': 'juergen.hemann@gmx.de',
        }]

        row = resolve_osm_email_websites(candidates)[0]

        self.assertFalse(row['eligible_for_enrichment'])
        self.assertEqual(row['ineligible_reason'], 'free_email_provider')

    def test_email_domain_already_present_on_an_osm_candidate_is_not_auto_enriched(self):
        candidates = [
            {'source_id': 'node/22', 'name': 'MatSec Security', 'website': '',
             'email': 'info@matsec-sec.de'},
            {'source_id': 'node/23', 'name': 'MatSec', 'website': 'https://matsec-sec.de/'},
        ]

        row = resolve_osm_email_websites(candidates)[0]

        self.assertFalse(row['eligible_for_enrichment'])
        self.assertEqual(row['ineligible_reason'], 'website_domain_already_mapped_in_osm')

    def test_wikidata_api_fetch_batches_ids_with_a_descriptive_user_agent(self):
        calls = []

        class Response:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return self.body

        def opener(request, timeout):
            query = parse_qs(urlsplit(request.full_url).query)
            ids = query['ids'][0].split('|')
            calls.append((ids, request.get_header('User-agent'), timeout))
            return Response(('{' + '"entities":{' + ','.join(
                json.dumps(qid) + ':' + json.dumps({'id': qid}) for qid in ids) + '}}').encode())

        entities = fetch_wikidata_entities([f'Q{number}' for number in range(1, 53)], opener)
        self.assertEqual(len(entities), 52)
        self.assertEqual([len(call[0]) for call in calls], [50, 2])
        self.assertIn('HiringScraper', calls[0][1])
        self.assertEqual(calls[0][2], 30)

    def test_direct_wikidata_entity_official_website_is_eligible_without_name_guessing(self):
        candidate = {'source_id': 'node/1', 'name': 'Acme GmbH', 'website': ''}
        osm = {'elements': [{'type': 'node', 'id': 1, 'tags': {'wikidata': 'Q1'}}]}
        entities = {'Q1': {'id': 'Q1', 'labels': {'en': {'value': 'Acme GmbH'}}, 'claims': {
            'P856': [{'rank': 'normal', 'mainsnak': {'datavalue': {'value': 'https://acme.example/'}}}],
        }}}
        rows = resolve_osm_websites([candidate], osm, entities)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['website_url'], 'https://acme.example/')
        self.assertTrue(rows[0]['eligible_for_enrichment'])
        self.assertEqual(rows[0]['relation'], 'entity')

    def test_brand_and_operator_website_require_identity_overlap(self):
        candidates = [
            {'source_id': 'node/2', 'name': 'Techniker Krankenkasse', 'website': ''},
            {'source_id': 'node/3', 'name': 'Onur Icel', 'website': ''},
        ]
        osm = {'elements': [
            {'type': 'node', 'id': 2, 'tags': {'brand:wikidata': 'Q2'}},
            {'type': 'node', 'id': 3, 'tags': {'brand:wikidata': 'Q2'}},
        ]}
        entities = {'Q2': {'id': 'Q2', 'labels': {'de': {'value': 'Techniker Krankenkasse'}}, 'claims': {
            'P856': [{'rank': 'normal', 'mainsnak': {'datavalue': {'value': 'https://www.tk.de/'}}}],
        }}}
        rows = resolve_osm_websites(candidates, osm, entities)
        by_id = {row['source_id']: row for row in rows}
        self.assertTrue(by_id['node/2']['eligible_for_enrichment'])
        self.assertFalse(by_id['node/3']['eligible_for_enrichment'])

    def test_brand_name_can_be_corroborated_by_official_domain_when_label_is_missing(self):
        candidate = {'source_id': 'node/7', 'name': 'Greenpeace Karlsruhe', 'website': ''}
        osm = {'elements': [{'type': 'node', 'id': 7, 'tags': {'brand:wikidata': 'Q7'}}]}
        entities = {'Q7': {'id': 'Q7', 'labels': {}, 'claims': {
            'P856': [{'rank': 'normal', 'mainsnak': {'datavalue': {'value': 'https://www.greenpeace.org/'}}}],
        }}}
        row = resolve_osm_websites([candidate], osm, entities)[0]
        self.assertTrue(row['eligible_for_enrichment'])
        self.assertEqual(row['identity_score'], 1.0)

    def test_ambiguous_osm_url_is_suggested_but_name_matched_domain_is_eligible(self):
        candidates = [
            {'source_id': 'node/8', 'name': 'Contargo Wörth-Karlsruhe GmbH', 'website': ''},
            {'source_id': 'node/9', 'name': 'Haus Solms', 'website': ''},
        ]
        osm = {'elements': [
            {'type': 'node', 'id': 8, 'tags': {'url': 'https://www.contargo.net/'}},
            {'type': 'node', 'id': 9, 'tags': {'url': 'https://ka.stadtwiki.net/Palais_Solms'}},
        ]}
        rows = resolve_osm_websites(candidates, osm, {})
        by_id = {row['source_id']: row for row in rows}
        self.assertTrue(by_id['node/8']['eligible_for_enrichment'])
        self.assertFalse(by_id['node/9']['eligible_for_enrichment'])

    def test_ignores_deprecated_statements_and_non_web_urls(self):
        candidate = {'source_id': 'way/4', 'name': 'No site', 'website': ''}
        osm = {'elements': [{'type': 'way', 'id': 4, 'tags': {'wikidata': 'Q4'}}]}
        entities = {'Q4': {'id': 'Q4', 'labels': {'en': {'value': 'No site'}}, 'claims': {
            'P856': [
                {'rank': 'deprecated', 'mainsnak': {'datavalue': {'value': 'https://old.example/'}}},
                {'rank': 'normal', 'mainsnak': {'datavalue': {'value': 'ftp://files.example/'}}},
            ],
        }}}
        self.assertEqual(resolve_osm_websites([candidate], osm, entities), [])

    def test_does_not_auto_select_official_url_marked_with_deprecation_reason(self):
        candidate = {'source_id': 'node/16', 'name': 'Acme GmbH', 'website': ''}
        osm = {'elements': [{'type': 'node', 'id': 16, 'tags': {'wikidata': 'Q16'}}]}
        entities = {'Q16': {'id': 'Q16', 'labels': {'de': {'value': 'Acme GmbH'}}, 'claims': {
            'P856': [{'rank': 'normal', 'mainsnak': {'datavalue': {'value': 'https://old.acme.example/'}},
                      'qualifiers': {'P2241': [{'datavalue': {'value': {'id': 'Q123'}}}]}}],
        }}}
        row = resolve_osm_websites([candidate], osm, entities)[0]
        self.assertFalse(row['eligible_for_enrichment'])
        self.assertEqual(row['ineligible_reason'], 'wikidata_website_has_deprecation_qualifier')

    def test_operator_candidate_is_only_enriched_when_its_name_identifies_the_operator(self):
        candidates = [
            {'source_id': 'node/5', 'name': 'VBK Betriebshof Ost', 'website': ''},
            {'source_id': 'node/6', 'name': 'Sozial- und Jugendbehörde', 'website': ''},
        ]
        osm = {'elements': [
            {'type': 'node', 'id': 5, 'tags': {'operator:wikidata': 'Q5'}},
            {'type': 'node', 'id': 6, 'tags': {'operator:wikidata': 'Q5'}},
        ]}
        entities = {'Q5': {'id': 'Q5', 'labels': {'de': {'value': 'Verkehrsbetriebe Karlsruhe GmbH'}},
                           'aliases': {'de': [{'value': 'VBK'}]}, 'claims': {
            'P856': [{'rank': 'normal', 'mainsnak': {'datavalue': {'value': 'https://www.vbk.info/'}}}],
        }}}
        rows = resolve_osm_websites(candidates, osm, entities)
        by_id = {row['source_id']: row for row in rows}
        self.assertTrue(by_id['node/5']['eligible_for_enrichment'])
        self.assertFalse(by_id['node/6']['eligible_for_enrichment'])

    def test_german_brand_candidate_does_not_inherit_another_country_site(self):
        candidate = {'source_id': 'node/17', 'name': 'Helvetia', 'website': ''}
        osm = {'elements': [{'type': 'node', 'id': 17, 'tags': {'brand:wikidata': 'Q17'}}]}
        entities = {'Q17': {'id': 'Q17', 'labels': {'de': {'value': 'Helvetia Versicherungen'}}, 'claims': {
            'P856': [{'rank': 'normal', 'mainsnak': {'datavalue': {'value': 'https://www.helvetia.es/'}}}],
        }}}
        row = resolve_osm_websites([candidate], osm, entities)[0]
        self.assertFalse(row['eligible_for_enrichment'])
        self.assertEqual(row['ineligible_reason'], 'non_german_country_domain')

    def test_operator_facility_is_not_added_when_the_company_site_is_already_a_candidate(self):
        candidates = [
            {'source_id': 'way/12', 'name': 'VBK Betriebshof Ost', 'website': ''},
            {'source_id': 'node/13', 'name': 'Verkehrsbetriebe Karlsruhe GmbH',
             'website': 'https://www.vbk.info/'},
        ]
        osm = {'elements': [{'type': 'way', 'id': 12, 'tags': {'operator:wikidata': 'Q12'}}]}
        entities = {'Q12': {'id': 'Q12', 'labels': {'de': {'value': 'Verkehrsbetriebe Karlsruhe'}}, 'claims': {
            'P856': [{'rank': 'normal', 'mainsnak': {'datavalue': {'value': 'https://www.vbk.info/'}}}],
        }}}
        rows = resolve_osm_websites(candidates, osm, entities)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['eligible_for_enrichment'])
        self.assertEqual(rows[0]['ineligible_reason'], 'operator_domain_already_mapped_in_osm')

    def test_entity_website_is_not_duplicated_when_an_osm_candidate_already_maps_the_domain(self):
        candidates = [
            {'source_id': 'node/14', 'name': 'Acme branch', 'website': ''},
            {'source_id': 'node/15', 'name': 'Acme GmbH', 'website': 'https://www.acme.example/'},
        ]
        osm = {'elements': [{'type': 'node', 'id': 14, 'tags': {'wikidata': 'Q14'}}]}
        entities = {'Q14': {'id': 'Q14', 'labels': {'de': {'value': 'Acme GmbH'}}, 'claims': {
            'P856': [{'rank': 'normal', 'mainsnak': {'datavalue': {'value': 'https://acme.example/careers'}}}],
        }}}
        row = resolve_osm_websites(candidates, osm, entities)[0]
        self.assertFalse(row['eligible_for_enrichment'])
        self.assertEqual(row['ineligible_reason'], 'website_domain_already_mapped_in_osm')

    def test_german_domain_is_preferred_when_an_entity_has_multiple_official_sites(self):
        suggestions = [
            {'source_id': 'node/10', 'relation': 'brand', 'eligible_for_enrichment': True,
             'identity_score': 1, 'website_url': 'https://www.allianz.com/'},
            {'source_id': 'node/10', 'relation': 'brand', 'eligible_for_enrichment': True,
             'identity_score': 1, 'website_url': 'https://www.allianz.de/'},
        ]
        self.assertEqual(select_website_enrichments(suggestions)['node/10']['website_url'],
                         'https://www.allianz.de/')

    def test_german_official_site_can_outrank_global_preferred_site_for_a_german_candidate(self):
        candidate = {'source_id': 'node/11', 'name': 'Allianz', 'website': ''}
        osm = {'elements': [{'type': 'node', 'id': 11, 'tags': {'brand:wikidata': 'Q11'}}]}
        entities = {'Q11': {'id': 'Q11', 'labels': {'de': {'value': 'Allianz SE'}}, 'claims': {
            'P856': [
                {'rank': 'preferred', 'mainsnak': {'datavalue': {'value': 'https://www.allianz.com/'}}},
                {'rank': 'normal', 'mainsnak': {'datavalue': {'value': 'https://www.allianz.de/'}}},
            ],
        }}}
        rows = resolve_osm_websites([candidate], osm, entities)
        selected = select_website_enrichments(rows)['node/11']
        self.assertEqual(selected['website_url'], 'https://www.allianz.de/')
        self.assertEqual(selected['website_claim_rank'], 'normal')

    def test_only_one_candidate_is_enriched_per_discovered_employer_domain(self):
        suggestions = [
            {'source_id': 'node/21', 'relation': 'brand', 'eligible_for_enrichment': True,
             'identity_score': 1, 'website_url': 'https://jobs.example.de/'},
            {'source_id': 'relation/22', 'relation': 'brand', 'eligible_for_enrichment': True,
             'identity_score': 1, 'website_url': 'https://www.jobs.example.de/careers'},
        ]
        selected = select_website_enrichments(suggestions)
        self.assertEqual(list(selected), ['node/21'])


if __name__ == '__main__':
    unittest.main()
