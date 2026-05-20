#!/usr/bin/env python3
"""
Install Automation
Monitors Coperniq for completed solar installs and runs post-install workflow.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

COPERNIQ_API_KEY = os.environ['COPERNIQ_API_KEY']
COMPANY_CAM_API_KEY = os.environ['COMPANY_CAM_API_KEY']
GMAIL_ADDRESS = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PW = os.environ['GMAIL_APP_PASSWORD']
SLACK_BOT_TOKEN = os.environ['SLACK_BOT_TOKEN']
KATHY_EMAIL = os.environ['KATHY_EMAIL']

COPERNIQ_BASE = 'https://api.coperniq.io/v1'
COMPANY_CAM_BASE = 'https://api.companycam.com/v2'
POLL_INTERVAL = 1800  # 30 minutes

ET = timezone(timedelta(hours=-5))
DIR = Path(__file__).parent
PROCESSED_FILE = DIR / 'processed_installs.json'

COP_GET = {'x-api-key': COPERNIQ_API_KEY}
COP_POST = {'x-api-key': COPERNIQ_API_KEY, 'Content-Type': 'application/json'}
CC_GET = {'Authorization': f'Bearer {COMPANY_CAM_API_KEY}', 'Accept': 'application/json'}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)


def _today_et() -> str:
    return datetime.now(tz=ET).strftime('%Y-%m-%dT00:00:00-05:00')

def _today_date() -> str:
    return datetime.now(tz=ET).strftime('%Y-%m-%d')

def load_processed() -> set:
    if PROCESSED_FILE.exists():
        return set(json.loads(PROCESSED_FILE.read_text()))
    return set()

def save_processed(ids: set):
    PROCESSED_FILE.write_text(json.dumps(list(ids)))


def get_todays_installs() -> list:
    """Return projects with a Solar Installation scheduled for today.

    Uses /projects/search with the install_scheduled_date custom field.
    Returns a list of project dicts; key fields:
      - id         : Coperniq project ID
      - title      : customer name
      - address    : list with one address string
      - primaryEmail / primaryPhone : customer contact
      - custom.install_scheduled_date / install_completed_date
    """
    today = _today_date()
    r = requests.get(
        f'{COPERNIQ_BASE}/projects/search',
        params={
            'prop1': 'install_scheduled_date',
            'op1': 'contains',
            'value1': today,
        },
        headers=COP_GET,
    )
    r.raise_for_status()
    projects = r.json()

    log.info(f'Found {len(projects)} solar install(s) scheduled for {today}')
    return projects


# --- Main loop ---

def main():
    log.info('Install automation started — polling every 30 minutes.')
    processed = load_processed()

    while True:
        try:
            log.info('Checking Coperniq for today\'s completed installs...')
            installs = get_todays_installs()
            for install in installs:
                project_id = install.get('id')
                if str(project_id) in processed:
                    continue
                log.info(f'New install to process: {install}')
                # TODO: run 10-step post-install workflow
                processed.add(str(project_id))
                save_processed(processed)
        except Exception:
            log.exception('Unexpected error — will retry on next poll.')
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
