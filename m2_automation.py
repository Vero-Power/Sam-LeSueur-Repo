#!/usr/bin/env python3
"""
M2 Approval Automation
Monitors Gmail for LUX Financial M2 approval emails and updates Coperniq.
"""

import imaplib
import json
import logging
import os
import re
import smtplib
import time
from datetime import datetime, timezone, timedelta
from email import message_from_bytes
from email.header import decode_header
from email.mime.text import MIMEText
from pathlib import Path

import requests
from dotenv import load_dotenv

def _api_get(url, params=None, max_retries=5):
    for attempt in range(max_retries):
        r = requests.get(url, params=params, headers=GET_H, timeout=30)
        if r.status_code == 429:
            wait = 15 * (attempt + 1)
            log.warning(f'Rate limited — waiting {wait}s before retry {attempt + 1}/{max_retries}')
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f'Still rate limited after {max_retries} retries: {url}')


def _api_patch(url, json_body, max_retries=5):
    for attempt in range(max_retries):
        r = requests.patch(url, headers=POST_H, json=json_body, timeout=30)
        if r.status_code == 429:
            wait = 15 * (attempt + 1)
            log.warning(f'Rate limited — waiting {wait}s before retry {attempt + 1}/{max_retries}')
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f'Still rate limited after {max_retries} retries: {url}')


def _api_post(url, json_body, max_retries=5):
    for attempt in range(max_retries):
        r = requests.post(url, headers=POST_H, json=json_body, timeout=30)
        if r.status_code == 429:
            wait = 15 * (attempt + 1)
            log.warning(f'Rate limited — waiting {wait}s before retry {attempt + 1}/{max_retries}')
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f'Still rate limited after {max_retries} retries: {url}')

load_dotenv()

# ─── Config ───────────────────────────────────────────────────────────────────

GMAIL_ADDRESS    = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PW     = os.environ['GMAIL_APP_PASSWORD']
COPERNIQ_API_KEY = os.environ['COPERNIQ_API_KEY']

COMPANY_ID    = 392
COPERNIQ_BASE = 'https://api.coperniq.io/v1'
POLL_INTERVAL = 120

GET_H  = {'x-api-key': COPERNIQ_API_KEY}
POST_H = {'x-api-key': COPERNIQ_API_KEY, 'Content-Type': 'application/json'}

ET = timezone(timedelta(hours=-5))

DIR            = Path(__file__).parent
PROCESSED_FILE = DIR / 'processed_m2_emails.json'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _today_et() -> str:
    return datetime.now(tz=ET).strftime('%Y-%m-%dT00:00:00-05:00')


def _decode_subject(raw: str) -> str:
    parts = decode_header(raw)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            result.append(part)
    return ''.join(result)


def _extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/plain':
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                payload = part.get_payload(decode=True)
                if payload:
                    html = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
                    return re.sub(r'<[^>]+>', ' ', html)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or 'utf-8', errors='replace')
    return ''


# ─── Email parsing ────────────────────────────────────────────────────────────

def parse_customer_name(subject: str) -> str:
    return subject.split('Vero LLC M2 Approval:')[1].strip()


def parse_email_fields(body: str) -> dict:
    def extract(pattern):
        m = re.search(pattern, body, re.IGNORECASE)
        return m.group(1).strip() if m else ''

    return {
        'system_size':        extract(r'System Size:\s*(.+)'),
        'product_type':       extract(r'Product Type:\s*(.+)'),
        'monthly_payment':    extract(r'Monthly Payment:\s*(.+)'),
        'epc_install_payout': extract(r'EPC Install Payout:\s*(.+)'),
        'epc_pto_payout':     extract(r'EPC PTO Payout:\s*(.+)'),
        'epc_total_payout':   extract(r'EPC Total Payout:\s*(.+)'),
    }


# ─── Gmail (IMAP / SMTP) ──────────────────────────────────────────────────────

def load_processed_ids() -> set:
    if PROCESSED_FILE.exists():
        return set(json.loads(PROCESSED_FILE.read_text()))
    return set()


def save_processed_ids(ids: set):
    PROCESSED_FILE.write_text(json.dumps(list(ids)))


def fetch_new_m2_emails(processed_ids: set) -> list[dict]:
    mail = imaplib.IMAP4_SSL('imap.gmail.com', timeout=30)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PW)
    mail.select('inbox')

    _, data = mail.search(None, 'SUBJECT "Vero LLC M2 Approval:"')
    emails = []
    for num in data[0].split():
        _, raw = mail.fetch(num, '(RFC822)')
        msg = message_from_bytes(raw[0][1])
        msg_id = msg.get('Message-ID', '').strip()
        if not msg_id or msg_id in processed_ids:
            continue
        subject = _decode_subject(msg.get('Subject', ''))
        if 'Vero LLC M2 Approval:' not in subject:
            continue
        emails.append({
            'id':      msg_id,
            'subject': subject,
            'sender':  msg.get('From', ''),
            'body':    _extract_body(msg),
        })

    mail.logout()
    return emails


def send_reply(to: str, subject: str):
    msg = MIMEText('Thank you!')
    msg['From']    = GMAIL_ADDRESS
    msg['To']      = to
    msg['Subject'] = f'Re: {subject}'
    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PW)
        server.send_message(msg)
    log.info(f'Reply sent to {to}')


# ─── Coperniq ─────────────────────────────────────────────────────────────────

def find_project(customer_name: str) -> dict:
    last_name = customer_name.split()[-1]
    r = _api_get(
        f'{COPERNIQ_BASE}/projects/search',
        params={'prop1': 'title', 'op1': 'contains', 'value1': last_name},
    )
    data = r.json()
    rows = data if isinstance(data, list) else data.get('rows') or [data]
    if not rows or not rows[0].get('id'):
        raise ValueError(f'No project found for: {customer_name}')
    log.info(f'Project found: {rows[0]["id"]} — {rows[0].get("title")}')
    return rows[0]


def get_m2_work_order(project_id: int) -> dict:
    r = _api_get(f'{COPERNIQ_BASE}/projects/{project_id}/work-orders')
    wo = next(
        (w for w in r.json()
         if not w.get('isArchived')
         and any(kw in (w.get('title') or '').lower() for kw in ['milestone 2', 'm2'])),
        None,
    )
    if not wo:
        raise ValueError(f'M2 work order not found on project {project_id}')
    log.info(f'M2 work order found: {wo["id"]} — {wo.get("title")}')
    return wo


def get_m2_form(project_id: int) -> dict:
    r = _api_get(f'{COPERNIQ_BASE}/projects/{project_id}/forms')
    stub = next(
        (f for f in r.json()
         if not f.get('isArchived')
         and any(kw in (f.get('name') or '').lower() for kw in ['milestone 2', 'm2'])),
        None,
    )
    if not stub:
        raise ValueError(f'M2 form not found on project {project_id}')
    r2 = _api_get(f'{COPERNIQ_BASE}/forms/{stub["id"]}')
    form = r2.json()
    log.info(f'M2 form found: {stub["id"]} — {stub.get("name")}')
    return form


def build_field_map(form: dict) -> dict:
    all_props = []
    for layout in form.get('formLayouts', []):
        for prop in layout.get('properties', []):
            all_props.append(prop)
            for field in prop.get('fields', []):
                all_props.append(field)
    field_map = {p['name']: p for p in all_props if 'name' in p}
    log.info(f'Available form fields: {list(field_map.keys())}')
    return field_map


# ─── Core processing ──────────────────────────────────────────────────────────

def process_m2_email(email: dict):
    customer_name = parse_customer_name(email['subject'])
    parsed        = parse_email_fields(email['body'])
    today         = _today_et()

    log.info(f'Processing M2 approval for: {customer_name}')

    # 1. Find project
    project = find_project(customer_name)
    project_id = project['id']

    # 2. Find M2 work order (fetch now, act on it after the form)
    wo = get_m2_work_order(project_id)

    # 3. Find M2 form, fill all fields (Finance Status → M2 Approved first)
    form      = get_m2_form(project_id)
    form_id   = form['id']
    field_map = build_field_map(form)

    desired_fields = {
        'Finance Status':               'M2 Approved',
        'M2 Submitted Date':            today,
        'M2 Completed Date':            today,
        'Finance Provider':             'Lux Financial',
        'Finance Product Type':         parsed['product_type'],
        'Financing Monthly Payment ($)': parsed['monthly_payment'],
    }
    fields = [
        {'columnId': field_map[name]['columnId'], 'value': value}
        for name, value in desired_fields.items()
        if name in field_map and value
    ]
    log.info(f'Updating {len(fields)} form fields')
    _api_patch(f'{COPERNIQ_BASE}/forms/{form_id}', {'fields': fields})

    # 4. Complete form
    _api_patch(f'{COPERNIQ_BASE}/forms/{form_id}', {'status': 'COMPLETED'})
    log.info('Form completed')

    # 5. Check off all work order boxes
    checklist = [{'id': item['id'], 'isCompleted': True} for item in wo.get('checklist', [])]
    if checklist:
        _api_patch(
            f'{COPERNIQ_BASE}/projects/{project_id}/work-orders/{wo["id"]}',
            {'checklist': checklist},
        )
        log.info(f'Checked off {len(checklist)} checklist items')

    # 6. Complete work order
    _api_patch(
        f'{COPERNIQ_BASE}/projects/{project_id}/work-orders/{wo["id"]}',
        {'status': 'COMPLETED'},
    )
    log.info('Work order completed')

    # 6. Leave note
    _api_post(
        f'{COPERNIQ_BASE}/projects/{project_id}/comments',
        {'body': 'M2 Approved — automated via LUX Financial approval email.'},
    )
    log.info('Note left on project')

    # 7. Reply to email
    send_reply(email['sender'], email['subject'])

    log.info(f'Done: {customer_name} (project {project_id})')


# ─── Main loop ────────────────────────────────────────────────────────────────

def main():
    log.info('M2 automation started — polling Gmail every 2 minutes.')
    processed_ids = load_processed_ids()

    while True:
        try:
            log.info('Checking Gmail for M2 approval emails...')
            emails = fetch_new_m2_emails(processed_ids)
            if not emails:
                log.info('No new emails.')
            for email in emails:
                try:
                    process_m2_email(email)
                    processed_ids.add(email['id'])
                    save_processed_ids(processed_ids)
                except Exception:
                    log.exception(f'Failed to process email {email["id"]}')
                time.sleep(3)
        except Exception:
            log.exception('Unexpected error — will retry on next poll.')
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
