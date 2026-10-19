"""Check posting-state search in an isolated preview, retaining synthetic demo jobs.

POSTING_TEST_WEB_URL=... POSTING_TEST_DATABASE_URL=sqlite:////tmp/serve-dev-worktree/... \
    .venv/bin/python tests/frontend_posting_status_browser.py
"""
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hiring_scraper.app.models import Company, Job, JobFeed, JobLocation, utcnow


web = os.environ['POSTING_TEST_WEB_URL'].rstrip('/')
database_url = os.environ['POSTING_TEST_DATABASE_URL']
assert database_url.startswith('sqlite:////tmp/serve-dev-worktree/hiring-scraper/'), 'Use an isolated preview database'
marker = 'PostingStateDemo' + uuid4().hex[:8]
engine = create_engine(database_url, connect_args={'timeout': 30})
with Session(engine) as session:
    company = Company(source='preview_posting_status', source_id=marker,
                      name='Synthetic posting state demo', country_code='DE')
    feed = JobFeed(company=company, provider='html_jobs', status='parsed', enabled=False,
                   feed_url=f'https://example.test/{marker}/jobs')
    closed = Job(feed=feed, external_id='closed', title=marker + ' Closed engineer',
                 url=f'https://example.test/{marker}/closed', is_active=False,
                 closed_at=utcnow() - timedelta(days=1), raw_metadata={'validThrough': '2020-01-01'},
                 locations=[JobLocation(label='Karlsruhe', country_code='DE')])
    opened = Job(feed=feed, external_id='open', title=marker + ' Open engineer',
                 url=f'https://example.test/{marker}/open', is_active=True,
                 locations=[JobLocation(label='Karlsruhe', country_code='DE')])
    session.add_all([closed, opened])
    session.commit()
    closed_title, open_title, closed_id = closed.title, opened.title, closed.id
engine.dispose()

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
    try:
        for width in (1440, 390):
            page = browser.new_page(viewport={'width': width, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'{web}/discover?view=jobs&q={marker}')
            posting = page.get_by_role('group', name='Posting status', exact=True)
            expect(posting.get_by_role('button', name='Open', exact=True)).to_have_attribute('aria-pressed', 'true')
            expect(page.get_by_role('link', name=open_title, exact=True)).to_be_visible(timeout=30000)
            expect(page.get_by_role('link', name=closed_title, exact=True)).to_have_count(0)

            posting.get_by_role('button', name='Closed', exact=True).click()
            expect(page.get_by_role('link', name=closed_title, exact=True)).to_be_visible()
            expect(page.get_by_role('link', name=open_title, exact=True)).to_have_count(0)
            expect(page.get_by_text('Posting closed', exact=False)).to_be_visible()
            assert parse_qs(urlsplit(page.url).query)['posting_status'] == ['closed']
            page.reload()
            expect(posting.get_by_role('button', name='Closed', exact=True)).to_have_attribute('aria-pressed', 'true')
            expect(page.get_by_role('link', name=closed_title, exact=True)).to_be_visible()

            posting.get_by_role('button', name='All', exact=True).click()
            expect(page.get_by_role('link', name=open_title, exact=True)).to_be_visible()
            expect(page.get_by_role('link', name=closed_title, exact=True)).to_be_visible()
            page.get_by_role('link', name=closed_title, exact=True).click()
            expect(page.get_by_role('heading', name=closed_title, exact=True)).to_be_visible()
            expect(page.locator('.metadata-grid').get_by_text('Closed', exact=True)).to_have_count(2)
            assert not errors, errors
            screenshot = Path('/tmp') / f'posting-status-detail-{width}.png'
            page.screenshot(path=str(screenshot), full_page=True, animations='disabled')
            page.close()
        print(f'Posting filters, URL reload, closed dates, and retained details passed at desktop/mobile sizes. Demo: {web}/discover?view=jobs&q={marker}&posting_status=closed')
    finally:
        browser.close()
