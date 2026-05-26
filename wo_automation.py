#!/usr/bin/env python3
"""
WO Assignment Automation
Polls all Coperniq projects every 5 minutes for work orders assigned to Sam
(or where Sam is a collaborator) that are still ASSIGNED.
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
POLL_INTERVAL    = 300   # 5 minutes
PROJECT_REFRESH  = 3600  # re-enumerate projects every 1 hour

SAM_USER_ID    = 14206
DAXTON_USER_ID = 14205
CLAY_USER_ID   = 14204

# Template IDs
VEROFICATION_TEMPLATE        = 1907069
ELECTRICAL_REVIEW_TEMPLATE   = 1907084
NTP_WO_TEMPLATE              = 1907087
CHANGE_ORDER_TEMPLATE        = 1907067
M2_WO_TEMPLATE               = 1907088
SIGNED_DESIGN_TEMPLATE       = 2121772
CONSTRUCTION_REVIEW_TEMPLATE = 1907081  # skip
SOLAR_INSTALL_TEMPLATE       = 1907085  # skip

HANDLED_TEMPLATES = {
    VEROFICATION_TEMPLATE,
    ELECTRICAL_REVIEW_TEMPLATE,
    NTP_WO_TEMPLATE,
    CHANGE_ORDER_TEMPLATE,
    M2_WO_TEMPLATE,
    SIGNED_DESIGN_TEMPLATE,
}

WO_NOTES = {
    VEROFICATION_TEMPLATE: (
        f'Waiting on Verofication — [Daxton Dillon|~id:{DAXTON_USER_ID}] please handle this one.'
    ),
    ELECTRICAL_REVIEW_TEMPLATE: (
        f'Waiting on Electrical Review — [Daxton Dillon|~id:{DAXTON_USER_ID}] please handle this one.'
    ),
    NTP_WO_TEMPLATE:      'Waiting on underwriting review.',
    CHANGE_ORDER_TEMPLATE: (
        f'Waiting on change order details. [Sam LeSueur|~id:{SAM_USER_ID}]'
    ),
    M2_WO_TEMPLATE:       'Waiting on install to be completed.',
    SIGNED_DESIGN_TEMPLATE: (
        f'Waiting on signed design / planset review. [Clay Neser|~id:{CLAY_USER_ID}]'
    ),
}

DIR            = Path(__file__).parent
PROCESSED_FILE = DIR / 'processed_wo_assignments.json'
PROJECTS_FILE  = DIR / 'all_projects.json'

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


def load_project_map() -> dict:
    """Load cached {number: project_id} map."""
    if PROJECTS_FILE.exists():
        return {int(k): v for k, v in json.loads(PROJECTS_FILE.read_text()).items()}
    return {}


def save_project_map(project_map: dict):
    PROJECTS_FILE.write_text(json.dumps({str(k): v for k, v in project_map.items()}))


def build_project_map(current_map: dict) -> dict:
    """Scan project numbers to build complete {number: project_id} map."""
    max_num = max(current_map.keys(), default=0)
    scan_up_to = max(max_num + 30, 250)  # always scan at least to 250 on first run

    log.info(f'Scanning project numbers 1–{scan_up_to} to build project map...')
    updated = dict(current_map)

    for num in range(1, scan_up_to + 1):
        if num in updated:
            continue
        try:
            r = requests.get(f'{COPERNIQ_BASE}/projects', params={'number': num}, headers=GET_H, timeout=30)
            if r.ok:
                rows = r.json() if isinstance(r.json(), list) else r.json().get('rows', [])
                if rows:
                    updated[num] = rows[0]['id']
            time.sleep(0.05)
        except Exception as e:
            log.warning(f'Error fetching project number {num}: {e}')

    log.info(f'Project map: {len(updated)} projects (max number: {max(updated.keys(), default=0)})')
    save_project_map(updated)
    return updated


def get_assigned_wos_for_project(project_id: int) -> list:
    """Return ASSIGNED WOs on this project where Sam is assignee or collaborator."""
    try:
        wos = _api_get(f'{COPERNIQ_BASE}/projects/{project_id}/work-orders').json()
    except Exception as e:
        log.warning(f'Failed to fetch WOs for project {project_id}: {e}')
        return []

    results = []
    for w in wos:
        if w.get('isArchived'):
            continue
        if (w.get('status') or '').lower() != 'assigned':
            continue
        assignee = w.get('assignee') or {}
        collabs = w.get('collaborators') or []
        sam_is_assignee = assignee.get('id') == SAM_USER_ID
        sam_is_collab = any(c.get('id') == SAM_USER_ID for c in collabs)
        if sam_is_assignee or sam_is_collab:
            results.append(w)
    return results


def process_wo(wo: dict, project_id: int, processed_ids: set) -> bool:
    wo_id    = wo['id']
    template = wo.get('templateId')
    title    = (wo.get('title') or '').strip()
    project  = wo.get('project') or {}
    customer = project.get('title', f'Project {project_id}')

    if str(wo_id) in processed_ids:
        return False

    if template not in HANDLED_TEMPLATES:
        log.info(f'Skipping WO {wo_id} ({title}) — template {template} not handled')
        processed_ids.add(str(wo_id))
        return False

    log.info(f'Processing WO {wo_id} ({title}) on {customer} — moving to WAITING')

    _api_patch(
        f'{COPERNIQ_BASE}/projects/{project_id}/work-orders/{wo_id}',
        {'status': 'WAITING'},
    )
    log.info(f'WO {wo_id} → WAITING')

    note_body = WO_NOTES[template]
    _api_post(
        f'{COPERNIQ_BASE}/projects/{project_id}/notes',
        {'body': note_body},
    )
    log.info(f'Note left on {customer}: {note_body[:80]}')

    processed_ids.add(str(wo_id))
    return True


def main():
    log.info('WO automation started — polling every 5 minutes.')
    processed_ids = load_processed()
    project_map   = load_project_map()
    last_refresh  = 0

    while True:
        now = time.time()

        # Rebuild project map at startup and every PROJECT_REFRESH seconds
        if now - last_refresh >= PROJECT_REFRESH:
            project_map  = build_project_map(project_map)
            last_refresh = now

        try:
            log.info(f'Checking {len(project_map)} projects for assigned WOs...')
            changed = False

            for project_num, project_id in sorted(project_map.items()):
                wos = get_assigned_wos_for_project(project_id)
                for wo in wos:
                    try:
                        if process_wo(wo, project_id, processed_ids):
                            changed = True
                    except Exception as e:
                        log.exception(f'Error processing WO {wo.get("id")} on project {project_id}: {e}')
                time.sleep(0.1)

            if changed:
                save_processed(processed_ids)

        except Exception as e:
            log.exception(f'Unexpected error — will retry: {e}')

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
