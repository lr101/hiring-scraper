"""Verify source closure outcomes through the real journey in an isolated preview."""
import os
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from unittest.mock import patch

from hiring_scraper.app import worker
from hiring_scraper.app.models import Company, Job, JobApplication, JobFeed, ScanRun, UserProfile, utcnow


web = os.environ['POSTING_TEST_WEB_URL'].rstrip('/')
database_url = os.environ['POSTING_TEST_DATABASE_URL']
assert database_url.startswith('sqlite:////tmp/serve-dev-worktree/hiring-scraper/'), 'Use an isolated preview database'
engine = create_engine(database_url, connect_args={'timeout': 30})
factory = sessionmaker(engine, expire_on_commit=False)
marker = 'JourneyClosureDemo' + uuid4().hex[:6]
expected = {'waiting_for_reply': 'rejected', 'automated_application': 'rejected',
            'new': 'closed', 'open': 'closed', 'in_gots_hand': 'closed', 'interview': 'closed',
            'not_interested': 'closed', 'rejected': 'rejected', 'accepted': 'accepted'}
with factory.begin() as session:
    profile = UserProfile(name=marker, preferences={})
    feed = JobFeed(company=Company(source='preview_journey_closure', source_id=marker, name='Synthetic closure demo'),
                   provider='html_jobs', feed_url=f'https://example.test/{marker}/jobs', status='parsed', enabled=False)
    session.add_all([profile, feed])
    session.flush()
    for stage in expected:
        job = Job(feed=feed, external_id=stage, title=f'{marker} {stage}',
                  url=f'https://example.test/{marker}/{stage}',
                  first_seen_at=utcnow() - timedelta(days=3), last_seen_at=utcnow() - timedelta(days=1))
        session.add(job)
        session.flush()
        session.add(JobApplication(profile_id=profile.id, job_id=job.id, status=stage))
    run = ScanRun(feed_id=feed.id, status='running')
    session.add(run)
    session.flush()
    profile_id, feed_id, run_id = profile.id, feed.id, run.id
with patch.object(worker, 'SessionLocal', factory):
    worker._persist_result(feed_id, run_id, {
        'state': 'ok', 'status': 200, 'parsed_feed': {'jobs': [], 'complete': True}}, b'')
engine.dispose()

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
    try:
        for width in (1440, 390):
            page = browser.new_page(viewport={'width': width, 'height': 1000})
            page.set_default_timeout(20000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'{web}/?profile_ids={profile_id}&statuses=closed')
            for old, status in expected.items():
                role = page.get_by_role('link', name=f'{marker} {old}', exact=True)
                if status == 'closed':
                    # The first visit builds this profile's match index over
                    # the full preview fixture before returning saved outcomes.
                    expect(role).to_be_visible(timeout=90000)
                else:
                    expect(role).to_have_count(0)
            page.get_by_text('Past outcomes', exact=False).click()
            expect(page.get_by_role('button', name='Closed applications', exact=True)).to_have_attribute('aria-pressed', 'true')
            page.get_by_role('button', name='Rejected applications', exact=True).click()
            for old in ('waiting_for_reply', 'automated_application', 'rejected'):
                expect(page.get_by_role('link', name=f'{marker} {old}', exact=True)).to_be_visible()
            expect(page.get_by_role('link', name=f'{marker} not_interested', exact=True)).to_have_count(0)
            page.locator('[data-journey-stage="accepted"]').click()
            expect(page.get_by_role('link', name=f'{marker} accepted', exact=True)).to_be_visible()
            page.get_by_role('button', name='Closed applications', exact=True).click()
            page.reload()
            expect(page.get_by_role('link', name=f'{marker} not_interested', exact=True)).to_be_visible()
            assert parse_qs(urlsplit(page.url).query)['statuses'] == ['closed']
            assert not errors, errors
            page.screenshot(path=f'/tmp/journey-closure-{width}.png', full_page=True, animations='disabled')
            page.close()
        print(f'Closed/rejected/accepted journey filters and reload passed at desktop/mobile sizes. Demo: {web}/?profile_ids={profile_id}&statuses=closed')
    finally:
        browser.close()
