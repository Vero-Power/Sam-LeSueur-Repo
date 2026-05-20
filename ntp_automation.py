#!/usr/bin/env python3
"""
NTP Approval Automation
Monitors Gmail for LUX Financial NTP approval emails and updates Coperniq.
"""

import imaplib
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from email import message_from_bytes
from email.header import decode_header
from pathlib import Path

import requests
from dotenv import load_dotenv
import os

load_dotenv()

# --- Config ---
GMAIL_ADDRESS    = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PW     = os.environ['GMAIL_APP_PASSWORD']
API_KEY = os.environ['COPERNIQ_API_KEY']
BASE_URL = 'https://api.coperniq.io/v1'
POLL_INTERVAL = 120
NTP_WO_TEMPLATE_ID = 1907087
NTP_FORM_TEMPLATE_ID = 1191546

DIR = Path(__file__).parent
PROCESSED_FILE = DIR / 'processed_emails.json'

ET = timezone(timedelta(hours=-5))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)


# --- Gmail (IMAP) ---

def _decode_subject(raw: str) -> str:
    parts = decode_header(raw)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            result.append(part)
    return ''.join(result)


def load_processed_ids() -> set:
    if PROCESSED_FILE.exists():
        return set(json.loads(PROCESSED_FILE.read_text()))
    return set()


def save_processed_ids(ids: set):
    PROCESSED_FILE.write_text(json.dumps(list(ids)))


def fetch_new_ntp_emails(processed_ids: set) -> list:
    mail = imaplib.IMAP4_SSL('imap.gmail.com', timeout=30)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PW)
    mail.select('inbox')

    _, data = mail.search(None, 'SUBJECT "Vero LLC NTP Approval:"')
    new = []
    for num in data[0].split():
        _, raw = mail.fetch(num, '(RFC822)')
        msg = message_from_bytes(raw[0][1])
        msg_id = msg.get('Message-ID', '').strip()
        if not msg_id or msg_id in processed_ids:
            continue
        subject = _decode_subject(msg.get('Subject', ''))
        if 'Vero LLC NTP Approval:' in subject and not subject.strip().lower().startswith('re:'):
            new.append({'id': msg_id, 'subject': subject})

    mail.logout()
    return new


# --- Coperniq API ---

def _get_headers():
    return {'x-api-key': API_KEY}


def _post_headers():
    return {'x-api-key': API_KEY, 'Content-Type': 'application/json'}


def _today_et() -> str:
    return datetime.now(tz=ET).strftime('%Y-%m-%dT00:00:00-05:00')


def process_ntp_approval(subject: str) -> dict:
    customer_name = subject.split('Vero LLC NTP Approval:')[1].strip()
    log.info(f'Processing NTP approval for: {customer_name}')

    # 1. Find project
    r = requests.get(
        f'{BASE_URL}/projects/search',
        params={'prop1': 'title', 'op1': 'contains', 'value1': customer_name},
        headers=_get_headers(),
    )
    projects = r.json()
    if not projects:
        log.error(f'No project found for "{customer_name}"')
        return {'success': False, 'message': f'No project found for {customer_name}'}
    project_id = projects[0]['id']
    log.info(f'Project ID: {project_id}')

    # 2. Find or create NTP work order
    work_orders = requests.get(
        f'{BASE_URL}/projects/{project_id}/work-orders', headers=_get_headers()
    ).json()
    ntp_wo = next(
        (wo for wo in work_orders
         if wo.get('title') and 'Notice to Proceed' in wo['title']
         and not wo.get('isArchived') and not wo.get('isField')),
        None,
    )
    if not ntp_wo:
        log.info('NTP work order not found — creating from template...')
        ntp_wo = requests.post(
            f'{BASE_URL}/projects/{project_id}/work-orders',
            headers=_post_headers(),
            json={'templateId': NTP_WO_TEMPLATE_ID},
        ).json()
    log.info(f'Work order ID: {ntp_wo["id"]}')

    # 3. Check off all checklist items
    checklist = [{'id': item['id'], 'isCompleted': True} for item in ntp_wo.get('checklist', [])]
    requests.patch(
        f'{BASE_URL}/projects/{project_id}/work-orders/{ntp_wo["id"]}',
        headers=_post_headers(),
        json={'checklist': checklist},
    )

    # 4. Find or create NTP form
    forms = requests.get(
        f'{BASE_URL}/projects/{project_id}/forms', headers=_get_headers()
    ).json()
    ntp_form_stub = next(
        (f for f in forms
         if f.get('name') and 'Notice to Proceed' in f['name'] and not f.get('isArchived')),
        None,
    )
    if not ntp_form_stub:
        log.info('NTP form not found — creating from template...')
        ntp_form_stub = requests.post(
            f'{BASE_URL}/projects/{project_id}/forms',
            headers=_post_headers(),
            json={'templateId': NTP_FORM_TEMPLATE_ID},
        ).json()
    log.info(f'Form ID: {ntp_form_stub["id"]}')

    # 5. Get full form to resolve column IDs
    ntp_form = requests.get(
        f'{BASE_URL}/forms/{ntp_form_stub["id"]}', headers=_get_headers()
    ).json()

    all_props = []
    for layout in ntp_form.get('formLayouts', []):
        for prop in layout.get('properties', []):
            all_props.append(prop)
            for field in prop.get('fields', []):
                all_props.append(field)
    field_map = {p['name']: p for p in all_props if 'name' in p}

    # Skip if already M2 Approved or M2 Submitted — NTP approval email came in after M2 was already done
    current_fin = field_map.get('Finance Status', {}).get('value', '')
    if isinstance(current_fin, list): current_fin = ' '.join(current_fin)
    if any(s in current_fin for s in ('M2 Approved', 'M2 Submitted')):
        log.info(f'Skipping — Finance Status is already "{current_fin}"')
        return {'success': True, 'skipped': True, 'reason': current_fin, 'customer_name': customer_name}

    today = _today_et()
    field_values = [
        ('Finance Status',      'NTP Approved'),
        ('NTP Submitted Date',  today),
        ('NTP Completed Date',  today),
        ('Stipulations',        'NA'),
        ('Finance Provider',    'Lux Financial'),
    ]
    fields = [
        {'columnId': field_map[name]['columnId'], 'value': value}
        for name, value in field_values
        if name in field_map
    ]

    # 6. Update form fields, then mark complete
    requests.patch(
        f'{BASE_URL}/forms/{ntp_form_stub["id"]}',
        headers=_post_headers(),
        json={'fields': fields},
    )
    form_result = requests.patch(
        f'{BASE_URL}/forms/{ntp_form_stub["id"]}',
        headers=_post_headers(),
        json={'status': 'COMPLETED'},
    ).json()

    # 7. Complete work order
    wo_result = requests.patch(
        f'{BASE_URL}/projects/{project_id}/work-orders/{ntp_wo["id"]}',
        headers=_post_headers(),
        json={'status': 'COMPLETED'},
    ).json()

    # 8. Leave comment
    requests.post(
        f'{BASE_URL}/projects/{project_id}/comments',
        headers=_post_headers(),
        json={'body': 'NTP Approved - automated via LUX Financial approval email.'},
    )

    result = {
        'success': True,
        'customer_name': customer_name,
        'project_id': project_id,
        'ntp_work_order_id': ntp_wo['id'],
        'wo_status': wo_result.get('status'),
        'ntp_form_id': ntp_form_stub['id'],
        'form_completed': form_result.get('isCompleted'),
    }
    log.info(f'Completed: {result}')
    return result


# --- Main loop ---

def main():
    log.info('NTP automation started — polling Gmail every 2 minutes.')
    processed_ids = load_processed_ids()

    while True:
        try:
            log.info('Checking Gmail for new NTP approval emails...')
            new_emails = fetch_new_ntp_emails(processed_ids)
            if not new_emails:
                log.info('No new emails.')
            for email in new_emails:
                process_ntp_approval(email['subject'])
                processed_ids.add(email['id'])
                save_processed_ids(processed_ids)
        except Exception:
            log.exception('Unexpected error — will retry on next poll.')
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
