#!/usr/bin/env python3
"""
NTP Stipulation Automation
Monitors Gmail for LUX Financial NTP stipulation emails,
updates Coperniq form/work order, notifies closer on Slack, replies to sender.
"""

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
SLACK_BOT_TOKEN  = os.environ['SLACK_BOT_TOKEN']

COMPANY_ID        = 392
COPERNIQ_BASE     = 'https://api.coperniq.io/v1'
NTP_WO_TEMPLATE_ID  = 1907087
NTP_FORM_TEMPLATE_ID = 1191546
POLL_INTERVAL     = 120
SAM_SLACK_ID      = 'U0AB51A9J9H'
FALLBACK_SLACK_CH = 'C0AB50H2K9R'   # corporate-operations

GET_H  = {'x-api-key': COPERNIQ_API_KEY}
POST_H = {'x-api-key': COPERNIQ_API_KEY, 'Content-Type': 'application/json'}

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
    'Title Verification':                    ['title', 'property ownership', 'proof of ownership'],
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
    'Copy of ID':                            ['copy of id', 'photo id', 'id front', 'id back', 'provide an id'],
    'Social Security card':                  ['social security', 'ssn', 'ss #', 'ss#'],
}

DIR            = Path(__file__).parent
PROCESSED_FILE = DIR / 'processed_stip_emails.json'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _api_get(url, params=None, max_retries=5):
    for attempt in range(max_retries):
        r = requests.get(url, params=params, headers=GET_H, timeout=30)
        if r.status_code == 429:
            wait = 15 * (attempt + 1)
            log.warning(f'Rate limited — waiting {wait}s (retry {attempt+1}/{max_retries})')
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
            log.warning(f'Rate limited — waiting {wait}s (retry {attempt+1}/{max_retries})')
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
            log.warning(f'Rate limited — waiting {wait}s (retry {attempt+1}/{max_retries})')
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f'Still rate limited after {max_retries} retries: {url}')


# ─── Parsing ──────────────────────────────────────────────────────────────────

def parse_customer_name(subject: str) -> str:
    return subject.split('Vero NTP Stipulation:')[1].strip()


def parse_stips(body: str) -> list[str]:
    # Join soft-wrapped lines then split on bullet/dash markers
    body = re.sub(r'\r\n|\r', '\n', body)
    # Collapse lines that don't start a new bullet point into the previous line
    lines = body.split('\n')
    joined = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            joined.append('')
            continue
        is_bullet = bool(re.match(r'^[\-\•\*•]', stripped))
        if is_bullet or not joined:
            joined.append(stripped)
        else:
            joined[-1] = (joined[-1] + ' ' + stripped).strip()

    stips = []
    skip_starts = ('hello', 'upon review', 'please let', 'thank you', 'kathy', 'hi ', 'sincerely', 'best')
    for line in joined:
        line = re.sub(r'^[\s\-\•\*•]+', '', line).strip()
        if len(line) > 10 and not any(line.lower().startswith(s) for s in skip_starts):
            stips.append(line)
    return stips


def match_stips(stips: list[str]) -> list[str]:
    matched = []
    for stip in stips:
        lower = stip.lower()
        for option, keywords in STIP_KEYWORDS.items():
            if any(kw in lower for kw in keywords) and option not in matched:
                matched.append(option)
    return matched


# ─── Gmail ────────────────────────────────────────────────────────────────────

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


def fetch_new_stip_emails(processed_ids: set) -> list:
    for attempt in range(3):
        try:
            mail = imaplib.IMAP4_SSL('imap.gmail.com', timeout=30)
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
                if 'Vero NTP Stipulation:' not in subject or subject.strip().lower().startswith('re:'):
                    continue
                emails.append({
                    'id':      msg_id,
                    'subject': subject,
                    'sender':  msg.get('From', ''),
                    'body':    _extract_body(msg),
                })

            try:
                mail.logout()
            except Exception:
                pass
            return emails
        except Exception as e:
            if attempt < 2:
                log.warning(f'IMAP error (attempt {attempt + 1}/3): {e} — retrying in 5s')
                time.sleep(5)
            else:
                log.warning(f'IMAP unavailable after 3 attempts: {e}')
                return []


def send_reply(to: str, subject: str):
    msg = MIMEText('Hi Kathy,\n\nThank you for the heads up — we are on it!')
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
    rows = [r for r in rows if r.get('id')]
    if not rows:
        raise ValueError(f'No project found for: {customer_name}')
    # If multiple results, prefer the one whose title contains the full name or first name
    if len(rows) > 1:
        first_name = customer_name.split()[0].lower()
        exact = [r for r in rows if customer_name.lower() in (r.get('title') or '').lower()]
        if exact:
            rows = exact
        else:
            first_match = [r for r in rows if first_name in (r.get('title') or '').lower()]
            if first_match:
                rows = first_match
    log.info(f'Project found: {rows[0]["id"]} — {rows[0].get("title")}')
    return rows[0]


def get_project(project_id: int) -> dict:
    r = _api_get(f'{COPERNIQ_BASE}/projects/{project_id}')
    return r.json()


def _start_ntp_phase(project_id: int, ntp_phase_id: int):
    """Trigger Coperniq to start the NTP phase by sending multiple PATCH signals."""
    log.info('NTP phase not started — starting it now...')
    project = _api_get(f'{COPERNIQ_BASE}/projects/{project_id}').json()
    ntp_phase_template_id = None
    for phase in project.get('phaseInstances', []):
        if phase['id'] == ntp_phase_id:
            ntp_phase_template_id = (phase.get('phaseTemplate') or {}).get('id')
            break
    # Send multiple patches — Coperniq requires more than one signal to start a phase
    for body in [
        {'phaseInstanceId': ntp_phase_id},
        {'currentPhaseInstanceId': ntp_phase_id},
        {'phaseId': ntp_phase_template_id} if ntp_phase_template_id else {},
        {'activePhaseInstanceId': ntp_phase_id},
    ]:
        if body:
            _api_patch(f'{COPERNIQ_BASE}/projects/{project_id}', body)
            time.sleep(0.5)


def get_or_create_ntp_wo(project_id: int) -> dict:
    r = _api_get(f'{COPERNIQ_BASE}/projects/{project_id}/work-orders')
    work_orders = r.json()

    wo = next(
        (w for w in work_orders
         if not w.get('isArchived')
         and 'Notice to Proceed' in (w.get('title') or '')),
        None,
    )
    if wo:
        log.info(f'NTP work order found: {wo["id"]}')
        return wo

    log.info('NTP work order not found — creating from template...')
    project = _api_get(f'{COPERNIQ_BASE}/projects/{project_id}').json()
    ntp_phase_id = None
    ntp_phase_status = None
    for phase in project.get('phaseInstances', []):
        if 'notice to proceed' in (phase.get('name') or '').lower():
            ntp_phase_id = phase['id']
            ntp_phase_status = phase.get('status')
            break

    # If NTP phase is NOT_STARTED, try to start it first
    # Coperniq silently rejects WO creation in NOT_STARTED phases (returns template ID instead of new WO)
    if ntp_phase_id and ntp_phase_status == 'NOT_STARTED':
        _start_ntp_phase(project_id, ntp_phase_id)
        for _ in range(30):
            time.sleep(2)
            refreshed = _api_get(f'{COPERNIQ_BASE}/projects/{project_id}').json()
            ntp_phase_status = next(
                (p.get('status') for p in refreshed.get('phaseInstances', []) if p['id'] == ntp_phase_id),
                None,
            )
            if ntp_phase_status == 'IN_PROGRESS':
                log.info('NTP phase is now IN_PROGRESS')
                break
        else:
            log.warning('NTP phase still NOT_STARTED after 60s — WO creation will likely fail')

        # Re-check for existing WO now that phase is active — Coperniq hides WOs in NOT_STARTED phases
        refreshed_wos = _api_get(f'{COPERNIQ_BASE}/projects/{project_id}/work-orders').json()
        wo = next(
            (w for w in refreshed_wos
             if not w.get('isArchived')
             and 'Notice to Proceed' in (w.get('title') or '')),
            None,
        )
        if wo:
            log.info(f'NTP work order found after phase start: {wo["id"]}')
            return wo

    # Create WO with phaseInstanceId so it lands in the NTP phase, not Other
    body = {'templateId': NTP_WO_TEMPLATE_ID}
    if ntp_phase_id:
        body['phaseInstanceId'] = ntp_phase_id
    r2 = _api_post(f'{COPERNIQ_BASE}/projects/{project_id}/work-orders', body)
    wo = r2.json()
    # Coperniq silently rejects WO creation when phase is NOT_STARTED — returns template ID instead
    if wo.get('id') == NTP_WO_TEMPLATE_ID:
        raise RuntimeError(
            f'WO creation failed — NTP phase {ntp_phase_id} is NOT_STARTED. '
            'Start the phase in Coperniq UI and this will retry automatically.'
        )
    log.info(f'NTP work order created: {wo["id"]}')
    return wo


def get_or_create_ntp_form(project_id: int) -> dict:
    r = _api_get(f'{COPERNIQ_BASE}/projects/{project_id}/forms')
    stub = next(
        (f for f in r.json()
         if not f.get('isArchived')
         and 'Notice to Proceed' in (f.get('name') or '')),
        None,
    )
    if not stub:
        log.info('NTP form not found — creating from template...')
        r2 = _api_post(
            f'{COPERNIQ_BASE}/projects/{project_id}/forms',
            {'templateId': NTP_FORM_TEMPLATE_ID},
        )
        stub = r2.json()
    r3 = _api_get(f'{COPERNIQ_BASE}/forms/{stub["id"]}')
    log.info(f'NTP form found: {stub["id"]}')
    return r3.json()


def build_field_map(form: dict) -> dict:
    all_props = []
    for layout in form.get('formLayouts', []):
        for prop in layout.get('properties', []):
            all_props.append(prop)
            for field in prop.get('fields', []):
                all_props.append(field)
    return {p['name']: p for p in all_props if 'name' in p}


# ─── Slack ────────────────────────────────────────────────────────────────────

def _slack_user_id_from_email(email: str):
    r = requests.get(
        'https://slack.com/api/users.lookupByEmail',
        params={'email': email},
        headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}'},
        timeout=30,
    )
    data = r.json()
    if data.get('ok'):
        return data['user']['id']
    log.warning(f'Could not find Slack user for {email}: {data.get("error")}')
    return None


def _slack_channel_for_closer(closer_name: str) -> str:
    last_name = closer_name.split()[-1].lower() if closer_name else ''
    r = requests.get(
        'https://slack.com/api/conversations.list',
        params={'limit': 200, 'types': 'public_channel,private_channel'},
        headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}'},
        timeout=30,
    )
    data = r.json()
    if data.get('ok'):
        for ch in data.get('channels', []):
            name = ch['name']
            if last_name and last_name in name and name.endswith('-ops'):
                return ch['id']
    log.warning(f'No ops channel found for {closer_name}, using fallback')
    return FALLBACK_SLACK_CH


def send_slack(channel: str, text: str):
    r = requests.post(
        'https://slack.com/api/chat.postMessage',
        headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}', 'Content-Type': 'application/json'},
        json={'channel': channel, 'text': text},
        timeout=30,
    )
    data = r.json()
    if not data.get('ok'):
        raise ValueError(f'Slack error: {data.get("error")}')


# ─── Core processing ──────────────────────────────────────────────────────────

def process_stip_email(email: dict):
    customer_name = parse_customer_name(email['subject'])
    stips         = parse_stips(email['body'])
    matched_stips = match_stips(stips)
    stips_text    = '\n'.join(f'• {s}' for s in stips)

    log.info(f'Processing stip for: {customer_name}')
    log.info(f'Stips found: {stips}')
    log.info(f'Matched to dropdowns: {matched_stips}')

    # 1. Find project
    project    = find_project(customer_name)
    project_id = project['id']
    full       = get_project(project_id)

    # Skip cancelled or on-hold projects
    project_status = (full.get('status') or '').upper()
    if project_status in ('CANCELLED', 'ON_HOLD', 'HOLD'):
        log.info(f'Skipping — project status is {project_status}')
        return

    closer_name  = (full.get('custom') or {}).get('sales_closer_name', '')
    closer_email = (full.get('custom') or {}).get('sales_closer_email', '')

    # 2. Get or create NTP work order (opens phase if needed)
    wo = get_or_create_ntp_wo(project_id)

    # 3. Get NTP form, update Finance Status + Stipulations
    form      = get_or_create_ntp_form(project_id)
    field_map = build_field_map(form)

    # Skip if already NTP Approved or beyond
    current_status = field_map.get('Finance Status', {}).get('value', '') or ''
    if isinstance(current_status, list):
        current_status = ' '.join(current_status)
    skip_statuses = ('NTP Approved', 'M2 Approved', 'M2 Submitted')
    if any(s.lower() in current_status.lower() for s in skip_statuses):
        log.info(f'Skipping — Finance Status is already "{current_status}"')
        return

    fields = []
    if 'Finance Status' in field_map:
        fields.append({'columnId': field_map['Finance Status']['columnId'], 'value': 'Pending Stipulation'})
    if 'Stipulations' in field_map:
        fields.append({'columnId': field_map['Stipulations']['columnId'], 'value': matched_stips})

    if fields:
        _api_patch(f'{COPERNIQ_BASE}/forms/{form["id"]}', {'fields': fields})
        log.info('Form updated')

    # 4. Set work order to WAITING
    _api_patch(
        f'{COPERNIQ_BASE}/projects/{project_id}/work-orders/{wo["id"]}',
        {'status': 'WAITING'},
    )
    log.info('Work order set to WAITING')

    # 5. Leave note on project tagging Sam
    note = (
        f'🔴 NTP Stipulation received from LUX Financial for {customer_name}.\n\n'
        f'{stips_text}\n\n'
        f'[Sam LeSueur|~id:14206] — please review.'
    )
    _api_post(
        f'{COPERNIQ_BASE}/projects/{project_id}/notes',
        {'body': note},
    )
    log.info('Note left on project')

    # 6. Notify closer — Slack if they have an ops channel, email otherwise
    rep_slack_id  = _slack_user_id_from_email(closer_email) if closer_email else None
    rep_tag       = f'<@{rep_slack_id}>' if rep_slack_id else (closer_name or 'Rep')
    last_name     = closer_name.split()[-1].lower() if closer_name else ''
    slack_channel = _slack_channel_for_closer(closer_name)

    if slack_channel != FALLBACK_SLACK_CH:
        send_slack(
            slack_channel,
            f'🔴 *NTP Stipulation — {customer_name}*\n\n'
            f'{rep_tag} — LUX Financial has requested the following stips:\n\n'
            f'{stips_text}\n\n'
            f'Please work with your customer to resolve these ASAP.',
        )
        log.info(f'Slack sent to {closer_name} ({slack_channel})')
    elif closer_email:
        msg = MIMEText(
            f'Hi {closer_name},\n\n'
            f'LUX Financial has requested the following stipulations for {customer_name}:\n\n'
            f'{stips_text}\n\n'
            f'Please work with your customer to resolve these ASAP.\n\n'
            f'— Sam LeSueur'
        )
        msg['From']    = GMAIL_ADDRESS
        msg['To']      = closer_email
        msg['Subject'] = f'NTP Stipulation — {customer_name}'
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PW)
            server.send_message(msg)
        log.info(f'Email sent to rep {closer_name} ({closer_email}) — no Slack ops channel found')
    else:
        send_slack(
            FALLBACK_SLACK_CH,
            f'🔴 *NTP Stipulation — {customer_name}*\n\n'
            f'{closer_name or "Rep"} — LUX Financial has requested the following stips:\n\n'
            f'{stips_text}\n\n'
            f'Please work with your customer to resolve these ASAP.',
        )
        log.info(f'Slack sent to fallback channel — no rep email or ops channel for {closer_name}')

    # 7. Reply to Lux
    send_reply(email['sender'], email['subject'])

    log.info(f'Done: {customer_name} (project {project_id})')


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
                    log.exception(f'Failed to process {email["id"]}')
                time.sleep(3)
        except Exception:
            log.exception('Unexpected error — will retry on next poll.')
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
