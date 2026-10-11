import tempfile
import unittest
from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from hiring_scraper.app.database import initialize_sqlite_schema

class RankedSchemaTests(unittest.TestCase):
    def test_existing_company_and_campaign_survive_repeatable_rank_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f'sqlite:///{Path(directory)/"legacy.db"}')
            with engine.begin() as db:
                db.exec_driver_sql('CREATE TABLE companies (id INTEGER PRIMARY KEY, name VARCHAR(500))')
                db.exec_driver_sql("INSERT INTO companies(id,name) VALUES(1,'Existing employer')")
                db.exec_driver_sql('CREATE TABLE discovery_jobs (id INTEGER PRIMARY KEY)')
            initialize_sqlite_schema(engine)
            columns = {c['name'] for c in inspect(engine).get_columns('companies')}
            self.assertIn('crawl_priority',columns,'Existing databases need durable ranking fields')
            with engine.begin() as db:
                db.execute(text("UPDATE companies SET crawl_priority=90,crawl_interval_days=1,country_code='DE',employee_count=1000 WHERE id=1"))
                db.exec_driver_sql("INSERT INTO discovery_jobs(id,map_cursor) VALUES(1,'{\"state_index\":4}')")
            initialize_sqlite_schema(engine)
            with engine.connect() as db:
                self.assertEqual(tuple(db.execute(text('SELECT name,crawl_priority,crawl_interval_days,country_code,employee_count FROM companies WHERE id=1')).one()),('Existing employer',90,1,'DE',1000))
                self.assertIn('state_index',db.execute(text('SELECT map_cursor FROM discovery_jobs WHERE id=1')).scalar())
