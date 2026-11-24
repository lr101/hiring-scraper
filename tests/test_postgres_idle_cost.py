"""Idle worker cycles must not scan the inventory; use a disposable TEST_POSTGRES_URL.

Each background loop polls every few seconds. A query whose cost grows with the
number of jobs is harmless in a small test database and a statement timeout in
production. This test measures the buffers that every SELECT in one idle cycle
touches (via EXPLAIN ANALYZE) at two inventory sizes and requires the cost to
stay flat.
"""
import json
import os
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import (
    application_preparation_queue, discovery_worker, job_analysis_worker, job_location_scope, job_search,
    search_snapshots, semantic_search_worker, worker,
)
from hiring_scraper.app.models import ProfileEvidenceUnits, SemanticSearchIndexState, UserProfile
from hiring_scraper.matching_v3.evidence import UNITS_VERSION, units_hash, units_source
from hiring_scraper.semantic_matching import MODEL_KEY
from tests import pg

SMALL, LARGE = 3000, 40000
# Allowed growth from SMALL to LARGE jobs (13x more rows; SMALL exceeds bounded scan windows). B-tree depth may add a
# page per index lookup; a scan would grow by orders of magnitude.
GROWTH_FACTOR, GROWTH_SLACK = 1.5, 60


def _no_inference(*_args, **_kwargs):
    raise AssertionError('an idle cycle ran model inference')


def _idle_cycles():
    return {
        'semantic search indexer': semantic_search_worker.process_index_batch,
        'profile index backfill': job_search.refresh_profile_match_indexes_once,
        'changed-job rematch': job_search.refresh_dirty_job_matches_once,
        'feed scan': worker.scan_once,
        'company discovery': discovery_worker.process_batch,
        'search snapshot refresh': search_snapshots.process_search_snapshot_refresh_once,
        'job location scope backfill': search_snapshots.backfill_job_location_scopes_once,
        'application preparation claim': lambda: application_preparation_queue.process_once(
            lambda *_args: None),
        'job analysis': lambda: (
            job_analysis_worker.refresh_profile_units_once(embed_fn=_no_inference),
            job_analysis_worker.refresh_evidence_once(embed_fn=_no_inference, rerank_fn=_no_inference),
            job_analysis_worker.analyse_next_job(analyse=_no_inference)),
        'application render claim': lambda: application_preparation_queue.process_render_once(
            lambda *_args: None),
    }


def _scope_queries():
    return {
        'germany scope snapshot': search_snapshots._scoped_job_rows_statement(
            employer_country='DE',
            state=None,
            location_scope='area_remote',
            company_id=None,
            normalized_query=None,
            work_style='all',
            posting_status='open',
        ),
        'state scope snapshot': search_snapshots._scoped_job_rows_statement(
            employer_country=None,
            state='Bayern',
            location_scope='area_remote',
            company_id=None,
            normalized_query=None,
            work_style='all',
            posting_status='open',
        ),
    }


def _plan_nodes(node):
    yield node
    for child in node.get('Plans', ()):
        yield from _plan_nodes(child)


class IdleWorkerCostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pg.reset()
        cls.engine = create_engine(pg.url(), pool_size=4, max_overflow=4,
            connect_args={'options': '-c statement_timeout=60000'})
        cls.factory = sessionmaker(cls.engine, autoflush=False, expire_on_commit=False)
        cls.stack = ExitStack()
        for module in list(sys.modules.values()):
            name = getattr(module, '__name__', '')
            if name.startswith('hiring_scraper.') and hasattr(module, 'SessionLocal'):
                cls.stack.enter_context(patch.object(module, 'SessionLocal', cls.factory))
        cls.stack.enter_context(patch.object(discovery_worker, '_ORIGIN_PACER', None, create=True))

        class NoIdleEmbedding:
            def encode(self, _texts):
                raise AssertionError('an idle cycle tried to embed')

        cls.stack.enter_context(patch.object(semantic_search_worker, '_embedder', NoIdleEmbedding()))
        cls.seed_profile()
        cls.costs = {}
        cls.scope_costs = {}
        cls.add_jobs(1, SMALL)
        cls.costs[SMALL] = cls.measure()
        cls.scope_costs[SMALL] = cls.measure_scopes()
        cls.add_jobs(SMALL + 1, LARGE)
        cls.costs[LARGE] = cls.measure()
        cls.scope_costs[LARGE] = cls.measure_scopes()

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()
        cls.engine.dispose()

    @classmethod
    def seed_profile(cls):
        with cls.factory.begin() as session:
            profile = UserProfile(name='Idle', preferences={
                'desired_roles': ['Software developer'], 'skills': ['Python'],
                'summary': 'I build software services.'})
            session.add(profile)
            session.flush()
            source = units_source(profile.matching_preferences)
            session.add(ProfileEvidenceUnits(profile_id=profile.id, version=UNITS_VERSION,
                                             source_hash=units_hash(source), payload=source, vectors=b''))
            profile_id = profile.id
            for kind in ('company', 'job'):
                session.add(SemanticSearchIndexState(entity_type=kind, model_revision=MODEL_KEY,
                                                     backfill_complete=True))
        with cls.factory.begin() as session:
            # Saving the profile vector invalidates the index; mark it current afterwards.
            profile = session.get(UserProfile, profile_id)
            profile.match_index_version = job_search._profile_index_version(profile)
        with cls.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO companies(source, source_id, name, career_status, next_discovery_at,
                                      crawl_priority, crawl_paused, discovery_attempt_count,
                                      size_evidence, irrelevant_checks, homepage_resolution_attempts,
                                      career_url_manual)
                SELECT 'idle', g::text, 'Company ' || g, 'checked', now() + interval '30 days',
                       0, false, 0, '{}', 0, 0, false
                FROM generate_series(1, 200) g"""))
            connection.execute(text("""
                INSERT INTO job_feeds(company_id, provider, feed_url, status, next_scan_at, enabled,
                                      attempt_count, source_revision, source_config, pending_search_scopes)
                SELECT id, 'rss', 'https://example.invalid/' || id, 'parsed', now() + interval '1 day',
                       true, 0, 1, '{}', '[]'
                FROM companies"""))

    @classmethod
    def add_jobs(cls, first, last):
        with cls.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO jobs(feed_id, external_id, title, url, description, raw_metadata, enrichment,
                                 first_seen_at, last_seen_at, is_active, is_remote, missing_complete_scans,
                                 location_text, germany_candidate, location_states, location_scope_version)
                SELECT (SELECT min(id) FROM job_feeds) + (g % 200), g::text, 'Engineer ' || g,
                       'https://example.invalid/j/' || g, repeat('Build services. ', 40), '{}', '{}',
                       now(), now(), true, (g IN (6, 7)), 0,
                       CASE
                           WHEN g BETWEEN 1 AND 3 THEN 'München'
                           WHEN g BETWEEN 4 AND 5 THEN 'Berlin'
                           WHEN g IN (6, 7) THEN 'Remote'
                           ELSE 'Paris, France'
                       END,
                       (g BETWEEN 1 AND 5),
                       CASE
                           WHEN g BETWEEN 1 AND 3 THEN ARRAY['Bayern']::text[]
                           WHEN g BETWEEN 4 AND 5 THEN ARRAY['Berlin']::text[]
                           ELSE ARRAY[]::text[]
                       END,
                       :scope_version
                FROM generate_series(CAST(:first AS integer), CAST(:last AS integer)) g"""), {
                    'first': first,
                    'last': last,
                    'scope_version': job_location_scope.LOCATION_SCOPE_VERSION,
                })
            for kind, table in (('job', 'jobs'), ('company', 'companies')):
                connection.execute(text(f"""
                    INSERT INTO semantic_search_documents(entity_type, entity_id, content_hash, model_revision,
                                                          embedding, indexed_at)
                    SELECT '{kind}', id, 'h', :model, array_fill(0.01::real, ARRAY[384])::vector, now()
                    FROM {table} ON CONFLICT DO NOTHING"""), {'model': MODEL_KEY})
            connection.execute(text('DELETE FROM semantic_search_refreshes'))
            # Production tables churn constantly: leave the visibility map stale, as feed scans do.
            connection.execute(text('UPDATE jobs SET last_seen_at = now()'))
        with cls.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            # Autovacuum keeps statistics fresh and drained queues compact in production.
            # Leave jobs unvacuumed on purpose: its stale visibility map is the scenario.
            for table in ('semantic_search_refreshes', 'match_dirty_jobs', 'job_search_snapshot_refreshes',
                          'job_analysis_queue', 'match_evidence_queue'):
                connection.execute(text(f'VACUUM {table}'))
            connection.execute(text('ANALYZE'))

    @classmethod
    def measure(cls):
        costs = {}
        for name, cycle in _idle_cycles().items():
            statements = []

            def record(_connection, _cursor, statement, parameters, _context, many):
                # Claims are often UPDATE ... WHERE id IN (SELECT ...). Measure DML too:
                # every EXPLAIN ANALYZE runs in its own transaction and is rolled back.
                if not many and statement.lstrip().upper().startswith(
                        ('SELECT', 'WITH', 'UPDATE', 'DELETE', 'INSERT')):
                    statements.append((statement, parameters))

            event.listen(cls.engine, 'before_cursor_execute', record)
            try:
                cycle()
            finally:
                event.remove(cls.engine, 'before_cursor_execute', record)
            costs[name] = (sum(cls.buffers(statement, parameters) for statement, parameters in statements),
                           statements)
        return costs

    @classmethod
    def measure_scopes(cls):
        results = {}
        for name, stmt in _scope_queries().items():
            captured = []

            def record(_connection, _cursor, statement, parameters, _context, many):
                if not many and statement.lstrip().upper().startswith('SELECT'):
                    captured.append((statement, parameters))

            event.listen(cls.engine, 'before_cursor_execute', record)
            try:
                with cls.factory() as session:
                    rows = session.execute(stmt).all()
            finally:
                event.remove(cls.engine, 'before_cursor_execute', record)
            statement, parameters = captured[-1]
            plan = cls.explain_plan(statement, parameters)
            top = plan[0]['Plan']
            bufs = int(top.get('Shared Hit Blocks', 0)) + int(top.get('Shared Read Blocks', 0))
            results[name] = (bufs, len(rows), plan, statement)
        return results

    @classmethod
    def explain_plan(cls, statement, parameters):
        raw = cls.engine.raw_connection()
        try:
            cursor = raw.cursor()
            cursor.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + statement, parameters)
            plan = cursor.fetchone()[0]
            return json.loads(plan) if isinstance(plan, str) else plan
        finally:
            raw.rollback()
            raw.close()

    @classmethod
    def buffers(cls, statement, parameters):
        raw = cls.engine.raw_connection()
        try:
            cursor = raw.cursor()
            cursor.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + statement, parameters)
            plan = cursor.fetchone()[0]
            plan = json.loads(plan) if isinstance(plan, str) else plan
            top = plan[0]['Plan']
            return int(top.get('Shared Hit Blocks', 0)) + int(top.get('Shared Read Blocks', 0))
        except Exception as error:
            if os.getenv('RADIUS_IDLE_COST_REPORT') == '1':
                print('EXPLAIN failed:', type(error).__name__, str(error).splitlines()[0],
                      ' '.join(statement.split())[:160], file=sys.stderr)
            return 0
        finally:
            raw.rollback()
            raw.close()

    def test_idle_cycles_do_not_grow_with_the_inventory(self):
        if os.getenv('RADIUS_IDLE_COST_REPORT') == '1':
            for name in _idle_cycles():
                print(f'{name:32} {self.costs[SMALL][name][0]:>7} -> {self.costs[LARGE][name][0]:>7} buffers '
                      f'({len(self.costs[LARGE][name][1])} statements)', file=sys.stderr)
        failures = []
        for name in _idle_cycles():
            small, _ = self.costs[SMALL][name]
            large, statements = self.costs[LARGE][name]
            if large > small * GROWTH_FACTOR + GROWTH_SLACK:
                worst = max(statements, key=lambda item: self.buffers(*item), default=('', None))[0]
                failures.append(f'{name}: {small} -> {large} buffers; costliest statement: '
                                f'{" ".join(worst.split())[:300]}')
        self.assertEqual(failures, [], '\n'.join(failures))

    def test_germany_and_state_scope_snapshot_queries_use_indexes_and_stay_flat(self):
        expected_counts = {'germany scope snapshot': 7, 'state scope snapshot': 5}
        expected_indexes = {
            'germany scope snapshot': 'ix_jobs_germany_candidate',
            'state scope snapshot': 'ix_jobs_location_states',
        }
        for name in _scope_queries():
            small_bufs, small_rows, _, _ = self.scope_costs[SMALL][name]
            large_bufs, large_rows, plan, statement = self.scope_costs[LARGE][name]
            self.assertEqual(small_rows, expected_counts[name], name)
            self.assertEqual(large_rows, expected_counts[name], name)
            self.assertLessEqual(
                large_bufs,
                small_bufs * GROWTH_FACTOR + GROWTH_SLACK,
                f'{name}: {small_bufs} -> {large_bufs} buffers; SQL: {" ".join(statement.split())[:300]}',
            )
            nodes = list(_plan_nodes(plan[0]['Plan']))
            used_indexes = {node.get('Index Name') for node in nodes if node.get('Index Name')}
            self.assertIn(expected_indexes[name], used_indexes, f'{name} plan indexes: {sorted(used_indexes)}')
            self.assertIn('ix_jobs_location_scope_version', used_indexes, f'{name} plan indexes: {sorted(used_indexes)}')
            self.assertIn('ix_jobs_remote_candidate_active', used_indexes, f'{name} plan indexes: {sorted(used_indexes)}')
            job_seq_scans = [
                node for node in nodes
                if node.get('Node Type') == 'Seq Scan' and node.get('Relation Name') == 'jobs'
            ]
            self.assertEqual(job_seq_scans, [], f'{name} performed a Seq Scan on jobs')
            executed_location_scans = [
                node for node in nodes
                if node.get('Relation Name') == 'job_locations' and int(node.get('Actual Loops', 0)) > 0
            ]
            self.assertEqual(executed_location_scans, [], f'{name} scanned job_locations despite up-to-date scope')


if __name__ == '__main__':
    unittest.main()
