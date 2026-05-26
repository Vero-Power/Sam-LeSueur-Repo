#!/usr/bin/env python3
"""
M3 Submission Automation
Polls Coperniq every 30 minutes for projects where PTO is granted
(PTO Submitted + PTO Approved WOs both COMPLETED, M3 WO still WAITING).
Submits M3 to Lux portal, updates Coperniq forms/WOs, sends emails, leaves note.
"""

import asyncio
import json
import logging
import os
import smtplib
import time
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ─── Config ───────────────────────────────────────────────────────────────────

GMAIL_ADDRESS    = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PW     = os.environ['GMAIL_APP_PASSWORD']
COPERNIQ_API_KEY = os.environ['COPERNIQ_API_KEY']
KATHY_EMAIL      = os.environ.get('KATHY_EMAIL', 'kathy.treanor@luxfinancial.io')
MIKE_EMAIL       = 'michael.paris@luxfinancial.io'

COPERNIQ_BASE = 'https://api.coperniq.io/v1'
POLL_INTERVAL = 1800  # 30 minutes

# WO template IDs
PTO_SUBMITTED_TEMPLATE   = 1907082
PTO_APPROVED_TEMPLATE    = 1907092
M3_WO_TEMPLATE           = 1907089
COMMISSIONING_WO_TEMPLATE = 1907086

# Form template IDs
M3_FORM_TEMPLATE           = 1191548
COMMISSIONING_FORM_TEMPLATE = 1191545

# M3 form column IDs
M3_COL_FINANCE_STATUS  = 17266976
M3_COL_SUBMITTED_DATE  = 17266977
M3_COL_FINANCE_PROVIDER = 17266979
M3_COL_PTO_LETTER      = 17266985

# Commissioning form column IDs
COMM_COL_MONITORING_UPLOAD = 17267028
COMM_COL_STATUS            = 17267029
COMM_COL_COMPLETE_DATE     = 17267030

ET = timezone(timedelta(hours=-5))

DIR            = Path(__file__).parent
PROCESSED_FILE = DIR / 'processed_m3_projects.json'

GET_H  = {'x-api-key': COPERNIQ_API_KEY}
POST_H = {'x-api-key': COPERNIQ_API_KEY, 'Content-Type': 'application/json'}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _today_et() -> str:
    return datetime.now(tz=ET).strftime('%Y-%m-%dT00:00:00-05:00')


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


def load_processed_ids() -> set:
    if PROCESSED_FILE.exists():
        return set(json.loads(PROCESSED_FILE.read_text()))
    return set()


def save_processed_ids(ids: set):
    PROCESSED_FILE.write_text(json.dumps(list(ids)))


# ─── Project discovery ────────────────────────────────────────────────────────

def get_active_projects(page_size=100) -> list:
    """Fetch all active projects (not CANCELLED/ON_HOLD/COMPLETED)."""
    projects = []
    page = 1
    while True:
        r = _api_get(f'{COPERNIQ_BASE}/projects', params={
            'limit': page_size,
            'page': page,
        })
        data = r.json()
        rows = data if isinstance(data, list) else data.get('rows', [])
        if not rows:
            break
        for p in rows:
            status = (p.get('status') or '').upper()
            if status not in ('CANCELLED', 'ON_HOLD', 'COMPLETED'):
                projects.append(p)
        if len(rows) < page_size:
            break
        page += 1
    return projects


def is_pto_granted(project_id: int) -> tuple:
    """Returns (pto_granted, m3_wo, commissioning_wo) or (False, None, None)."""
    wos = _api_get(f'{COPERNIQ_BASE}/projects/{project_id}/work-orders').json()

    pto_submitted_done = False
    pto_approved_done  = False
    m3_wo              = None
    commissioning_wo   = None

    for wo in wos:
        if wo.get('isArchived'):
            continue
        template_id = wo.get('templateId')
        status      = (wo.get('status') or '').lower()

        if template_id == PTO_SUBMITTED_TEMPLATE and status == 'completed':
            pto_submitted_done = True
        if template_id == PTO_APPROVED_TEMPLATE and status == 'completed':
            pto_approved_done = True
        if template_id == M3_WO_TEMPLATE and status == 'waiting':
            m3_wo = wo
        if template_id == COMMISSIONING_WO_TEMPLATE:
            commissioning_wo = wo

    if pto_submitted_done and pto_approved_done and m3_wo:
        return True, m3_wo, commissioning_wo
    return False, None, None


# ─── Coperniq form/WO updates ─────────────────────────────────────────────────

def get_form(project_id: int, template_id: int):
    forms = _api_get(f'{COPERNIQ_BASE}/projects/{project_id}/forms').json()
    return next((f for f in forms if f.get('templateId') == template_id and not f.get('isArchived')), None)


def update_m3_form(form_id: int):
    today = _today_et()
    _api_patch(f'{COPERNIQ_BASE}/forms/{form_id}', {'fields': [
        {'columnId': M3_COL_FINANCE_STATUS,   'value': 'M3 Submitted'},
        {'columnId': M3_COL_SUBMITTED_DATE,    'value': today},
        {'columnId': M3_COL_FINANCE_PROVIDER,  'value': 'Lux Financial'},
    ]})
    log.info('M3 form updated: Finance Status→M3 Submitted, submitted date set')


def update_commissioning_form(form_id: int):
    today = _today_et()
    _api_patch(f'{COPERNIQ_BASE}/forms/{form_id}', {'fields': [
        {'columnId': COMM_COL_STATUS,         'value': 'Completed'},
        {'columnId': COMM_COL_COMPLETE_DATE,   'value': today},
    ]})
    log.info('Commissioning form updated: status→Completed, complete date set')


def complete_commissioning_wo(project_id: int, wo_id: int):
    _api_patch(
        f'{COPERNIQ_BASE}/projects/{project_id}/work-orders/{wo_id}',
        {'status': 'COMPLETED'},
    )
    log.info(f'Commissioning WO {wo_id} → COMPLETED')


def upload_monitoring_screenshot_to_coperniq(project_id: int, form_id: int, screenshot_bytes: bytes):
    """Upload Tesla monitoring screenshot to the Commissioning form Monitoring Upload field."""
    try:
        filename = f'monitoring_screenshot_{project_id}.png'
        r = requests.post(
            f'{COPERNIQ_BASE}/files',
            headers={'x-api-key': COPERNIQ_API_KEY},
            files={'file': (filename, screenshot_bytes, 'image/png')},
            timeout=60,
        )
        if r.status_code not in (200, 201):
            log.warning(f'Coperniq file upload returned {r.status_code} — skipping monitoring upload')
            return
        file_data = r.json()
        file_id = file_data.get('id') or file_data.get('generatedName')
        if not file_id:
            log.warning('No file ID returned from Coperniq file upload')
            return
        _api_patch(f'{COPERNIQ_BASE}/forms/{form_id}', {'fields': [
            {'columnId': COMM_COL_MONITORING_UPLOAD, 'value': [file_id]},
        ]})
        log.info('Monitoring screenshot uploaded to Commissioning form')
    except Exception as e:
        log.warning(f'Could not upload monitoring screenshot to Coperniq: {e}')


def leave_note(project_id: int, customer_name: str):
    _api_post(
        f'{COPERNIQ_BASE}/projects/{project_id}/notes',
        {'body': f'M3 Submitted — automated via PTO approval. Lux portal updated, Kathy and Mike notified. [Sam LeSueur|~id:14206]'},
    )
    log.info('Note left on project')


# ─── PTO Letter ───────────────────────────────────────────────────────────────

def get_pto_letter(form_id: int) -> tuple:
    """Returns (filename, bytes) of PTO letter from M3 form, or (None, None)."""
    try:
        form = _api_get(f'{COPERNIQ_BASE}/forms/{form_id}').json()
        for layout in form.get('formLayouts', []):
            for prop in layout.get('properties', []):
                if prop.get('columnId') == M3_COL_PTO_LETTER:
                    files = prop.get('value') or []
                    if files:
                        f = files[0]
                        url  = f.get('downloadUrl')
                        name = f.get('name') or f.get('originalName') or 'PTO_Letter.pdf'
                        if url:
                            r = requests.get(url, timeout=30)
                            log.info(f'Downloaded PTO letter: {name} ({len(r.content)} bytes)')
                            return name, r.content
    except Exception as e:
        log.warning(f'Could not get PTO letter: {e}')
    return None, None


# ─── Email ────────────────────────────────────────────────────────────────────

def send_m3_email(customer_name: str):
    msg = MIMEMultipart()
    msg['From']    = GMAIL_ADDRESS
    msg['To']      = f'{KATHY_EMAIL}, {MIKE_EMAIL}'
    msg['Subject'] = f'M3 Submitted — {customer_name}'
    body = (
        f'Hi Kathy and Mike,\n\n'
        f'We have submitted M3 for {customer_name}. '
        f'Please let us know how it looks and if you need anything else from our end.\n\n'
        f'Thanks,\nSam LeSueur\nVero Power'
    )
    msg.attach(MIMEText(body, 'plain'))
    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PW)
        server.sendmail(GMAIL_ADDRESS, [KATHY_EMAIL, MIKE_EMAIL], msg.as_string())
    log.info(f'M3 email sent to Kathy and Mike for {customer_name}')


# ─── Main processing ──────────────────────────────────────────────────────────

async def process_m3(project: dict) -> bool:
    from install_browser import screenshot_tesla_commissioning, upload_to_lux_portal

    project_id    = project['id']
    customer_name = project.get('title', f'Project {project_id}')
    custom        = project.get('custom') or {}
    address       = (project.get('address') or [''])[0]
    battery_model = custom.get('battery_model', '')
    battery_kwh   = float(custom.get('battery_kwh') or 0)

    log.info(f'Processing M3 for {customer_name} (project {project_id})')

    granted, m3_wo, commissioning_wo = is_pto_granted(project_id)
    if not granted:
        log.info(f'PTO not fully granted for {customer_name} — skipping')
        return False

    # 1. Get M3 and commissioning forms
    m3_form   = get_form(project_id, M3_FORM_TEMPLATE)
    comm_form = get_form(project_id, COMMISSIONING_FORM_TEMPLATE)

    if not m3_form:
        log.error(f'M3 form not found for {customer_name}')
        return False

    # 2. Tesla commissioning screenshot
    log.info('Generating Tesla commissioning screenshot...')
    screenshot = await screenshot_tesla_commissioning(
        customer_address=address,
        customer_name=customer_name,
        battery_model=battery_model,
        battery_kwh=battery_kwh,
    )
    if not screenshot:
        log.warning('Tesla screenshot failed — continuing without it')

    # 3. Download PTO letter from Coperniq M3 form
    pto_filename, pto_bytes = get_pto_letter(m3_form['id'])
    if not pto_bytes:
        log.warning('PTO letter not found on M3 form — continuing without it')

    # 4. Upload to Lux portal (Pending PTO section)
    lux_files = []
    if screenshot:
        lux_files.append((f'{customer_name}_commissioning.png', screenshot, 'Proof of Commissioning'))
    if pto_bytes:
        lux_files.append((pto_filename or 'PTO_Letter.pdf', pto_bytes, 'PTO Letter'))

    if lux_files:
        log.info(f'Uploading {len(lux_files)} file(s) to Lux portal...')
        success = await upload_to_lux_portal(customer_name, lux_files)
        if not success:
            log.warning('Lux portal upload failed — continuing with Coperniq updates')
    else:
        log.warning('No files to upload to Lux portal')

    # 5. Update M3 form
    update_m3_form(m3_form['id'])

    # 6. Update Commissioning form + WO
    if comm_form:
        update_commissioning_form(comm_form['id'])
        if screenshot:
            upload_monitoring_screenshot_to_coperniq(project_id, comm_form['id'], screenshot)
    else:
        log.warning('Commissioning form not found — skipping commissioning updates')

    if commissioning_wo:
        complete_commissioning_wo(project_id, commissioning_wo['id'])
    else:
        log.warning('Commissioning WO not found — skipping')

    # 7. Send emails
    try:
        send_m3_email(customer_name)
    except Exception as e:
        log.warning(f'Email failed: {e}')

    # 8. Leave note
    leave_note(project_id, customer_name)

    log.info(f'M3 complete for {customer_name}')
    return True


# ─── Main loop ────────────────────────────────────────────────────────────────

async def main():
    log.info('M3 automation started — polling Coperniq every 30 minutes.')
    processed_ids = load_processed_ids()

    while True:
        try:
            log.info('Checking Coperniq for projects ready for M3 submission...')
            projects = get_active_projects()
            log.info(f'Found {len(projects)} active project(s) to check')

            for project in projects:
                project_id = project['id']
                if str(project_id) in processed_ids:
                    continue

                try:
                    success = await process_m3(project)
                    if success:
                        processed_ids.add(str(project_id))
                        save_processed_ids(processed_ids)
                except Exception as e:
                    log.exception(f'Error processing project {project_id}: {e}')

        except Exception as e:
            log.exception(f'Unexpected error — will retry on next poll: {e}')

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    asyncio.run(main())
