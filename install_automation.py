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


def complete_install_coperniq(project_id: int) -> dict:
    """Complete the Solar Installation work order, form, and field visit in Coperniq.

    Steps:
      1. Find the Solar Installation WO (title contains 'install', not 'site survey',
         not archived, not already COMPLETED) and:
         a. Check off all incomplete checklist items.
         b. Mark the WO COMPLETED.
      2. Find the Solar Installation form (name contains 'install', not 'site survey',
         not archived, not already COMPLETED) and:
         a. GET the full form to build the field map from formLayouts.
         b. Set 'Install Completed Date' (and any other DATE field whose name contains
            'install', 'date', or 'completed') to today's date in ET.
         c. Mark the form COMPLETED.
      3. If the WO was an isField work order with an uncompleted visit, mark visits done.

    Returns a dict with keys 'wo_id', 'form_id', 'visit_ids' reporting what was acted on.
    Raises on HTTP errors.
    """
    result = {'wo_id': None, 'form_id': None, 'visit_ids': []}
    today_iso = _today_et()  # e.g. 2026-05-20T00:00:00-05:00

    # ── 1. Work order ──────────────────────────────────────────────────────────
    r = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}/work-orders', headers=COP_GET)
    r.raise_for_status()
    all_wos = r.json()

    install_wo = None
    for wo in all_wos:
        title = (wo.get('title') or '').lower()
        if 'install' not in title:
            continue
        if 'site survey' in title:
            continue
        if wo.get('isArchived'):
            continue
        status_id = wo.get('status') if isinstance(wo.get('status'), str) else (wo.get('status') or {}).get('id', '')
        if str(status_id).upper() == 'COMPLETED':
            continue
        install_wo = wo
        break

    if install_wo is None:
        log.warning(f'[coperniq] No incomplete Solar Installation WO found for project {project_id}')
    else:
        wo_id = install_wo['id']
        result['wo_id'] = wo_id
        log.info(f'[coperniq] Found install WO {wo_id}: {install_wo.get("title")}')

        # Get full WO detail to access checklist
        r = requests.get(f'{COPERNIQ_BASE}/work-orders/{wo_id}', headers=COP_GET)
        r.raise_for_status()
        wo_detail = r.json()

        # Check off each incomplete checklist item
        for item in wo_detail.get('checklist', []):
            if not item.get('isCompleted'):
                patch_r = requests.patch(
                    f'{COPERNIQ_BASE}/work-orders/{wo_id}/checklist/{item["id"]}',
                    headers=COP_POST,
                    json={'isCompleted': True},
                )
                patch_r.raise_for_status()
                log.info(f'[coperniq] Checked off checklist item {item["id"]}: {item.get("detail", "")[:60]}')

        # Mark WO COMPLETED
        patch_r = requests.patch(
            f'{COPERNIQ_BASE}/work-orders/{wo_id}',
            headers=COP_POST,
            json={'status': 'COMPLETED'},
        )
        patch_r.raise_for_status()
        log.info(f'[coperniq] WO {wo_id} marked COMPLETED')

        # Check for incomplete field visits and complete them
        for visit in wo_detail.get('visits', {}).get('visits', []):
            if not visit.get('isCompleted'):
                v_id = visit['id']
                v_r = requests.patch(
                    f'{COPERNIQ_BASE}/work-orders/{wo_id}/visits/{v_id}',
                    headers=COP_POST,
                    json={'isCompleted': True},
                )
                v_r.raise_for_status()
                result['visit_ids'].append(v_id)
                log.info(f'[coperniq] Visit {v_id} marked completed')

    # ── 2. Form ────────────────────────────────────────────────────────────────
    r = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}/forms', headers=COP_GET)
    r.raise_for_status()
    all_forms = r.json()

    install_form = None
    for f in all_forms:
        name = (f.get('name') or '').lower()
        if 'install' not in name:
            continue
        if 'site survey' in name:
            continue
        if f.get('isArchived'):
            continue
        if str(f.get('status') or '').upper() == 'COMPLETED':
            continue
        install_form = f
        break

    if install_form is None:
        log.warning(f'[coperniq] No incomplete Solar Installation form found for project {project_id}')
    else:
        form_id = install_form['id']
        result['form_id'] = form_id
        log.info(f'[coperniq] Found install form {form_id}: {install_form.get("name")}')

        # Get full form to build field map
        r = requests.get(f'{COPERNIQ_BASE}/forms/{form_id}', headers=COP_GET)
        r.raise_for_status()
        form_detail = r.json()

        # Build field map: id -> field dict, from all formLayouts groups
        fields_to_update = {}
        for group in form_detail.get('formLayouts', []):
            for field in group.get('fields', []):
                if field.get('type') != 'DATE':
                    continue
                fname = (field.get('name') or '').lower()
                if any(kw in fname for kw in ('install', 'date', 'completed')):
                    fields_to_update[field['id']] = field

        if fields_to_update:
            fields_payload = [
                {'id': fid, 'value': today_iso}
                for fid in fields_to_update
            ]
            patch_r = requests.patch(
                f'{COPERNIQ_BASE}/forms/{form_id}',
                headers=COP_POST,
                json={'fields': fields_payload, 'status': 'COMPLETED'},
            )
            patch_r.raise_for_status()
            log.info(f'[coperniq] Form {form_id} updated fields {list(fields_to_update.keys())} and marked COMPLETED')
        else:
            # No date fields to update — just mark completed
            patch_r = requests.patch(
                f'{COPERNIQ_BASE}/forms/{form_id}',
                headers=COP_POST,
                json={'status': 'COMPLETED'},
            )
            patch_r.raise_for_status()
            log.info(f'[coperniq] Form {form_id} marked COMPLETED (no date fields found)')

    return result


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
