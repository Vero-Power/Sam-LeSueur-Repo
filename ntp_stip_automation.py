#!/usr/bin/env python3
"""
NTP Stipulation Automation
Monitors Gmail for LUX Financial NTP stipulation emails,
updates Coperniq, notifies the closer on Slack, and replies to sender.
"""

import imaplib
import imaplib
import json
import logging
import os
import re
import smtplib
import time
from email import message_from_bytes
from email.header import decode_header
from email.mime.text import MIMEText
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ─── Config ───────────────────────────────────────────────────────────────────

GMAIL_ADDRESS    = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PW     = os.environ['GMAIL_APP_PASSWORD']
COPERNIQ_API_KEY = os.environ['COPERNIQ_API_KEY']
COPERNIQ_BEARER  = os.environ['COPERNIQ_BEARER_TOKEN']
SLACK_BOT_TOKEN  = os.environ['SLACK_BOT_TOKEN']

NTP_WO_TEMPLATE_ID = 1907087
COMPANY_ID         = 392
FALLBACK_SLACK_CH  = 'C0AB50H2K9R'   # corporate-operations
COPERNIQ_BASE      = 'https://api.coperniq.io/v1'
POLL_INTERVAL      = 120              # seconds

GET_H    = {'x-api-key': COPERNIQ_API_KEY}
POST_H   = {'x-api-key': COPERNIQ_API_KEY, 'Content-Type': 'application/json'}
BEARER_H = {
    'Authorization': f'Bearer {COPERNIQ_BEARER}',
    'Content-Type': 'application/json',
    'Company-Id': str(COMPANY_ID),
}

SLACK_CHANNEL_MAP = {
    'alejandro opsina': 'C0ALJKNUCLD',
    'bronson ashjian':  'C0AJE2T1JFK',
    'chase armstrong':  'C0AJH0HFV8E',
    'crew wakley':      'C0AFAMZCUD9',
    'enoch cheung':     'C0APC615UVD',
    'hayden young':     'C0ACJNZ8PCP',
    'jared brough':     'C0AJNLQEQG4',
    'kaden johnson':    'C0AB4NU8W07',
    'lance orlob':      'C0AU2BHHZUM',
    'landen olsen':     'C0B0GG0FG4A',
    'liam fuller':      'C0AGDPNR5T2',
    'noah lund':        'C0ADD4FRBC1',
    'thomas morrow':    'C0ABGPQ871T',
    'tyler cooper':     'C0AET3R25GQ',
    'weston bonny':     'C0ABZ4DD0QH',
    'zachary burton':   'C0ADDJXD4RX',
    'zach burton':      'C0ADDJXD4RX',
}

STIP_KEYWORDS = {
    'Bank Verification':                     ['bank verification', 'bank'],
    'Title Verification':                    ['title'],
    'Energy Community Error':                ['energy community'],
    'Finance Contract Signature Needed':     ['signature', 'contract'],
    'Pending Change Order':                  ['change order'],
    'Identity Verification':                 ['identity', 'id verification'],
    'Address Discrepancy':                   ['address', 'city', 'discrepancy', 'different address'],
    'Design Upload Needed':                  ['design'],
    'Utility Bill Needed':                   ['utility bill needed', 'provide a bill'],
    'FEOC for Inverter/battery and racking': ['feoc', 'inverter', 'racking'],
    'Behind on Utility Bill':                ['past due', 'behind on utility', 'balance'],
    'Voided check':                          ['voided check', 'void check'],
    'Copy of ID':                            ['copy of id', 'photo id'],
    'Social Security card':                  ['social security', 'ssn'],
}

DIR            = Path(__file__).parent
PROCESSED_FILE = DIR / 'processed_stip_emails.json'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)


# ─── Parsing ──────────────────────────────────────────────────────────────────

def parse_customer_name(subject: str) -> str:
    return subject.replace('Vero NTP Stipulation:', '').strip()


def parse_stips(body: str) -> list[str]:
    section = ''
    if 'addressed:' in body:
        section = body.split('addressed:')[1].split('Please let me know')[0]
    return [
        re.sub(r'^[\s\-\•\*]+', '', line).strip()
        for line in section.split('\n')
        if len(re.sub(r'^[\s\-\•\*]+', '', line).strip()) > 10
    ]


def match_stips(stips: list[str]) -> list[str]:
    matched = []
    for stip in stips:
        lower = stip.lower()
        for option, keywords in STIP_KEYWORDS.items():
            if any(kw in lower for kw in keywords) and option not in matched:
                matched.append(option)
    return matched


# ─── Gmail (IMAP/SMTP) ────────────────────────────────────────────────────────

def load_processed_ids() -> set:
    if PROCESSED_FILE.exists():
        return set(json.loads(PROCESSED_FILE.read_text()))
    return set()


def save_processed_ids(ids: set):
    PROCESSED_FILE.write_text(json.dumps(list(ids)))


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


def fetch_new_stip_emails(processed_ids: set) -> list[dict]:
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PW)
    mail.select('inbox')

    _, data = mail.search(None, 'SUBJECT "Vero NTP Stipulation:"')
    emails = []
    for num in data[0].split():
        _, raw = mail.fetch(num, '(RFC822)')
        msg = message_from_bytes(raw[0][1])
        msg_id = msg.get('Message-ID', '').strip()
        if not msg_id or msg_id in processed_ids:
            continue
        subject = _decode_subject(msg.get('Subject', ''))
        if 'Vero NTP Stipulation:' not in subject:
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
    msg = MIMEText('Hi Kathy,\n\nThank you for sending this over — we are on it!')
    msg['From']    = GMAIL_ADDRESS
    msg['To']      = to
    msg['Subject'] = f'Re: {subject}'
    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PW)
        server.send_message(msg)


# ─── Coperniq ─────────────────────────────────────────────────────────────────

def find_project(last_name: str) -> dict:
    r = requests.get(
        f'{COPERNIQ_BASE}/projects/search',
        params={'prop1': 'title', 'op1': 'contains', 'value1': last_name},
        headers=GET_H,
    )
    r.raise_for_status()
    data = r.json()
    rows = data.get('rows') or (data if isinstance(data, list) else [data])
    if not rows or not rows[0].get('id'):
        raise ValueError(f'No project found for last name: {last_name}')
    return rows[0]


def get_project(project_id: int) -> dict:
    r = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}', headers=GET_H)
    r.raise_for_status()
    return r.json()


def get_or_start_ntp_wo(project_id: int) -> dict:
    r = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}/work-orders', headers=GET_H)
    r.raise_for_status()
    wo = next(
        (w for w in r.json() if w.get('templateId') == NTP_WO_TEMPLATE_ID and not w.get('isArchived')),
        None,
    )
    if wo:
        return wo

    r2 = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}/elements', headers=GET_H)
    r2.raise_for_status()
    elems = r2.json()
    if isinstance(elems, dict):
        elems = elems.get('rows', [])
    ntp_elem = next(
        (e for e in elems
         if e.get('templateId') == NTP_WO_TEMPLATE_ID or 'Notice to Proceed' in (e.get('title') or '')),
        None,
    )
    if ntp_elem:
        requests.put(
            f'https://coperniq.dev/project-service/workflow-instances/elements/instances/{ntp_elem["id"]}/start?companyId={COMPANY_ID}',
            headers=BEARER_H,
            json={},
        )
        log.info('Started NTP phase — waiting 5s for work order to be created...')
        time.sleep(5)

    r3 = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}/work-orders', headers=GET_H)
    r3.raise_for_status()
    wo = next(
        (w for w in r3.json() if w.get('templateId') == NTP_WO_TEMPLATE_ID and not w.get('isArchived')),
        None,
    )
    if not wo:
        raise ValueError('NTP work order not found after attempting to start phase')
    return wo


def get_ntp_form(project_id: int) -> dict:
    r = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}/forms', headers=GET_H)
    r.raise_for_status()
    stub = next(
        (f for f in r.json() if 'Notice to Proceed' in f.get('name', '') and not f.get('isArchived')),
        None,
    )
    if not stub:
        raise ValueError('NTP form not found')
    r2 = requests.get(f'{COPERNIQ_BASE}/forms/{stub["id"]}', headers=GET_H)
    r2.raise_for_status()
    return r2.json()


def get_stip_column_id(form: dict) -> str:
    for layout in form.get('formLayouts', []):
        for prop in layout.get('properties', []):
            if prop.get('name') == 'Stipulations':
                return prop['columnId']
            for field in prop.get('fields', []):
                if field.get('name') == 'Stipulations':
                    return field['columnId']
    raise ValueError('Stipulations field not found in form')


# ─── Slack ────────────────────────────────────────────────────────────────────

def send_slack(channel: str, text: str):
    r = requests.post(
        'https://slack.com/api/chat.postMessage',
        headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}', 'Content-Type': 'application/json'},
        json={'channel': channel, 'text': text},
    )
    data = r.json()
    if not data.get('ok'):
        raise ValueError(f'Slack error: {data.get("error")}')


# ─── Core processing ──────────────────────────────────────────────────────────

def process_stip_email(email: dict) -> dict:
    customer_name = parse_customer_name(email['subject'])
    last_name     = customer_name.split()[-1]
    stips         = parse_stips(email['body'])
    matched_stips = match_stips(stips)
    stips_text    = '\n'.join(f'{i+1}. {s}' for i, s in enumerate(stips))

    log.info(f'Processing: {customer_name}')

    project_stub = find_project(last_name)
    project_id   = project_stub['id']
    full_project = get_project(project_id)

    closer_name   = (full_project.get('custom') or {}).get('closer_name_10') or \
                    (full_project.get('custom') or {}).get('sales_closer_name', '')
    slack_channel = SLACK_CHANNEL_MAP.get(closer_name.strip().lower(), FALLBACK_SLACK_CH)

    ntp_wo   = get_or_start_ntp_wo(project_id)
    ntp_form = get_ntp_form(project_id)
    col_id   = get_stip_column_id(ntp_form)

    requests.patch(
        f'{COPERNIQ_BASE}/forms/{ntp_form["id"]}',
        headers=POST_H,
        json={'fields': [{'columnId': col_id, 'value': matched_stips}]},
    ).raise_for_status()

    note = (
        f'🔴 NTP Stipulations from LUX Financial for {customer_name}:\n\n'
        f'{stips_text}\n\n'
        f'Added to form: {", ".join(matched_stips)}'
    )
    requests.post(
        f'{COPERNIQ_BASE}/projects/{project_id}/comments',
        headers=POST_H,
        json={'body': note},
    ).raise_for_status()

    requests.patch(
        f'{COPERNIQ_BASE}/projects/{project_id}/work-orders/{ntp_wo["id"]}',
        headers=POST_H,
        json={'status': 'WAITING'},
    ).raise_for_status()

    send_slack(
        slack_channel,
        f'🔴 *NTP Stipulation — {customer_name}*\n\n'
        f'LUX Financial has requested the following stips:\n{stips_text}\n\n'
        f'Please work with your customer to resolve these ASAP.',
    )

    send_reply(email['sender'], email['subject'])

    result = {
        'success':       True,
        'customer_name': customer_name,
        'project_id':    project_id,
        'ntp_form_id':   ntp_form['id'],
        'ntp_wo_id':     ntp_wo['id'],
        'matched_stips': matched_stips,
        'slack_channel': slack_channel,
    }
    log.info(f'Done: {result}')
    return result


# ─── Main loop ────────────────────────────────────────────────────────────────

def main():
    log.info('NTP Stip automation started — polling Gmail every 2 minutes.')
    processed_ids = load_processed_ids()

    while True:
        try:
            log.info('Checking Gmail for NTP stipulation emails...')
            emails = fetch_new_stip_emails(processed_ids)
            if not emails:
                log.info('No new emails.')
            for email in emails:
                try:
                    process_stip_email(email)
                    processed_ids.add(email['id'])
                    save_processed_ids(processed_ids)
                except Exception:
                    log.exception(f'Failed to process email {email["id"]}')
        except Exception:
            log.exception('Unexpected error — will retry on next poll.')
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
