"""Run the real Radius API/frontend with an isolated synthetic test database."""
import argparse
import uvicorn
from hiring_scraper.app.database import SessionLocal, engine, initialize_sqlite_schema
from hiring_scraper.app.models import Company, Job, JobFeed, UserProfile

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fd', type=int, required=True)
    args = parser.parse_args()
    initialize_sqlite_schema(engine)
    with SessionLocal.begin() as session:
        session.add(UserProfile(id=1, name='Research profile', preferences={}))
        company = Company(source='test', source_id='extension-e2e', name='Research Labs')
        feed = JobFeed(company=company, provider='html_jobs', feed_url='https://employer.example/jobs', status='parsed')
        session.add(Job(id=1574, feed=feed, external_id='1574', title='Research project manager', url='https://employer.example/jobs/1574', description='Coordinate research projects with partner teams.', is_active=True))
    uvicorn.run('hiring_scraper.app.api:app', fd=args.fd, log_level='error')
