"""Render current RRFS guidance and deliver one image per local morning/evening slot."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent
BASE = 'https://matthew-richardson.github.io/LPC_rrfs/data'
LOCAL = ZoneInfo('America/Denver')
IMAGE = 'LPC_RRFS_precip_vs_rain_preview.png'
MAX_AGE = timedelta(hours=18)


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('RRFS time lacks timezone')
    return result.astimezone(timezone.utc)


def validate_manifest(manifest, now):
    start = timestamp(manifest['initialized'])
    end = timestamp(manifest['frames'][-1]['valid'])
    if not timedelta(0) <= now-start <= MAX_AGE or end <= now:
        raise ValueError('RRFS run is stale, future-dated, or expired; image withheld')
    if manifest['completeThroughHour'] != 84 or manifest['frames'][-1]['hour'] != 84 or end-start != timedelta(hours=84):
        raise ValueError('RRFS forecast is not a complete 84-hour run')
    if not re.fullmatch(r'\d{10}', manifest['directory']) or manifest['directory'] != start.strftime('%Y%m%d%H'):
        raise ValueError('RRFS directory and initialization disagree')
    grid = manifest['grid']
    reference = json.loads((ROOT/'assets/rrfs/watershed-weights.json').read_text())['grid']
    if any(grid[key] != reference[key] for key in ('width', 'height', 'bounds')):
        raise ValueError('RRFS grid changed; watershed weights need rebuilding')
    return start, end


def validate_grid(grid, manifest):
    size = manifest['grid']['width'] * manifest['grid']['height']
    for field in ('qpf', 'rain'):
        values = grid[field]
        if len(values) != size or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values):
            raise ValueError('Missing or invalid RRFS '+field+' grid')
    if any(rain > total + .02 for total, rain in zip(grid['qpf'], grid['rain'])):
        raise ValueError('RRFS rainfall exceeds total precipitation')


def fetch_forecast(destination, now):
    destination.mkdir(parents=True, exist_ok=True)
    # A publishing rollover may remove the old run between these two reads.
    for attempt in range(2):
        try:
            response = requests.get(BASE+'/manifest.json', params={'t': now.timestamp()}, timeout=45)
            response.raise_for_status()
            manifest = response.json()
            validate_manifest(manifest, now)
            response = requests.get(BASE+'/'+manifest['directory']+'/grid-084.json', timeout=45)
            response.raise_for_status()
            grid = response.json()
            validate_grid(grid, manifest)
            (destination/'manifest.json').write_text(json.dumps(manifest))
            (destination/'grid.json').write_text(json.dumps(grid))
            return manifest
        except requests.HTTPError:
            if attempt:
                raise


def render(destination, now):
    destination.mkdir(parents=True, exist_ok=True)
    (destination/IMAGE).unlink(missing_ok=True)
    manifest = fetch_forecast(destination, now)
    subprocess.run([sys.executable, str(ROOT/'scripts/render_rrfs_comparison.py'), str(destination)], check=True)
    with Image.open(destination/IMAGE) as image:
        image.verify()
    return manifest


def delivery_slot(now):
    local = now.astimezone(LOCAL)
    minutes = local.hour*60 + local.minute
    if minutes < 6*60+30 or minutes >= 21*60:
        return None
    return local.strftime('%Y-%m-%d') + (' PM' if minutes >= 18*60+30 else ' AM')


def caption(manifest):
    start = timestamp(manifest['initialized']).astimezone(LOCAL)
    end = timestamp(manifest['frames'][-1]['valid']).astimezone(LOCAL)
    fmt = '%a %-m/%-d, %-I:%M %p %Z'
    return ('LPC RRFS | Total precipitation and estimated rain\n'
            'Model start: '+start.strftime(fmt)+'\nForecast through: '+end.strftime(fmt)+
            ' (84 hours)\nTown-center totals and watershed averages, inches. Forecast guidance, not a warning.')


def send(destination, state_path, now, force=False):
    slot = delivery_slot(now)
    if slot is None:
        print('RRFS image: outside daytime delivery window')
        return False
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if not force and state.get('last_sent_slot') == slot:
        print('RRFS image already delivered for '+slot)
        return False
    manifest = json.loads((destination/'manifest.json').read_text())
    validate_manifest(manifest, now)
    validate_grid(json.loads((destination/'grid.json').read_text()), manifest)
    bot = os.environ.get('GROUPME_BOT_ID')
    token = os.environ.get('GROUPME_ACCESS_TOKEN')
    if not bot or not token:
        raise RuntimeError('RRFS image requires GitHub Actions secrets GROUPME_BOT_ID and GROUPME_ACCESS_TOKEN; text delivery is unchanged')
    with Image.open(destination/IMAGE) as image:
        image.verify()
    response = requests.post('https://image.groupme.com/pictures',
                             headers={'X-Access-Token': token, 'Content-Type': 'image/png'},
                             data=(destination/IMAGE).read_bytes(), timeout=60)
    if not response.ok:
        raise RuntimeError(f'GroupMe image upload failed (HTTP {response.status_code})')
    url = response.json()['payload']['url']
    if urlparse(url).scheme != 'https' or urlparse(url).hostname != 'i.groupme.com':
        raise ValueError('GroupMe returned an unexpected image URL')
    response = requests.post('https://api.groupme.com/v3/bots/post',
                             json={'bot_id': bot, 'text': caption(manifest),
                                   'attachments': [{'type': 'image', 'url': url}]}, timeout=30)
    if not response.ok:
        raise RuntimeError(f'GroupMe image post failed (HTTP {response.status_code})')
    state.update(last_sent_slot=slot, cycle=manifest['directory'], sent_at=now.isoformat(), image_url=url)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2)+'\n')
    print('RRFS image delivered for '+slot)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('render', 'send'))
    parser.add_argument('--directory', type=Path, default=ROOT/'data/rrfs-map')
    parser.add_argument('--state', type=Path, default=ROOT/'data/rrfs_image_state.json')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.command == 'render':
        render(args.directory, now)
    else:
        send(args.directory, args.state, now, force=args.force)


if __name__ == '__main__':
    main()
