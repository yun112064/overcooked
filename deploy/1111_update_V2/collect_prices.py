"""Small resumable REAL price collection, hard-locked to the 1111_update database."""
from dataclasses import asdict
import argparse
from datetime import datetime, timezone
import json
import sys
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
sys.path.insert(0, '/app/backend')
from sqlalchemy import text
from app.db.session import engine
from app.core.config import settings
from app.pricing_scheduler import SchedulerSettings, create_service
from app.source_adapters.pxmart import PxMartAdapter

USER_AGENT = 'overcooked-t02-price-research/1.0 (+academic project; low-rate)'


class RobotsClient:
    """Single connection, at least two seconds between requests; fail closed."""
    def __init__(self):
        self.client = httpx.Client(timeout=15, follow_redirects=False,
            limits=httpx.Limits(max_connections=1), headers={'User-Agent': USER_AGENT})
        self.last = 0.0
        self.robots = {}

    def _get(self, url, **kwargs):
        time.sleep(max(0, 2 - (time.monotonic() - self.last)))
        try:
            return self.client.get(url, **kwargs)
        finally:
            self.last = time.monotonic()

    def get(self, url, **kwargs):
        parsed = urlsplit(url)
        origin = f'{parsed.scheme}://{parsed.netloc}'
        if origin not in self.robots:
            response = self._get(origin + '/robots.txt')
            parser = RobotFileParser()
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
            elif response.status_code == 404:
                parser.parse([])
            else:
                parser.parse(['User-agent: *', 'Disallow: /'])
            self.robots[origin] = parser
        if not self.robots[origin].can_fetch(USER_AGENT, url):
            return httpx.Response(403, request=httpx.Request('GET', url), text='robots policy unavailable or disallowed')
        return self._get(url, **kwargs)

    def close(self):
        self.client.close()


with engine.connect() as db:
    assert db.scalar(text('SELECT current_database()')) == 'meal_planner_1102_v3_1111_update_v2'
    assert db.scalar(text('SELECT version_num FROM alembic_version')) == 'm1111simorder1'
parser = argparse.ArgumentParser()
parser.add_argument('--food-id', action='append')
parser.add_argument('--run-key', default='1111-update-real-price-sample-v1')
args = parser.parse_args()
food_ids = set(args.food_id or ['FOOD000867', 'FOOD000039'])
service = create_service(SchedulerSettings.from_env(), expected_recipe_release_id=settings.generation_recipe_release_id)
service.adapter_factory = lambda: PxMartAdapter(client=RobotsClient(), retries=0)
result = service.run(trigger_type='manual', scheduled_for=datetime.now(timezone.utc),
                     run_key=args.run_key, food_ids=food_ids, limit=len(food_ids))
print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
