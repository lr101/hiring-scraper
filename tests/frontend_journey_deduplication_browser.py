"""Verify duplicate source jobs produce one actionable Journey card per vacancy."""
import os
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hiring_scraper.app.job_search import _profile_index_version
from hiring_scraper.app.models import Company, Job, JobApplication, JobFeed, UserProfile


web = os.environ['POSTING_TEST_WEB_URL'].rstrip('/')
database_url = os.environ['POSTING_TEST_DATABASE_URL']
assert database_url.startswith('sqlite:////tmp/serve-dev-worktree/hiring-scraper/'), 'Use an isolated preview database'
engine = create_engine(database_url, connect_args={'timeout': 30})
marker = 'JourneyDedup' + uuid4().hex[:6]
with Session(engine) as session:
    profile = UserProfile(name=marker, preferences={'matching_defaults': {'min_match_score': 100}})
    feed = JobFeed(company=Company(source='preview_journey_dedup', source_id=marker,
                                  name='Synthetic deduplication demo'),
                   provider='html_jobs', feed_url=f'https://example.test/{marker}/jobs',
                   status='parsed', enabled=False)
    session.add_all([profile, feed])
    session.flush()
    profile.match_index_version = _profile_index_version(profile)
    for vacancy, stages in [('new-role', ['new', 'new']), ('saved-role', ['interview', 'new'])]:
        for index, stage in enumerate(stages):
            job = Job(feed=feed, external_id=f'{vacancy}-{index}',
                      title=f'{marker} {vacancy} source {index}',
                      url=f'https://example.test/{marker}/{vacancy}?utm_source={index}')
            session.add(job)
            session.flush()
            session.add(JobApplication(profile_id=profile.id, job_id=job.id, status=stage,
                                       auto_created=vacancy == 'saved-role' and index == 1))
    session.commit()
    profile_id = profile.id
engine.dispose()

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
    try:
        for width in (1440, 390):
            page = browser.new_page(viewport={'width': width, 'height': 1000})
            page.set_default_timeout(20000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'{web}/?profile_ids={profile_id}&statuses=new')
            cards = page.locator('.application-job-card')
            expect(cards).to_have_count(1, timeout=20000)
            expect(cards.locator('.application-job-title')).to_contain_text('new-role')
            expect(page.locator('[data-journey-stage="new"]')).to_have_accessible_name('New, 1 roles')
            expect(page.locator('[data-journey-stage="interview"]')).to_have_accessible_name('Interview, 1 roles')
            cards.get_by_role('button', name='Mark interested', exact=True).click()
            expect(page.locator('[data-journey-stage="open"]')).to_have_accessible_name('Interested, 1 roles')
            expect(cards).to_have_count(0)
            page.reload()
            expect(cards).to_have_count(0)
            page.locator('[data-journey-stage="open"]').click()
            expect(cards).to_have_count(1)
            # Undo must not uncover the other source as a second New card.
            cards.get_by_role('button', name='Undo to New', exact=False).click()
            expect(page.locator('[data-journey-stage="new"]')).to_have_accessible_name('New, 1 roles')
            page.locator('[data-journey-stage="interview"]').click()
            expect(cards).to_have_count(1)
            expect(cards.locator('.application-job-title')).to_contain_text('saved-role source 0')
            page.reload()
            expect(cards).to_have_count(1)
            assert not errors, errors
            page.close()
        print(f'Journey deduplication, stage counts, moves, undo and reload passed at desktop/mobile sizes. Demo: {web}/?profile_ids={profile_id}&statuses=new')
    finally:
        browser.close()
