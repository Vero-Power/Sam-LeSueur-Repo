#!/usr/bin/env python3
"""
NTP Approval Automation
Monitors Gmail for LUX Financial NTP approval emails and updates Coperniq.
"""

import json
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# --- Config ---
API_KEY = '5756c367-6917-4a5c-b388-6c0b4be05525'
BASE_URL = 'https://api.coperniq.io/v1'
POLL_INTERVAL = 120  # seconds (2 minutes)
NTP_WO_TEMPLATE_ID = 1907087
NTP_FORM_TEMPLATE_ID = 1191546

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
DIR = Path(__file__).parent
TOKEN_FILE = DIR / 'token.json'
CREDENTIALS_FILE = DIR / 'credentials.json'
PROCESSED_FILE = DIR / 'processed_emails.json'

ET = timezone(timedelta(hours=-5))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)


# --- Gmail ---

def get_gmail_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return build('gmail', 'v1', credentials=creds)


def load_processed_ids() -> set:
    if PROCESSED_FILE.exists():
        return set(json.loads(PROCESSED_FILE.read_text()))
    return set()


def save_processed_ids(ids: set):
    PROCESSED_FILE.write_text(json.dumps(list(ids)))


def fetch_new_ntp_emails(service, processed_ids: set) -> list:
    results = service.users().messages().list(
        userId='me',
        q='subject:"Vero LLC NTP Approval:"',
    ).execute()

    new = []
    for msg in results.get('messages', []):
        if msg['id'] in processed_ids:
            continue
        full = service.users().messages().get(
            userId='me', id=msg['id'], format='metadata',
            metadataHeaders=['Subject', 'From'],
        ).execute()
        headers = {h['name']: h['value'] for h in full['payload']['headers']}
        subject = headers.get('Subject', '')
        if 'Vero LLC NTP Approval:' in subject:
            new.append({'id': msg['id'], 'subject': subject})

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
    service = get_gmail_service()
    processed_ids = load_processed_ids()

    while True:
        try:
            log.info('Checking Gmail for new NTP approval emails...')
            new_emails = fetch_new_ntp_emails(service, processed_ids)
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
