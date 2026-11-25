"""Run the real Radius API/frontend against the migrated, emptied test database."""
import argparse

import uvicorn
from sqlalchemy import text

from hiring_scraper.app.database import SessionLocal
from hiring_scraper.app.models import Company, Job, JobFeed, UserProfile

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fd', type=int, required=True)
    args = parser.parse_args()
    with SessionLocal.begin() as session:
        session.add(UserProfile(id=1, name='Research profile', preferences={}))
        company = Company(source='test', source_id='extension-e2e', name='Research Labs')
        feed = JobFeed(company=company, provider='html_jobs', feed_url='https://employer.example/jobs', status='parsed')
        session.add(Job(id=1574, feed=feed, external_id='1574', title='Research project manager', url='https://employer.example/jobs/1574', description='Coordinate research projects with partner teams.', is_active=True))
        session.flush()
        # Explicit ids bypass the sequences; move them past the fixture rows.
        for table in ('user_profiles', 'jobs'):
            session.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}))"))
    uvicorn.run('hiring_scraper.app.api:app', fd=args.fd, log_level='error')
