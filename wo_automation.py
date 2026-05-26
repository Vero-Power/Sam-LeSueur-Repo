#!/usr/bin/env python3
"""
WO Assignment Automation
Polls Coperniq every 5 minutes for work orders assigned to Sam that are still ASSIGNED.
Moves them to WAITING and leaves the appropriate note.
"""

import json
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

COPERNIQ_API_KEY = os.environ['COPERNIQ_API_KEY']
COPERNIQ_BASE    = 'https://api.coperniq.io/v1'
POLL_INTERVAL    = 300  # 5 minutes

SAM_USER_ID    = 14206
DAXTON_USER_ID = 14205

# Template IDs
VEROFICATION_TEMPLATE     = 1907069
ELECTRICAL_REVIEW_TEMPLATE = 1907084
NTP_WO_TEMPLATE           = 1907087
CHANGE_ORDER_TEMPLATE     = 1907067
M2_WO_TEMPLATE            = 1907088
CONSTRUCTION_REVIEW_TEMPLATE = 1907081  # skip — Sam shouldn't be assigned these

# WO note content per template
WO_NOTES = {
    VEROFICATION_TEMPLATE: (
        f'Waiting on Verofication — reassigning. '
        f'[Daxton Dillon|~id:{DAXTON_USER_ID}] please handle this one.'
    ),
    ELECTRICAL_REVIEW_TEMPLATE: (
        f'Waiting on Electrical Review — reassigning. '
        f'[Daxton Dillon|~id:{DAXTON_USER_ID}] please handle this one.'
    ),
    NTP_WO_TEMPLATE: 'Waiting on underwriting review.',
    CHANGE_ORDER_TEMPLATE: (
        f'Waiting on change order details. '
        f'[Sam LeSueur|~id:{SAM_USER_ID}]'
    ),
    M2_WO_TEMPLATE: 'Waiting on install to be completed.',
}

DIR            = Path(__file__).parent
PROCESSED_FILE = DIR / 'processed_wo_assignments.json'

GET_H  = {'x-api-key': COPERNIQ_API_KEY}
POST_H = {'x-api-key': COPERNIQ_API_KEY, 'Content-Type': 'application/json'}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)


def _api_get(url, params=None, max_retries=5):
    for attempt in range(max_retries):
        r = requests.get(url, params=params, headers=GET_H, timeout=30)
        if r.status_code == 429:
            wait = 15 * (attempt + 1)
            log.warning(f'Rate limited — waiting {wait}s')
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f'Still rate limited after {max_retries} retries: {url}')


def _api_patch(url, body, max_retries=5):
    for attempt in range(max_retries):
        r = requests.patch(url, headers=POST_H, json=body, timeout=30)
        if r.status_code == 429:
            wait = 15 * (attempt + 1)
            log.warning(f'Rate limited — waiting {wait}s')
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f'Still rate limited after {max_retries} retries: {url}')


def _api_post(url, body, max_retries=5):
    for attempt in range(max_retries):
        r = requests.post(url, headers=POST_H, json=body, timeout=30)
        if r.status_code == 429:
            wait = 15 * (attempt + 1)
            log.warning(f'Rate limited — waiting {wait}s')
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f'Still rate limited after {max_retries} retries: {url}')


def load_processed() -> set:
    if PROCESSED_FILE.exists():
        return set(json.loads(PROCESSED_FILE.read_text()))
    return set()


def save_processed(ids: set):
    PROCESSED_FILE.write_text(json.dumps(list(ids)))


def get_assigned_wos() -> list:
    """Fetch all WOs currently assigned to Sam."""
    r = _api_get(f'{COPERNIQ_BASE}/work-orders', params={
        'assigneeId': SAM_USER_ID,
        'limit': 200,
    })
    data = r.json()
    rows = data if isinstance(data, list) else data.get('rows', [])
    return [w for w in rows if not w.get('isArchived')]


def process_wo(wo: dict, processed_ids: set) -> bool:
    wo_id      = wo['id']
    project    = wo.get('project') or {}
    project_id = project.get('id')
    template   = wo.get('templateId')
    status     = (wo.get('status') or '').lower()
    title      = (wo.get('title') or '').strip()

    if str(wo_id) in processed_ids:
        return False

    if template == CONSTRUCTION_REVIEW_TEMPLATE:
        log.info(f'Skipping Construction Review WO {wo_id} — Sam should not be assigned these')
        processed_ids.add(str(wo_id))
        return False

    if template not in WO_NOTES:
        return False

    if status != 'assigned':
        return False

    if not project_id:
        log.warning(f'WO {wo_id} ({title}) has no project — skipping')
        return False

    customer = project.get('title', f'Project {project_id}')
    log.info(f'Processing WO {wo_id} ({title}) on {customer} — moving to WAITING')

    # Move WO to WAITING
    _api_patch(
        f'{COPERNIQ_BASE}/projects/{project_id}/work-orders/{wo_id}',
        {'status': 'WAITING'},
    )
    log.info(f'WO {wo_id} → WAITING')

    # Leave note on project
    note_body = WO_NOTES[template]
    _api_post(
        f'{COPERNIQ_BASE}/projects/{project_id}/notes',
        {'body': note_body},
    )
    log.info(f'Note left on project {project_id}: {note_body[:80]}')

    processed_ids.add(str(wo_id))
    return True


def main():
    log.info('WO automation started — polling every 5 minutes.')
    processed_ids = load_processed()

    while True:
        try:
            log.info('Checking for assigned WOs...')
            wos = get_assigned_wos()
            log.info(f'Found {len(wos)} WO(s) assigned to Sam')

            changed = False
            for wo in wos:
                try:
                    if process_wo(wo, processed_ids):
                        changed = True
                except Exception as e:
                    log.exception(f'Error processing WO {wo.get("id")}: {e}')

            if changed:
                save_processed(processed_ids)

        except Exception as e:
            log.exception(f'Unexpected error — will retry: {e}')

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
