#!/usr/bin/env python3
"""
Install Automation
Monitors Coperniq for completed solar installs and runs post-install workflow.
"""

import email as emaillib
import imaplib
import json
import logging
import os
import smtplib
import time
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

COPERNIQ_API_KEY = os.environ['COPERNIQ_API_KEY']
COMPANY_CAM_API_KEY = os.environ['COMPANY_CAM_API_KEY']
GMAIL_ADDRESS = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PW = os.environ['GMAIL_APP_PASSWORD']
SLACK_BOT_TOKEN = os.environ['SLACK_BOT_TOKEN']
KATHY_EMAIL = os.environ['KATHY_EMAIL']
TESLA_CLIENT_ID = os.environ['TESLA_CLIENT_ID']
TESLA_CLIENT_SECRET = os.environ['TESLA_CLIENT_SECRET']
TESLA_GROUP_ID = os.environ['TESLA_GROUP_ID']

COPERNIQ_BASE = 'https://api.coperniq.io/v1'
TESLA_AUTH_URL = 'https://gridlogic-api.sn.tesla.services/v1/auth/token'
TESLA_ASSET_BASE = 'https://gridlogic-api.sn.tesla.services/v2/asset'
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


def _is_completed(obj: dict) -> bool:
    """Return True if the Coperniq object's status is COMPLETED."""
    status = obj.get('status')
    if isinstance(status, dict):
        status = status.get('id', '')
    return (status or '').upper() == 'COMPLETED'


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
        if _is_completed(wo):
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
        visits = wo_detail.get('visits', {}).get('visits', [])
        log.info(f'Found {len(visits)} visits on install WO')
        for visit in visits:
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
        if _is_completed(f):
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

        # Build field map keyed by name, from formLayouts → properties → fields
        all_props = []
        for layout in form_detail.get('formLayouts', []):
            for prop in layout.get('properties', []):
                all_props.append(prop)
                for field in prop.get('fields', []):
                    all_props.append(field)
        field_map = {p['name']: p for p in all_props if 'name' in p}

        # Look up date fields by exact name first, fall back to keyword matching
        DATE_FIELDS = ['Install Completed Date', 'Install Scheduled Date']
        fields_payload = []
        matched_names = []
        for exact_name in DATE_FIELDS:
            if exact_name in field_map:
                col_id = field_map[exact_name].get('columnId')
                if col_id:
                    fields_payload.append({'columnId': col_id, 'value': today_iso})
                    matched_names.append(exact_name)

        if not fields_payload:
            # Fall back: any DATE field whose name contains install/date/completed
            for name, prop in field_map.items():
                if prop.get('type') != 'DATE':
                    continue
                lname = name.lower()
                if any(kw in lname for kw in ('install', 'date', 'completed')):
                    col_id = prop.get('columnId')
                    if col_id:
                        fields_payload.append({'columnId': col_id, 'value': today_iso})
                        matched_names.append(name)

        if fields_payload:
            patch_r = requests.patch(
                f'{COPERNIQ_BASE}/forms/{form_id}',
                headers=COP_POST,
                json={'fields': fields_payload, 'status': 'COMPLETED'},
            )
            patch_r.raise_for_status()
            log.info(f'[coperniq] Form {form_id} updated fields {matched_names} and marked COMPLETED')
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


def send_customer_sms(project_id: int, customer_name: str):
    """Send a post-install thank-you SMS to the customer via Coperniq.

    Coperniq's REST API does not expose a dedicated SMS-send endpoint — the
    /communications, /messages, and /sms routes all return 404.  The built-in
    Twilio integration is only reachable through the Coperniq web UI.

    Current strategy:
      1. Attempt POST /projects/{id}/communications (in case the endpoint is
         added or enabled for this account in the future).
      2. If that returns non-2xx, fall back to POST /projects/{id}/notes so
         the outgoing message text is recorded on the project as a paper trail,
         and log a WARNING so the operator knows a manual SMS is needed.
    """
    first_name = customer_name.split()[0]
    message = (
        f"Hey {first_name}! This is Sam with Vero, just checking in to make sure the install "
        f"went well and to thank you for being great to work with. If you ever have any neighbors "
        f"or friends who are interested in the program, let us know so we can send ya a $500 referral bonus!"
    )

    # Attempt 1: /communications (may be activated for this account in future)
    r = requests.post(
        f'{COPERNIQ_BASE}/projects/{project_id}/communications',
        headers=COP_POST,
        json={'body': message, 'type': 'SMS'},
    )
    if r.status_code in (200, 201):
        log.info(f'SMS sent to {customer_name} via Coperniq /communications')
        return

    log.warning(
        f'Coperniq /communications returned {r.status_code} — SMS not sent automatically. '
        f'Recording message as a project note instead. Send manually to {customer_name}.'
    )

    # Fallback: leave the message text as a note so it is not lost
    note_body = (
        f'[PENDING MANUAL SMS — send to customer]\n\n{message}'
    )
    note_r = requests.post(
        f'{COPERNIQ_BASE}/projects/{project_id}/notes',
        headers=COP_POST,
        json={'body': note_body},
    )
    if note_r.status_code not in (200, 201):
        log.warning(f'Also failed to leave note: {note_r.status_code} {note_r.text[:200]}')
    else:
        log.info(f'Note left on project {project_id} with pending SMS text for {customer_name}')


def _slack_user_id_from_email(email: str) -> Optional[str]:
    r = requests.get(
        'https://slack.com/api/users.lookupByEmail',
        params={'email': email},
        headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}'},
    )
    data = r.json()
    return data['user']['id'] if data.get('ok') else None


def send_install_slack(project: dict, photos: list):
    """Post install notification to #vero with customer/rep info and photos."""
    VERO_CHANNEL = 'C0AC5MSF4PJ'

    customer_name = project.get('title') or 'Unknown'
    custom = project.get('custom') or {}

    setter_name = custom.get('sales_setter_name') or ''
    closer_name = custom.get('sales_closer_name') or ''
    setter_email = custom.get('sales_setter_email') or ''
    closer_email = custom.get('sales_closer_email') or ''

    setter_id = _slack_user_id_from_email(setter_email) if setter_email else None
    closer_id = _slack_user_id_from_email(closer_email) if closer_email else None
    setter_tag = f'<@{setter_id}>' if setter_id else setter_name
    closer_tag = f'<@{closer_id}>' if closer_id else closer_name

    # System size from top-level 'size' field (kW)
    system_size = project.get('size')
    size_val = f'{system_size}' if system_size else '?'

    # Battery detection via custom fields
    has_battery = bool(
        custom.get('battery_manufacturer') or
        custom.get('battery_model') or
        custom.get('battery_qty')
    )
    size_str = f'{size_val}kW+battery 🔋' if has_battery else f'{size_val}kW'

    # City from top-level 'city' field
    city = project.get('city') or ''

    text = (
        f'Customer: {customer_name}\n\n'
        f'Setter: {setter_tag}\n'
        f'Closer: {closer_tag}\n\n'
        f'{size_str}\n\n'
        f'Area: {city}'
    )

    r = requests.post(
        'https://slack.com/api/chat.postMessage',
        headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}', 'Content-Type': 'application/json'},
        json={'channel': VERO_CHANNEL, 'text': text},
    )
    r.raise_for_status()
    log.info(f'Slack message sent for {customer_name}')

    # Upload photos to same channel (not threaded)
    for i, photo_bytes in enumerate(photos):
        requests.post(
            'https://slack.com/api/files.upload',
            headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}'},
            data={
                'channels': VERO_CHANNEL,
                'filename': f'install_{i+1}.jpg',
            },
            files={'file': (f'install_{i+1}.jpg', photo_bytes, 'image/jpeg')},
        )
    log.info(f'Uploaded {len(photos)} photos to Slack for {customer_name}')


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


def download_bom_from_gmail(customer_name: str) -> list:
    """Return list of (filename, bytes) tuples from the most recent [Customer] Solar Materials email."""
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PW)
    mail.select('inbox')

    _, data = mail.search(None, f'SUBJECT "{customer_name} Solar Materials"')
    if not data[0].split():
        last = customer_name.split()[-1]
        _, data = mail.search(None, f'SUBJECT "{last} Solar Materials"')

    attachments = []
    for num in reversed(data[0].split()):
        _, raw = mail.fetch(num, '(RFC822)')
        msg = emaillib.message_from_bytes(raw[0][1])
        for part in msg.walk():
            if part.get_content_disposition() == 'attachment':
                filename = part.get_filename() or 'attachment.pdf'
                payload = part.get_payload(decode=True)
                if payload:
                    attachments.append((filename, payload))
        if attachments:
            break

    mail.logout()
    log.info(f'Downloaded {len(attachments)} BOM attachment(s) for {customer_name}')
    return attachments


def download_cad_from_coperniq(project_id: int):
    """Return (filename, bytes) tuple for the most recent CAD/planset file, or None if not found."""
    r = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}/files', headers=COP_GET)
    if r.status_code != 200:
        log.warning(f'Coperniq /files returned {r.status_code} for project {project_id}')
        return None

    files = r.json() if isinstance(r.json(), list) else r.json().get('rows', [])
    cad_keywords = ['cad', 'planset', 'plan set', 'engineering', 'design', 'stamped']
    cad_files = [
        f for f in files
        if any(kw in (f.get('name') or f.get('filename') or '').lower() for kw in cad_keywords)
    ]

    if not cad_files:
        log.warning(f'No CAD/planset found for project {project_id}')
        return None

    latest = sorted(
        cad_files,
        key=lambda f: f.get('createdAt') or f.get('created_at') or '',
        reverse=True,
    )[0]
    url = latest.get('downloadUrl') or latest.get('url') or latest.get('file_url')
    if not url:
        log.warning(f'CAD file found but no download URL for project {project_id}')
        return None

    resp = requests.get(url)
    resp.raise_for_status()
    filename = latest.get('name') or latest.get('filename') or 'planset.pdf'
    log.info(f'Downloaded CAD/planset: {filename}')
    return (filename, resp.content)


# ─── Company Cam ───────────────────────────────────────────────────────────────

def find_company_cam_project(customer_name: str) -> Optional[dict]:
    """Find a Company Cam project by customer name. Returns project dict or None."""
    last_name = customer_name.split()[-1]
    for search_term in [customer_name, last_name]:
        r = requests.get(
            f'{COMPANY_CAM_BASE}/projects',
            params={'search': search_term},
            headers=CC_GET,
        )
        if r.status_code != 200:
            log.warning(f'Company Cam project search returned {r.status_code}: {r.text[:200]}')
            continue
        data = r.json()
        projects = data if isinstance(data, list) else data.get('projects', [])
        if projects:
            log.info(f'Company Cam project found: {projects[0].get("id")} — {projects[0].get("name")}')
            return projects[0]
    log.warning(f'No Company Cam project found for {customer_name}')
    return None


def is_install_checklist_complete(cc_project_id: str) -> bool:
    """Return True if VERO SOLAR INSTALLER CHECKLIST has all items completed."""
    r = requests.get(
        f'{COMPANY_CAM_BASE}/projects/{cc_project_id}/checklists',
        headers=CC_GET,
    )
    if r.status_code != 200:
        log.warning(f'Company Cam checklists returned {r.status_code} for project {cc_project_id}')
        return False
    data = r.json()
    checklists = data if isinstance(data, list) else data.get('checklists', [])
    for checklist in checklists:
        name = (checklist.get('name') or '').upper()
        if 'VERO SOLAR INSTALLER CHECKLIST' in name:
            total = checklist.get('total_items') or checklist.get('fields_count') or 0
            completed = checklist.get('completed_items') or checklist.get('completed_fields_count') or 0
            is_complete = total > 0 and completed >= total
            log.info(f'Install checklist: {completed}/{total} — {"COMPLETE" if is_complete else "INCOMPLETE"}')
            return is_complete
    log.info('VERO SOLAR INSTALLER CHECKLIST not found in Company Cam')
    return False


def get_install_photos(cc_project_id: str) -> list:
    """Return list of photo bytes from 'installed panels' and 'battery' checklist items."""
    r = requests.get(
        f'{COMPANY_CAM_BASE}/projects/{cc_project_id}/checklists',
        headers=CC_GET,
    )
    if r.status_code != 200:
        log.warning(f'Company Cam checklists returned {r.status_code}')
        return []
    data = r.json()
    checklists = data if isinstance(data, list) else data.get('checklists', [])

    checklist_id = None
    for c in checklists:
        if 'VERO SOLAR INSTALLER CHECKLIST' in (c.get('name') or '').upper():
            checklist_id = c['id']
            break
    if not checklist_id:
        return []

    r2 = requests.get(f'{COMPANY_CAM_BASE}/checklists/{checklist_id}', headers=CC_GET)
    if r2.status_code != 200:
        log.warning(f'Company Cam checklist detail returned {r2.status_code}')
        return []
    checklist_data = r2.json()

    photo_urls = []
    items = checklist_data.get('items') or checklist_data.get('fields') or []
    for item in items:
        item_name = (item.get('label') or item.get('name') or '').lower()
        if any(kw in item_name for kw in ['installed panel', 'panels', 'battery', 'powerwall']):
            photos = item.get('photos') or item.get('responses') or []
            for photo in photos:
                url = photo.get('uri') or photo.get('url') or photo.get('original')
                if url:
                    photo_urls.append(url)

    log.info(f'Found {len(photo_urls)} install/battery photo(s)')
    photos_bytes = []
    for url in photo_urls:
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                photos_bytes.append(resp.content)
        except Exception as e:
            log.warning(f'Failed to download photo from {url[:80]}: {e}')
    return photos_bytes


# ─── M2 phase ─────────────────────────────────────────────────────────────────

M2_WO_TEMPLATE_ID   = 1907088
M2_FORM_TEMPLATE_ID = 1191547


def send_m2_email_kathy(customer_name: str):
    """Email Kathy at Lux Financial notifying her that M2 was submitted."""
    msg = MIMEText(
        f'Hi Kathy,\n\n'
        f'Please see that M2 has been submitted for {customer_name}. '
        f'All required documents have been uploaded to the Lux portal.\n\n'
        f'Thank you!'
    )
    msg['From']    = GMAIL_ADDRESS
    msg['To']      = KATHY_EMAIL
    msg['Subject'] = f'M2 Submitted — {customer_name}'
    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PW)
        server.send_message(msg)
    log.info(f'M2 email sent to Kathy for {customer_name}')


def _get_or_create_m2_work_order(project_id: int) -> dict:
    """Return the existing M2 work order or create one from the template."""
    r = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}/work-orders', headers=COP_GET)
    r.raise_for_status()
    wo = next(
        (w for w in r.json()
         if not w.get('isArchived')
         and any(kw in (w.get('title') or '').lower() for kw in ['milestone 2', 'm2'])),
        None,
    )
    if wo:
        log.info(f'M2 WO found: {wo["id"]} — {wo.get("title")}')
        return wo

    # Create from template
    log.info(f'No M2 WO found for project {project_id} — creating from template {M2_WO_TEMPLATE_ID}')
    cr = requests.post(
        f'{COPERNIQ_BASE}/projects/{project_id}/work-orders',
        headers=COP_POST,
        json={'templateId': M2_WO_TEMPLATE_ID},
    )
    cr.raise_for_status()
    wo = cr.json()
    log.info(f'M2 WO created: {wo["id"]}')
    return wo


def _get_or_create_m2_form(project_id: int) -> dict:
    """Return the full M2 form (with formLayouts) or create one from the template."""
    r = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}/forms', headers=COP_GET)
    r.raise_for_status()
    stub = next(
        (f for f in r.json()
         if not f.get('isArchived')
         and any(kw in (f.get('name') or '').lower() for kw in ['milestone 2', 'm2'])),
        None,
    )
    if not stub:
        log.info(f'No M2 form found for project {project_id} — creating from template {M2_FORM_TEMPLATE_ID}')
        cr = requests.post(
            f'{COPERNIQ_BASE}/projects/{project_id}/forms',
            headers=COP_POST,
            json={'templateId': M2_FORM_TEMPLATE_ID},
        )
        cr.raise_for_status()
        stub = cr.json()
        log.info(f'M2 form created: {stub["id"]}')

    r2 = requests.get(f'{COPERNIQ_BASE}/forms/{stub["id"]}', headers=COP_GET)
    r2.raise_for_status()
    form = r2.json()
    log.info(f'M2 form loaded: {stub["id"]} — {stub.get("name")}')
    return form


def start_m2_coperniq(project_id: int, customer_name: str):
    """Kick off the M2 phase in Coperniq for the given project.

    Steps:
      1. Find or create M2 work order.
      2. Find or create M2 form.
      3. Build field_map from formLayouts.
      4. Set Finance Status → 'M2 Submitted', M2 Submitted Date and M2 Completed Date → today.
      5. Mark form COMPLETED.
      6. Set WO status → 'WAITING'.
      7. Leave note on project.
    """
    today = _today_et()

    # 1. Work order
    wo = _get_or_create_m2_work_order(project_id)
    wo_id = wo['id']

    # 2. Form (full detail with formLayouts)
    form    = _get_or_create_m2_form(project_id)
    form_id = form['id']

    # 3. Build field map — mirrors m2_automation.build_field_map()
    all_props = []
    for layout in form.get('formLayouts', []):
        for prop in layout.get('properties', []):
            all_props.append(prop)
            for field in prop.get('fields', []):
                all_props.append(field)
    field_map = {p['name']: p for p in all_props if 'name' in p}
    log.info(f'M2 form fields available: {list(field_map.keys())}')

    # 4. Set form fields
    desired = {
        'Finance Status':    'M2 Submitted',
        'M2 Submitted Date': today,
        'M2 Completed Date': today,
    }
    fields = [
        {'columnId': field_map[name]['columnId'], 'value': value}
        for name, value in desired.items()
        if name in field_map
    ]
    log.info(f'Patching {len(fields)} M2 form fields')
    patch_r = requests.patch(
        f'{COPERNIQ_BASE}/forms/{form_id}',
        headers=COP_POST,
        json={'fields': fields},
    )
    patch_r.raise_for_status()

    # 5. Complete form
    patch_r2 = requests.patch(
        f'{COPERNIQ_BASE}/forms/{form_id}',
        headers=COP_POST,
        json={'status': 'COMPLETED'},
    )
    patch_r2.raise_for_status()
    log.info(f'M2 form {form_id} marked COMPLETED')

    # 6. Set WO status to WAITING
    wo_patch = requests.patch(
        f'{COPERNIQ_BASE}/projects/{project_id}/work-orders/{wo_id}',
        headers=COP_POST,
        json={'status': 'WAITING'},
    )
    wo_patch.raise_for_status()
    log.info(f'M2 WO {wo_id} set to WAITING')

    # 7. Leave note
    note = (
        f'M2 submitted for {customer_name}. '
        f'Documents uploaded to Lux portal. '
        f'[Sam LeSueur|~id:14206]'
    )
    note_r = requests.post(
        f'{COPERNIQ_BASE}/projects/{project_id}/comments',
        headers=COP_POST,
        json={'body': note},
    )
    note_r.raise_for_status()
    log.info(f'M2 note left on project {project_id}')


def _tesla_get_token() -> Optional[str]:
    """Obtain a Bearer token from the Tesla PowerHub API using client credentials.

    Auth endpoint: POST https://gridlogic-api.sn.tesla.services/v1/auth/token
    Uses HTTP Basic auth (client_id:client_secret) with grant_type=client_credentials.
    Returns the access_token string, or None on failure.
    """
    try:
        r = requests.post(
            TESLA_AUTH_URL,
            data={'grant_type': 'client_credentials'},
            auth=(TESLA_CLIENT_ID, TESLA_CLIENT_SECRET),
            timeout=20,
        )
        r.raise_for_status()
        return r.json()['data']['access_token']
    except Exception as e:
        log.error(f'[tesla] Auth failed: {e}')
        return None


def get_tesla_commissioning_data(customer_address: str) -> Optional[dict]:
    """Get Tesla PowerHub commissioning data for a site by address.

    Tesla PowerHub API overview (discovered via endpoint probing):
      - Auth:   POST https://gridlogic-api.sn.tesla.services/v1/auth/token
                HTTP Basic auth with (client_id, client_secret), grant_type=client_credentials
      - Sites:  GET  https://gridlogic-api.sn.tesla.services/v2/asset/sites?din={gateway_din}
                Returns site data for the Tesla gateway with that DIN.

    NOTE: The PowerHub Residential API looks up sites by gateway DIN (device identifier),
    not by street address.  There is no address-search endpoint in the API.

    Workflow:
      1. Obtain a Bearer token.
      2. Look up the Coperniq project by address to find the gateway DIN stored in
         the project's custom fields (custom.tesla_gateway_din or similar).
      3. Call /v2/asset/sites?din={din} to retrieve commissioning data.
      4. If no DIN is available, log a warning and return None.

    Returns a dict of site data from the Tesla PowerHub API, or None if not found.
    """
    token = _tesla_get_token()
    if not token:
        log.error(f'[tesla] Cannot look up site — auth failed')
        return None

    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}

    # Step 1 — Try to resolve a gateway DIN by searching Coperniq for the address
    din = None
    try:
        r = requests.get(
            f'{COPERNIQ_BASE}/projects/search',
            params={'prop1': 'address', 'op1': 'contains', 'value1': customer_address.split(',')[0].strip()},
            headers=COP_GET,
            timeout=15,
        )
        r.raise_for_status()
        projects = r.json()
        for project in projects:
            custom = project.get('custom') or {}
            # Try common field names that might store the gateway DIN
            for key in ('tesla_gateway_din', 'gateway_din', 'tesla_din', 'gateway_serial',
                        'tesla_gateway', 'powerwall_din'):
                candidate = custom.get(key)
                if candidate:
                    din = str(candidate).strip()
                    log.info(f'[tesla] Found gateway DIN {din!r} for {customer_address!r}')
                    break
            if din:
                break
    except Exception as e:
        log.warning(f'[tesla] Coperniq address lookup failed: {e}')

    if not din:
        log.warning(
            f'[tesla] No gateway DIN found for {customer_address!r}. '
            'The PowerHub API requires a gateway DIN to look up site data. '
            'Store the DIN in a Coperniq custom field (e.g. tesla_gateway_din) to enable this lookup.'
        )
        return None

    # Step 2 — Look up the site by DIN
    try:
        r = requests.get(
            f'{TESLA_ASSET_BASE}/sites',
            params={'din': din},
            headers=headers,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json().get('data', [])
        if not data:
            log.warning(f'[tesla] No site found for DIN {din!r} (address: {customer_address!r})')
            return None

        site = data[0]
        log.info(f'[tesla] Site found for DIN {din!r}: {site}')
        return site
    except Exception as e:
        log.error(f'[tesla] Site lookup failed for DIN {din!r}: {e}')
        return None


# ─── Browser tasks ────────────────────────────────────────────────────────────

def run_browser_tasks(
    cc_project_id: str,
    customer_name: str,
    customer_address: str,
    bom_files: list,
    cad_file,
) -> int:
    """Run all Playwright tasks (CC PDF export, Lux upload). Returns count of files uploaded."""
    import asyncio
    from install_browser import export_cc_checklist_pdf, upload_to_lux_portal

    async def _run():
        pdf = await export_cc_checklist_pdf(cc_project_id)

        lux_files = []
        if pdf:
            lux_files.append(('install_checklist.pdf', pdf))
        if cad_file:
            lux_files.append(cad_file)
        for name, data in bom_files:
            lux_files.append((name, data))

        # Tesla commissioning data is retrieved via API (get_tesla_commissioning_data),
        # not via browser — no Tesla file to add here

        if lux_files:
            await upload_to_lux_portal(customer_name, lux_files)
        return len(lux_files)

    return asyncio.run(_run())


# ─── Orchestration ────────────────────────────────────────────────────────────

def process_install(install: dict) -> bool:
    """Run full post-install workflow for one completed solar install.

    Returns True when the full workflow completes successfully.
    Returns False when processing should be retried next poll
    (Company Cam project not found, or checklist not yet complete).
    """
    project_id = install.get('id')
    customer_name = install.get('title') or 'Unknown'

    log.info(f'--- Processing install: {customer_name} (project {project_id}) ---')

    # Build address string for Tesla lookup
    address_raw = install.get('address') or {}
    if isinstance(address_raw, list):
        address_str = address_raw[0] if address_raw else ''
    elif isinstance(address_raw, dict):
        address_str = ', '.join(filter(None, [
            address_raw.get('street'),
            address_raw.get('city'),
            address_raw.get('state'),
            address_raw.get('zip'),
        ]))
    else:
        address_str = str(address_raw)

    # 1. Check Company Cam checklist
    cc_project = find_company_cam_project(customer_name)
    if not cc_project:
        log.warning(f'{customer_name}: no Company Cam project found — skipping')
        return False
    cc_project_id = str(cc_project.get('id', ''))

    if not is_install_checklist_complete(cc_project_id):
        log.info(f'{customer_name}: install checklist not complete — will retry next poll')
        return False

    # 2. Get install photos
    photos = get_install_photos(cc_project_id)

    # 3. Complete Coperniq install WO, form, and visit
    try:
        complete_install_coperniq(project_id)
    except Exception:
        log.exception(f'complete_install_coperniq failed for {customer_name}')

    # 4. Send Slack to #vero with photos
    try:
        send_install_slack(install, photos)
    except Exception:
        log.exception(f'send_install_slack failed for {customer_name}')

    # 5. Send customer SMS
    try:
        send_customer_sms(project_id, customer_name)
    except Exception:
        log.exception(f'send_customer_sms failed for {customer_name}')

    # 6. Download BOM from Gmail + CAD from Coperniq
    bom_files = []
    try:
        bom_files = download_bom_from_gmail(customer_name)
    except Exception:
        log.exception(f'download_bom_from_gmail failed for {customer_name}')

    cad_file = None
    try:
        cad_file = download_cad_from_coperniq(project_id)
    except Exception:
        log.exception(f'download_cad_from_coperniq failed for {customer_name}')

    # 7. Browser tasks: export CC checklist PDF, upload all docs to Lux
    try:
        uploaded_count = run_browser_tasks(cc_project_id, customer_name, address_str, bom_files, cad_file)
        log.info(f'Browser tasks complete: {uploaded_count} file(s) uploaded to Lux')
    except Exception:
        log.exception(f'Browser tasks failed for {customer_name}')

    # 8. Email Kathy that M2 was submitted
    try:
        send_m2_email_kathy(customer_name)
    except Exception:
        log.exception(f'send_m2_email_kathy failed for {customer_name}')

    # 9. Start M2 in Coperniq
    try:
        start_m2_coperniq(project_id, customer_name)
    except Exception:
        log.exception(f'start_m2_coperniq failed for {customer_name}')

    log.info(f'--- Completed full install workflow for {customer_name} ---')
    return True


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
                try:
                    did_process = process_install(install)
                    if did_process:
                        processed.add(str(project_id))
                        save_processed(processed)
                except Exception:
                    log.exception(f'Failed to process install for project {project_id}')
        except Exception:
            log.exception('Unexpected error — will retry on next poll.')
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
