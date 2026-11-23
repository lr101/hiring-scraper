import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app.default_job_feeds import ensure_default_job_feeds
from hiring_scraper.app.models import Company, JobFeed
from tests import pg


class DefaultJobFeedTests(unittest.TestCase):
    def test_loading_defaults_is_idempotent_and_preserves_pause_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "default-feeds.json"
            config_path.write_text(json.dumps({"feeds": [{
                "id": "public-board", "name": "Public Job Board", "provider": "rss",
                "feed_url": "https://example.org/jobs.xml",
                "source_page_url": "https://example.org/jobs",
                "website_url": "https://example.org",
            }]}))
            engine = pg.engine(self)
            factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
            with patch("hiring_scraper.app.default_job_feeds.CONFIG_PATH", config_path), \
                    patch("hiring_scraper.app.default_job_feeds.SessionLocal", factory):
                self.assertEqual(ensure_default_job_feeds(), 1)
                with factory.begin() as session:
                    feed = session.scalar(select(JobFeed))
                    feed.enabled = False
                    feed.status = "paused"
                    feed.next_scan_at = None

                self.assertEqual(ensure_default_job_feeds(), 0)

                with factory() as session:
                    self.assertEqual(len(session.scalars(select(Company)).all()), 1)
                    feeds = session.scalars(select(JobFeed)).all()
                    self.assertEqual(len(feeds), 1)
                    self.assertFalse(feeds[0].enabled)
                    self.assertEqual(feeds[0].status, "paused")
                    self.assertIsNone(feeds[0].next_scan_at)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
