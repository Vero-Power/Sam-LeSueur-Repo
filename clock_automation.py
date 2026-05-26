#!/usr/bin/env python3
"""
Clock automation — monitors #corporate-operations Slack for Sam's messages.
Clock IN:  first message containing "locked in", "yoked in", etc.
Clock OUT: message containing "arrived X" and "left Y".
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

SLACK_TOKEN = os.environ['SLACK_BOT_TOKEN']
SAM_USER_ID = 'U0AB51A9J9H'
OPS_CHANNEL = 'C0AB50H2K9R'  # corporate-operations
TIMECLOCK_BASE = 'https://disputes.veropwr.com/api/timeclock'
POLL_INTERVAL = 120  # 2 minutes

DIR = Path(__file__).parent
STATE_FILE = DIR / 'clock_state.json'

CLOCK_IN_PHRASES = [
    'locked in', 'locked yin', 'yoked in', 'yoked yin',
    'clocked in', 'checking in', 'check in', 'checkin',
    'in the office', 'in the building', 'at the office',
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(DIR / 'clock_automation.log'),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {'last_ts': str(time.time() - 60)}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state))


def is_clock_in(text: str) -> bool:
    t = text.lower()
    return any(phrase in t for phrase in CLOCK_IN_PHRASES)


def is_clock_out(text: str) -> bool:
    t = text.lower()
    return 'arrived' in t and 'left' in t


def timeclock_status() -> dict:
    r = requests.get(f'{TIMECLOCK_BASE}/status', timeout=30)
    r.raise_for_status()
    return r.json()


def do_clock_in() -> bool:
    r = requests.post(f'{TIMECLOCK_BASE}/clock-in', json={}, timeout=30)
    if r.status_code == 409:
        log.info('Clock-in skipped — already clocked in')
        return False
    r.raise_for_status()
    log.info('Clocked IN successfully')
    return True


def do_clock_out() -> bool:
    r = requests.post(f'{TIMECLOCK_BASE}/clock-out', json={}, timeout=30)
    if r.status_code == 409:
        log.info('Clock-out skipped — not currently clocked in')
        return False
    r.raise_for_status()
    log.info('Clocked OUT successfully')
    return True


def poll():
    state = load_state()
    last_ts = float(state.get('last_ts', 0))

    r = requests.get(
        'https://slack.com/api/conversations.history',
        headers={'Authorization': f'Bearer {SLACK_TOKEN}'},
        params={'channel': OPS_CHANNEL, 'oldest': str(last_ts), 'limit': 100},
        timeout=30,
    )
    data = r.json()
    if not data.get('ok'):
        log.warning(f'Slack API error: {data.get("error")}')
        return

    msgs = [
        m for m in data.get('messages', [])
        if m.get('user') == SAM_USER_ID and float(m['ts']) > last_ts
    ]
    msgs.sort(key=lambda m: float(m['ts']))

    for msg in msgs:
        text = msg.get('text', '')
        ts = float(msg['ts'])

        if is_clock_in(text):
            log.info(f'Clock-in trigger: "{text[:80]}"')
            try:
                status = timeclock_status()
                if not status.get('clocked_in'):
                    do_clock_in()
                else:
                    log.info('Already clocked in — no action')
            except Exception as e:
                log.warning(f'Clock-in failed: {e}')

        elif is_clock_out(text):
            log.info(f'Clock-out trigger: "{text[:80]}"')
            try:
                status = timeclock_status()
                if status.get('clocked_in'):
                    do_clock_out()
                else:
                    log.info('Not clocked in — no action')
            except Exception as e:
                log.warning(f'Clock-out failed: {e}')

        last_ts = max(last_ts, ts)

    if msgs:
        state['last_ts'] = str(last_ts)
        save_state(state)


def main():
    log.info('Clock automation started — polling every 2 minutes.')
    while True:
        try:
            poll()
        except Exception as e:
            log.error(f'Unexpected error: {e}')
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
