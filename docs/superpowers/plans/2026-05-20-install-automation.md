# Install Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a polling daemon that detects completed solar installs each day and runs the full post-install workflow: complete Coperniq records, Slack team, text customer, collect docs, upload to Lux portal, kick off M2.

**Architecture:** `install_automation.py` handles the polling loop and all API calls (matching the pattern of existing automations). `install_browser.py` handles all Playwright browser automation (Company Cam PDF export, Tesla screenshot, Lux portal upload) as a separate module since it's async/browser-based. Polls every 30 minutes, skips already-processed jobs, retries incomplete checklists next cycle.

**Tech Stack:** Python 3, requests, playwright (Python async), imaplib, smtplib, python-dotenv, logging

---

## File Structure

| File | Purpose |
|------|---------|
| `install_automation.py` | Main polling loop + all Coperniq/CompanyCam/Slack/Gmail API calls |
| `install_browser.py` | All Playwright automation (CC PDF, Tesla screenshot, Lux upload) |
| `processed_installs.json` | Tracks processed job IDs to prevent reprocessing |
| `install_automation.log` | Log output |
| `.env` | Add COMPANY_CAM_API_KEY, LUX_GOOGLE_PASSWORD, TESLA_PASSWORD |
| `lux_session.json` | Playwright saved session for Lux (Google OAuth — created once, reused) |

---

## Task 1: Install Dependencies + Environment Setup

**Files:**
- Modify: `requirements.txt`
- Modify: `.env`

- [ ] **Step 1: Add playwright to requirements.txt**

```
requests
google-api-python-client
google-auth-httplib2
google-auth-oauthlib
python-dotenv
playwright
```

- [ ] **Step 2: Install dependencies and playwright browsers**

```bash
pip install -r requirements.txt
playwright install chromium
```

Expected: Chromium browser downloads successfully.

- [ ] **Step 3: Add new credentials to .env**

Open `/Users/samlesueur/vero-power/.env` and add:
```
COMPANY_CAM_API_KEY=<get from Company Cam → Resources → Integrations → API>
LUX_GOOGLE_PASSWORD=Firstblood84
TESLA_PASSWORD=Firstblood84
KATHY_EMAIL=<get from existing Lux email threads — look for underwriting@ or kathy@ address>
```

- [ ] **Step 4: Create empty processed_installs.json**

```bash
echo "[]" > /Users/samlesueur/vero-power/processed_installs.json
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt processed_installs.json
git commit -m "Add playwright dependency and install tracking file for install automation"
```

---

## Task 2: Discover Coperniq Calendar API

**Files:**
- Create: `install_automation.py` (skeleton only this task)

- [ ] **Step 1: Create install_automation.py with config and discovery helper**

```python
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
```

- [ ] **Step 2: Discover the Coperniq visits/calendar endpoint**

Run this in a Python shell to find today's Solar Installation WOs:

```python
import requests, os
from dotenv import load_dotenv
load_dotenv('/Users/samlesueur/vero-power/.env')
API_KEY = os.environ['COPERNIQ_API_KEY']
BASE = 'https://api.coperniq.io/v1'
H = {'x-api-key': API_KEY}

# Try visits endpoint
r = requests.get(f'{BASE}/visits', params={'startDate': '2026-05-20', 'endDate': '2026-05-20'}, headers=H)
print(r.status_code, r.text[:500])
```

- [ ] **Step 3: If /visits doesn't work, try work-orders with date filter**

```python
from datetime import date
today = date.today().isoformat()
r = requests.get(f'{BASE}/work-orders', params={'startDate': today, 'endDate': today, 'type': 'installation'}, headers=H)
print(r.status_code, r.text[:1000])

# Also try scheduler endpoint
r2 = requests.get(f'{BASE}/scheduler/events', params={'date': today}, headers=H)
print(r2.status_code, r2.text[:500])
```

- [ ] **Step 4: Implement get_todays_installs() using the working endpoint**

Replace `# ENDPOINT_TBD` with the working endpoint discovered above:

```python
def get_todays_installs() -> list[dict]:
    today = _today_date()
    r = requests.get(
        f'{COPERNIQ_BASE}/visits',   # replace with working endpoint
        params={'startDate': today, 'endDate': today},
        headers=COP_GET,
    )
    r.raise_for_status()
    data = r.json()
    rows = data if isinstance(data, list) else data.get('rows', [])
    installs = [
        row for row in rows
        if 'solar installation' in (row.get('title') or row.get('workOrderTitle') or '').lower()
        or row.get('type', '').lower() in ('install', 'installation')
    ]
    log.info(f'Found {len(installs)} installs scheduled for {today}')
    return installs
```

- [ ] **Step 5: Verify it returns today's installs**

```python
# Run in Python shell
import subprocess
result = subprocess.run(['python3', '-c', 
    'import sys; sys.path.insert(0, "."); from install_automation import get_todays_installs; print(get_todays_installs())'],
    cwd='/Users/samlesueur/vero-power', capture_output=True, text=True)
print(result.stdout)
print(result.stderr)
```

Expected: list of dicts with customer names matching today's calendar.

- [ ] **Step 6: Commit**

```bash
git add install_automation.py
git commit -m "Add install automation skeleton and Coperniq calendar query"
```

---

## Task 3: Company Cam — Check Checklist Completion

**Files:**
- Modify: `install_automation.py`

- [ ] **Step 1: Discover Company Cam project search endpoint**

```python
import requests, os
from dotenv import load_dotenv
load_dotenv('/Users/samlesueur/vero-power/.env')
CC_KEY = os.environ['COMPANY_CAM_API_KEY']
H = {'Authorization': f'Bearer {CC_KEY}', 'Accept': 'application/json'}

# Search for a project by name (use a real customer name from today or recent install)
r = requests.get('https://api.companycam.com/v2/projects', 
    params={'search': 'Jesse Jackson'}, headers=H)
print(r.status_code, r.json())
```

- [ ] **Step 2: Discover Company Cam checklists endpoint**

```python
# Use the project ID from the previous step
project_id = '100288461'  # Jesse Jackson's project from the screenshots
r = requests.get(f'https://api.companycam.com/v2/projects/{project_id}/checklists', headers=H)
print(r.status_code, r.text[:2000])
```

- [ ] **Step 3: Implement find_company_cam_project()**

```python
def find_company_cam_project(customer_name: str) -> dict | None:
    last_name = customer_name.split()[-1]
    r = requests.get(
        f'{COMPANY_CAM_BASE}/projects',
        params={'search': customer_name},
        headers=CC_GET,
    )
    r.raise_for_status()
    data = r.json()
    projects = data if isinstance(data, list) else data.get('projects', [])
    if not projects:
        # Try last name only
        r2 = requests.get(f'{COMPANY_CAM_BASE}/projects', params={'search': last_name}, headers=CC_GET)
        r2.raise_for_status()
        data2 = r2.json()
        projects = data2 if isinstance(data2, list) else data2.get('projects', [])
    if not projects:
        log.warning(f'No Company Cam project found for {customer_name}')
        return None
    log.info(f'Company Cam project found: {projects[0].get("id")} — {projects[0].get("name")}')
    return projects[0]
```

- [ ] **Step 4: Implement is_install_checklist_complete()**

```python
def is_install_checklist_complete(cc_project_id: str) -> bool:
    r = requests.get(
        f'{COMPANY_CAM_BASE}/projects/{cc_project_id}/checklists',
        headers=CC_GET,
    )
    r.raise_for_status()
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
```

- [ ] **Step 5: Verify with a known completed project**

```python
# Run in Python shell (use Jesse Jackson project ID 100288461)
from install_automation import find_company_cam_project, is_install_checklist_complete
proj = find_company_cam_project('Jesse Jackson')
print(proj)
print(is_install_checklist_complete(proj['id']))
```

Expected: `True` for Jesse Jackson (11/11 complete).

- [ ] **Step 6: Commit**

```bash
git add install_automation.py
git commit -m "Add Company Cam project lookup and checklist completion check"
```

---

## Task 4: Company Cam — Download Install Photos

**Files:**
- Modify: `install_automation.py`

- [ ] **Step 1: Discover photo structure on a checklist item**

```python
# Get the specific checklist details to see photo structure
cc_project_id = '100288461'
r = requests.get(f'https://api.companycam.com/v2/projects/{cc_project_id}/checklists', headers=H)
checklists = r.json()
# Find VERO SOLAR INSTALLER CHECKLIST
for c in checklists:
    if 'VERO SOLAR INSTALLER' in (c.get('name') or '').upper():
        checklist_id = c['id']
        break

# Get checklist items with photos
r2 = requests.get(f'https://api.companycam.com/v2/checklists/{checklist_id}', headers=H)
print(r2.text[:3000])
```

- [ ] **Step 2: Implement get_install_photos()**

```python
def get_install_photos(cc_project_id: str) -> list[bytes]:
    r = requests.get(f'{COMPANY_CAM_BASE}/projects/{cc_project_id}/checklists', headers=CC_GET)
    r.raise_for_status()
    checklists = r.json() if isinstance(r.json(), list) else r.json().get('checklists', [])
    
    checklist_id = None
    for c in checklists:
        if 'VERO SOLAR INSTALLER CHECKLIST' in (c.get('name') or '').upper():
            checklist_id = c['id']
            break
    if not checklist_id:
        return []

    r2 = requests.get(f'{COMPANY_CAM_BASE}/checklists/{checklist_id}', headers=CC_GET)
    r2.raise_for_status()
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

    log.info(f'Found {len(photo_urls)} install/battery photos')
    photos_bytes = []
    for url in photo_urls:
        resp = requests.get(url)
        if resp.status_code == 200:
            photos_bytes.append(resp.content)
    return photos_bytes
```

- [ ] **Step 3: Verify photos download correctly**

```python
from install_automation import get_install_photos
photos = get_install_photos('100288461')
print(f'Downloaded {len(photos)} photos, sizes: {[len(p) for p in photos]}')
```

Expected: list of byte strings, each > 10000 bytes (real photo).

- [ ] **Step 4: Commit**

```bash
git add install_automation.py
git commit -m "Add Company Cam photo download for panel and battery checklist items"
```

---

## Task 5: Complete Coperniq Install Work Order + Form

**Files:**
- Modify: `install_automation.py`

- [ ] **Step 1: Discover install WO and form names on a real project**

```python
import requests, os
from dotenv import load_dotenv
load_dotenv('/Users/samlesueur/vero-power/.env')
H = {'x-api-key': os.environ['COPERNIQ_API_KEY']}
BASE = 'https://api.coperniq.io/v1'

# Use a known recently-installed project ID from Coperniq
# Find by searching for a customer who had an install today
project_id = None  # Fill in from get_todays_installs() result

r = requests.get(f'{BASE}/projects/{project_id}/work-orders', headers=H)
wos = r.json()
print([{'id': w['id'], 'title': w.get('title'), 'status': w.get('status')} for w in wos])

r2 = requests.get(f'{BASE}/projects/{project_id}/forms', headers=H)
forms = r2.json()
print([{'id': f['id'], 'name': f.get('name'), 'status': f.get('status')} for f in forms])
```

- [ ] **Step 2: Discover visit check-off endpoint**

```python
# Check if there are visits on the project
r = requests.get(f'{BASE}/projects/{project_id}/visits', headers=H)
print(r.status_code, r.text[:1000])

# Or it may be on the work order itself (isField visits)
field_wos = [w for w in wos if w.get('isField')]
print('Field WOs (visits):', field_wos)
```

- [ ] **Step 3: Implement complete_install_coperniq()**

```python
def complete_install_coperniq(project_id: int):
    today = _today_et()

    # 1. Find install work order (title contains 'install' but not 'site survey')
    wos = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}/work-orders', headers=COP_GET).json()
    install_wo = next(
        (w for w in wos
         if not w.get('isArchived')
         and 'install' in (w.get('title') or '').lower()
         and 'site survey' not in (w.get('title') or '').lower()
         and w.get('status') != 'COMPLETED'),
        None,
    )
    if install_wo:
        checklist = [{'id': item['id'], 'isCompleted': True} for item in install_wo.get('checklist', [])]
        if checklist:
            requests.patch(
                f'{COPERNIQ_BASE}/projects/{project_id}/work-orders/{install_wo["id"]}',
                headers=COP_POST, json={'checklist': checklist},
            )
        requests.patch(
            f'{COPERNIQ_BASE}/projects/{project_id}/work-orders/{install_wo["id"]}',
            headers=COP_POST, json={'status': 'COMPLETED'},
        )
        log.info(f'Install WO {install_wo["id"]} completed')

    # 2. Find and complete install form
    forms = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}/forms', headers=COP_GET).json()
    install_form_stub = next(
        (f for f in forms
         if not f.get('isArchived')
         and 'install' in (f.get('name') or '').lower()
         and 'site survey' not in (f.get('name') or '').lower()),
        None,
    )
    if install_form_stub:
        full_form = requests.get(f'{COPERNIQ_BASE}/forms/{install_form_stub["id"]}', headers=COP_GET).json()
        all_props = []
        for layout in full_form.get('formLayouts', []):
            for prop in layout.get('properties', []):
                all_props.append(prop)
                for field in prop.get('fields', []):
                    all_props.append(field)
        field_map = {p['name']: p for p in all_props if 'name' in p}

        date_fields = ['Install Date', 'Installation Date', 'Completed Date', 'Install Completed Date']
        fields = [
            {'columnId': field_map[name]['columnId'], 'value': today}
            for name in date_fields if name in field_map
        ]
        if fields:
            requests.patch(f'{COPERNIQ_BASE}/forms/{install_form_stub["id"]}', headers=COP_POST, json={'fields': fields})
        requests.patch(f'{COPERNIQ_BASE}/forms/{install_form_stub["id"]}', headers=COP_POST, json={'status': 'COMPLETED'})
        log.info(f'Install form {install_form_stub["id"]} completed')

    # 3. Check off visit (field work order / visit)
    visit_wo = next(
        (w for w in wos if w.get('isField') and w.get('status') != 'COMPLETED'),
        None,
    )
    if visit_wo:
        requests.patch(
            f'{COPERNIQ_BASE}/projects/{project_id}/work-orders/{visit_wo["id"]}',
            headers=COP_POST, json={'status': 'COMPLETED'},
        )
        log.info(f'Visit WO {visit_wo["id"]} checked off')
```

- [ ] **Step 4: Test on a real project from today's installs**

```python
from install_automation import get_todays_installs, complete_install_coperniq
installs = get_todays_installs()
if installs:
    project_id = installs[0]['projectId']  # adjust key based on actual API response
    complete_install_coperniq(project_id)
```

Verify in Coperniq UI that the install WO and form are completed and visit is checked off.

- [ ] **Step 5: Commit**

```bash
git add install_automation.py
git commit -m "Add Coperniq install WO, form, and visit completion"
```

---

## Task 6: Send Slack Notification with Photos

**Files:**
- Modify: `install_automation.py`

- [ ] **Step 1: Discover setter/closer/system size fields on Coperniq project**

```python
import requests, os
from dotenv import load_dotenv
load_dotenv('/Users/samlesueur/vero-power/.env')
H = {'x-api-key': os.environ['COPERNIQ_API_KEY']}
BASE = 'https://api.coperniq.io/v1'

project_id = None  # use a real project ID
r = requests.get(f'{BASE}/projects/{project_id}', headers=H)
proj = r.json()
print('custom fields:', proj.get('custom'))
print('system size:', proj.get('systemSize'), proj.get('size'))
print('address:', proj.get('address'))
```

- [ ] **Step 2: Implement send_install_slack()**

Note: Uses `files.upload` Slack API to send photos. Photos are sent as files attached to the message.

```python
VERO_SLACK_CHANNEL = 'C...'  # #vero channel ID — get from: requests.get('https://slack.com/api/conversations.list', headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}'}, params={'types': 'public_channel,private_channel'}).json()

def _get_vero_channel_id() -> str:
    r = requests.get(
        'https://slack.com/api/conversations.list',
        headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}'},
        params={'types': 'public_channel,private_channel', 'limit': 200},
    )
    for ch in r.json().get('channels', []):
        if ch['name'] == 'vero':
            return ch['id']
    raise ValueError('Could not find #vero channel')


def _slack_user_id_from_email(email: str) -> str | None:
    r = requests.get(
        'https://slack.com/api/users.lookupByEmail',
        params={'email': email},
        headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}'},
    )
    data = r.json()
    return data['user']['id'] if data.get('ok') else None


def send_install_slack(project: dict, photos: list[bytes]):
    channel_id = _get_vero_channel_id()
    customer_name = project.get('title') or project.get('name') or 'Unknown'
    custom = project.get('custom') or {}
    
    setter_email = custom.get('sales_setter_email') or custom.get('setter_email') or ''
    closer_email = custom.get('sales_closer_email') or custom.get('closer_email') or ''
    setter_name  = custom.get('sales_setter_name') or custom.get('setter_name') or 'Setter'
    closer_name  = custom.get('sales_closer_name') or custom.get('closer_name') or 'Closer'

    setter_id = _slack_user_id_from_email(setter_email) if setter_email else None
    closer_id = _slack_user_id_from_email(closer_email) if closer_email else None
    setter_tag = f'<@{setter_id}>' if setter_id else setter_name
    closer_tag = f'<@{closer_id}>' if closer_id else closer_name

    system_size = project.get('systemSize') or project.get('size') or ''
    has_battery = any(
        kw in str(project).lower() for kw in ['battery', 'storage', 'powerwall']
    )
    size_str = f'{system_size}kW{"+ battery 🔋" if has_battery else ""}'

    address = project.get('address') or {}
    city = address.get('city') or address.get('street') or ''

    text = (
        f'Customer: {customer_name}\n\n'
        f'Setter: {setter_tag}\n'
        f'Closer: {closer_tag}\n\n'
        f'{size_str}\n\n'
        f'Area: {city}'
    )

    # Post text message first
    r = requests.post(
        'https://slack.com/api/chat.postMessage',
        headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}', 'Content-Type': 'application/json'},
        json={'channel': channel_id, 'text': text},
    )
    msg_ts = r.json().get('ts')

    # Upload photos as files in the same thread
    for i, photo_bytes in enumerate(photos):
        requests.post(
            'https://slack.com/api/files.upload',
            headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}'},
            data={'channels': channel_id, 'thread_ts': msg_ts, 'filename': f'install_photo_{i+1}.jpg'},
            files={'file': (f'photo_{i+1}.jpg', photo_bytes, 'image/jpeg')},
        )
    log.info(f'Slack sent to #vero for {customer_name} with {len(photos)} photos')
```

- [ ] **Step 3: Get the #vero channel ID and hardcode it**

```python
# Run once to get the channel ID
import requests, os
from dotenv import load_dotenv
load_dotenv('/Users/samlesueur/vero-power/.env')
r = requests.get('https://slack.com/api/conversations.list',
    headers={'Authorization': f'Bearer {os.environ["SLACK_BOT_TOKEN"]}'},
    params={'types': 'public_channel,private_channel', 'limit': 200})
for ch in r.json().get('channels', []):
    if ch['name'] == 'vero':
        print('VERO_CHANNEL_ID =', ch['id'])
```

Replace the `_get_vero_channel_id()` call with the hardcoded ID in `send_install_slack()`.

- [ ] **Step 4: Commit**

```bash
git add install_automation.py
git commit -m "Add Slack install notification with panel and battery photos"
```

---

## Task 7: Send Customer SMS via Coperniq

**Files:**
- Modify: `install_automation.py`

- [ ] **Step 1: Discover Coperniq SMS endpoint**

```python
import requests, os
from dotenv import load_dotenv
load_dotenv('/Users/samlesueur/vero-power/.env')
H = {'x-api-key': os.environ['COPERNIQ_API_KEY']}
BASE = 'https://api.coperniq.io/v1'

project_id = None  # real project ID

# Try communications endpoint
r = requests.get(f'{BASE}/projects/{project_id}/communications', headers=H)
print(r.status_code, r.text[:500])

# Try messages endpoint
r2 = requests.get(f'{BASE}/projects/{project_id}/messages', headers=H)
print(r2.status_code, r2.text[:500])
```

- [ ] **Step 2: Implement send_customer_sms()**

```python
def send_customer_sms(project_id: int, customer_name: str):
    first_name = customer_name.split()[0]
    message = (
        f"Hey {first_name}! This is Sam with Vero, just checking in to make sure the install "
        f"went well and to thank you for being great to work with. If you ever have any neighbors "
        f"or friends who are interested in the program, let us know so we can send ya a $500 referral bonus!"
    )
    # Try communications endpoint first, fall back to messages
    for endpoint in ['communications', 'messages']:
        r = requests.post(
            f'{COPERNIQ_BASE}/projects/{project_id}/{endpoint}',
            headers=COP_POST,
            json={'body': message, 'type': 'SMS'},
        )
        if r.status_code in (200, 201):
            log.info(f'SMS sent to {customer_name} via /projects/{project_id}/{endpoint}')
            return
        log.warning(f'{endpoint} returned {r.status_code}: {r.text[:200]}')
    log.error(f'Could not send SMS to {customer_name} — check endpoint')
```

- [ ] **Step 3: Test on a real project**

Test manually using a project ID — verify in Coperniq UI that the SMS shows in the communications panel.

- [ ] **Step 4: Commit**

```bash
git add install_automation.py
git commit -m "Add customer SMS via Coperniq after install completion"
```

---

## Task 8: Download BOM from Gmail + CAD from Coperniq

**Files:**
- Modify: `install_automation.py`

- [ ] **Step 1: Implement download_bom_from_gmail()**

```python
import imaplib
import email as emaillib
from email.header import decode_header

def download_bom_from_gmail(customer_name: str) -> list[tuple[str, bytes]]:
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PW)
    mail.select('inbox')

    search_name = customer_name.replace(' ', ' ')
    _, data = mail.search(None, f'SUBJECT "{customer_name} Solar Materials"')
    if not data[0].split():
        # Try last name only
        last = customer_name.split()[-1]
        _, data = mail.search(None, f'SUBJECT "{last} Solar Materials"')

    attachments = []
    for num in reversed(data[0].split()):  # most recent first
        _, raw = mail.fetch(num, '(RFC822)')
        msg = emaillib.message_from_bytes(raw[0][1])
        for part in msg.walk():
            if part.get_content_disposition() == 'attachment':
                filename = part.get_filename() or 'attachment.pdf'
                payload = part.get_payload(decode=True)
                if payload:
                    attachments.append((filename, payload))
        if attachments:
            break  # use most recent email's attachments

    mail.logout()
    log.info(f'Downloaded {len(attachments)} BOM attachments for {customer_name}')
    return attachments
```

- [ ] **Step 2: Discover Coperniq files/docs endpoint for CAD/planset**

```python
project_id = None  # real project
r = requests.get(f'{BASE}/projects/{project_id}/files', headers=H)
print(r.status_code, r.text[:2000])

r2 = requests.get(f'{BASE}/projects/{project_id}/documents', headers=H)
print(r2.status_code, r2.text[:2000])

r3 = requests.get(f'{BASE}/projects/{project_id}/docs', headers=H)
print(r3.status_code, r3.text[:500])
```

- [ ] **Step 3: Implement download_cad_from_coperniq()**

```python
def download_cad_from_coperniq(project_id: int) -> tuple[str, bytes] | None:
    for endpoint in ['files', 'documents', 'docs']:
        r = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}/{endpoint}', headers=COP_GET)
        if r.status_code != 200:
            continue
        files = r.json() if isinstance(r.json(), list) else r.json().get('rows', [])
        cad_keywords = ['cad', 'planset', 'plan set', 'engineering', 'design']
        cad_files = [
            f for f in files
            if any(kw in (f.get('name') or f.get('filename') or '').lower() for kw in cad_keywords)
        ]
        if cad_files:
            latest = sorted(cad_files, key=lambda f: f.get('createdAt') or f.get('created_at') or '', reverse=True)[0]
            url = latest.get('url') or latest.get('downloadUrl') or latest.get('file_url')
            if url:
                resp = requests.get(url)
                filename = latest.get('name') or latest.get('filename') or 'planset.pdf'
                log.info(f'Downloaded CAD/planset: {filename}')
                return (filename, resp.content)
    log.warning(f'No CAD/planset found for project {project_id}')
    return None
```

- [ ] **Step 4: Test both functions**

```python
from install_automation import download_bom_from_gmail, download_cad_from_coperniq
bom = download_bom_from_gmail('Jondrea Freeman')
print(f'BOM: {[(name, len(data)) for name, data in bom]}')

cad = download_cad_from_coperniq(793003)  # Jondrea Freeman's project ID
print(f'CAD: {cad[0] if cad else None}, {len(cad[1]) if cad else 0} bytes')
```

- [ ] **Step 5: Commit**

```bash
git add install_automation.py
git commit -m "Add BOM download from Gmail and CAD/planset download from Coperniq"
```

---

## Task 9: Playwright Setup + Company Cam PDF Export

**Files:**
- Create: `install_browser.py`

- [ ] **Step 1: Create install_browser.py with Company Cam PDF export**

```python
#!/usr/bin/env python3
"""
Browser automation for install automation.
Handles: Company Cam PDF export, Tesla PowerHub screenshot, Lux portal upload.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from playwright.async_api import async_playwright

log = logging.getLogger(__name__)

CC_EMAIL = os.environ.get('GMAIL_ADDRESS', '')
CC_PASSWORD = os.environ.get('COMPANY_CAM_PASSWORD', '')  # add to .env if CC uses separate login
LUX_GOOGLE_PASSWORD = os.environ.get('LUX_GOOGLE_PASSWORD', '')
TESLA_PASSWORD = os.environ.get('TESLA_PASSWORD', '')
GMAIL_ADDRESS = os.environ.get('GMAIL_ADDRESS', '')

DIR = Path(__file__).parent


async def export_cc_checklist_pdf(cc_project_id: str) -> bytes | None:
    """Log into Company Cam, export VERO SOLAR INSTALLER CHECKLIST as PDF."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Login
        await page.goto('https://app.companycam.com/users/sign_in')
        await page.fill('input[type="email"], input[name="email"]', GMAIL_ADDRESS)
        await page.fill('input[type="password"], input[name="password"]', CC_PASSWORD)
        await page.click('button[type="submit"], input[type="submit"]')
        await page.wait_for_load_state('networkidle')

        # Navigate to project checklists
        await page.goto(f'https://app.companycam.com/projects/{cc_project_id}/todos')
        await page.wait_for_load_state('networkidle')

        # Find VERO SOLAR INSTALLER CHECKLIST and click its "..." menu
        checklist_row = page.locator('text=VERO SOLAR INSTALLER CHECKLIST').first
        await checklist_row.wait_for()
        
        # Click the "..." button next to the completed checklist
        row_container = checklist_row.locator('xpath=ancestor::*[contains(@class, "checklist") or contains(@class, "row")][1]')
        await row_container.locator('button:has-text("..."), [aria-label="More options"], button[data-testid="menu"]').first.click()
        
        # Click Export to PDF
        await page.locator('text=Export to PDF').click()

        # Wait for download
        async with page.expect_download() as download_info:
            # The export may auto-trigger or need a confirm button
            try:
                await page.locator('text=Export to PDF').click(timeout=2000)
            except Exception:
                pass
        download = await download_info.value
        
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            await download.save_as(tmp.name)
            pdf_bytes = Path(tmp.name).read_bytes()

        await browser.close()
        log.info(f'Company Cam checklist PDF exported: {len(pdf_bytes)} bytes')
        return pdf_bytes
```

- [ ] **Step 2: Add Company Cam password to .env**

```bash
# Check if Company Cam uses same Google login or separate credentials
# Add to .env:
# COMPANY_CAM_PASSWORD=Firstblood84
```

- [ ] **Step 3: Test the PDF export**

```python
import asyncio
from dotenv import load_dotenv
load_dotenv('/Users/samlesueur/vero-power/.env')
from install_browser import export_cc_checklist_pdf

pdf = asyncio.run(export_cc_checklist_pdf('100288461'))  # Jesse Jackson
print(f'PDF size: {len(pdf)} bytes')
with open('/tmp/test_checklist.pdf', 'wb') as f:
    f.write(pdf)
print('Saved to /tmp/test_checklist.pdf — open to verify')
```

- [ ] **Step 4: Fix any selector issues found during testing**

Company Cam's DOM structure may differ from assumptions. Use Playwright's `page.pause()` in headed mode to inspect:

```python
# Temporarily change to headless=False and add pause for debugging:
# browser = await p.chromium.launch(headless=False)
# await page.pause()  # opens inspector
```

- [ ] **Step 5: Commit**

```bash
git add install_browser.py
git commit -m "Add Playwright Company Cam checklist PDF export"
```

---

## Task 10: Tesla PowerHub Screenshot

**Files:**
- Modify: `install_browser.py`

- [ ] **Step 1: Add Tesla screenshot function**

```python
async def screenshot_tesla_commissioning(customer_address: str) -> bytes | None:
    """Log into Tesla PowerHub, find job by address, screenshot commissioning page."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Login
        await page.goto('https://powerhub.energy.tesla.com/login?redirect_to=%2F')
        await page.wait_for_load_state('networkidle')
        await page.fill('input[type="email"], input[name="email"], #email', GMAIL_ADDRESS)
        await page.click('button[type="submit"], button:has-text("Next"), button:has-text("Continue")')
        await page.fill('input[type="password"], input[name="password"], #password', TESLA_PASSWORD)
        await page.click('button[type="submit"], button:has-text("Sign in"), button:has-text("Log in")')
        await page.wait_for_load_state('networkidle')

        # Search for job by address
        search = page.locator('input[placeholder*="search"], input[type="search"]').first
        if await search.count() > 0:
            await search.fill(customer_address)
            await page.keyboard.press('Enter')
            await page.wait_for_load_state('networkidle')
            await page.locator(f'text={customer_address.split(",")[0]}').first.click()
            await page.wait_for_load_state('networkidle')

        # Navigate to commissioning tab/section
        commissioning_link = page.locator('text=Commissioning, a:has-text("Commissioning")').first
        if await commissioning_link.count() > 0:
            await commissioning_link.click()
            await page.wait_for_load_state('networkidle')

        screenshot = await page.screenshot(full_page=True)
        await browser.close()
        log.info(f'Tesla commissioning screenshot: {len(screenshot)} bytes')
        return screenshot
```

- [ ] **Step 2: Test the Tesla screenshot**

```python
import asyncio
from dotenv import load_dotenv
load_dotenv('/Users/samlesueur/vero-power/.env')
from install_browser import screenshot_tesla_commissioning

screenshot = asyncio.run(screenshot_tesla_commissioning('1526 Island Grove Dr, Iowa Colony, TX 77583'))
with open('/tmp/tesla_commissioning.png', 'wb') as f:
    f.write(screenshot)
print('Saved to /tmp/tesla_commissioning.png — open to verify')
```

- [ ] **Step 3: Adjust selectors based on actual Tesla PowerHub DOM**

Run with `headless=False` to inspect the login flow and job search if step 2 fails. Tesla may use a different auth flow.

- [ ] **Step 4: Commit**

```bash
git add install_browser.py
git commit -m "Add Tesla PowerHub commissioning screenshot via Playwright"
```

---

## Task 11: Lux Portal Upload

**Files:**
- Modify: `install_browser.py`

- [ ] **Step 1: Create Lux Google OAuth session (run once manually)**

```python
# Run this ONCE interactively to save the Google OAuth session
# Then the automation reuses the saved session without re-authenticating

import asyncio
from playwright.async_api import async_playwright
from pathlib import Path
import os
from dotenv import load_dotenv
load_dotenv('/Users/samlesueur/vero-power/.env')

SESSION_FILE = '/Users/samlesueur/vero-power/lux_session.json'

async def create_lux_session():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # headed so you can see it
        context = await browser.new_context()
        page = await context.new_page()
        
        await page.goto('https://app.luxfinancial.io/installer/?partnerId=49937750-8974-491e-9d25-b1b2fe86f715&page=1')
        await page.wait_for_load_state('networkidle')
        
        # Click "Sign in with Google"
        await page.locator('text=Sign in with Google, button:has-text("Google")').first.click()
        
        # Google login
        await page.fill('input[type="email"]', os.environ['GMAIL_ADDRESS'])
        await page.click('button:has-text("Next")')
        await page.fill('input[type="password"]', os.environ['LUX_GOOGLE_PASSWORD'])
        await page.click('button:has-text("Next")')
        await page.wait_for_load_state('networkidle')
        
        # Save session state
        await context.storage_state(path=SESSION_FILE)
        print(f'Session saved to {SESSION_FILE}')
        await browser.close()

asyncio.run(create_lux_session())
```

Run this script once and verify `lux_session.json` is created.

- [ ] **Step 2: Add lux_session.json to .gitignore**

```bash
echo "lux_session.json" >> /Users/samlesueur/vero-power/.gitignore
```

- [ ] **Step 3: Inspect the Lux portal to understand job search + upload UI**

```python
# Run this to explore the Lux portal structure after logging in
import asyncio
from playwright.async_api import async_playwright

async def explore_lux():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(storage_state='/Users/samlesueur/vero-power/lux_session.json')
        page = await context.new_page()
        await page.goto('https://app.luxfinancial.io/installer/?partnerId=49937750-8974-491e-9d25-b1b2fe86f715&page=1')
        await page.wait_for_load_state('networkidle')
        await page.pause()  # opens Playwright inspector — explore the DOM

asyncio.run(explore_lux())
```

Note the selectors for: job search input, job list items, upload buttons/tabs, file input fields.

- [ ] **Step 4: Implement upload_to_lux_portal()**

```python
SESSION_FILE = DIR / 'lux_session.json'
LUX_URL = 'https://app.luxfinancial.io/installer/?partnerId=49937750-8974-491e-9d25-b1b2fe86f715&page=1'

async def upload_to_lux_portal(customer_name: str, files: list[tuple[str, bytes]]) -> bool:
    """
    Log into Lux portal using saved session, find job, upload all files.
    files: list of (filename, bytes) tuples
    """
    import tempfile

    if not Path(SESSION_FILE).exists():
        log.error('lux_session.json not found — run create_lux_session() first')
        return False

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=str(SESSION_FILE))
        page = await context.new_page()

        await page.goto(LUX_URL)
        await page.wait_for_load_state('networkidle')

        # Search for customer
        last_name = customer_name.split()[-1]
        search = page.locator('input[placeholder*="search" i], input[type="search"]').first
        await search.fill(last_name)
        await page.keyboard.press('Enter')
        await page.wait_for_load_state('networkidle')

        # Click into the job
        job_row = page.locator(f'text={last_name}').first
        await job_row.click()
        await page.wait_for_load_state('networkidle')

        # Write files to temp dir and upload
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = []
            for filename, data in files:
                path = Path(tmpdir) / filename
                path.write_bytes(data)
                paths.append(str(path))

            # Find upload input(s) — may need multiple for different doc types
            upload_inputs = page.locator('input[type="file"]')
            count = await upload_inputs.count()
            log.info(f'Found {count} file upload inputs on Lux portal')

            for path in paths:
                for i in range(count):
                    try:
                        await upload_inputs.nth(i).set_input_files(path)
                        await page.wait_for_timeout(1000)
                        break
                    except Exception:
                        continue

            # Look for and click Save/Submit/Upload button
            for btn_text in ['Save', 'Submit', 'Upload', 'Confirm']:
                btn = page.locator(f'button:has-text("{btn_text}")').first
                if await btn.count() > 0:
                    await btn.click()
                    await page.wait_for_load_state('networkidle')
                    break

        await browser.close()
        log.info(f'Uploaded {len(files)} files to Lux portal for {customer_name}')
        return True
```

- [ ] **Step 5: Test upload with real files**

```python
import asyncio
from dotenv import load_dotenv
load_dotenv('/Users/samlesueur/vero-power/.env')
from install_browser import upload_to_lux_portal

# Use test files
test_files = [('test.pdf', b'%PDF test content')]
result = asyncio.run(upload_to_lux_portal('Jondrea Freeman', test_files))
print('Upload result:', result)
```

Verify files appear in Lux portal UI.

- [ ] **Step 6: Commit**

```bash
git add install_browser.py .gitignore
git commit -m "Add Lux portal upload via Playwright with saved Google OAuth session"
```

---

## Task 12: Email Kathy + M2 Kickoff in Coperniq

**Files:**
- Modify: `install_automation.py`

- [ ] **Step 1: Implement send_m2_email_kathy()**

```python
import smtplib
from email.mime.text import MIMEText

def send_m2_email_kathy(customer_name: str):
    msg = MIMEText(
        f'Hi Kathy,\n\n'
        f'Please see that M2 has been submitted for {customer_name}. '
        f'All required documents have been uploaded to the Lux portal.\n\n'
        f'Thank you!'
    )
    msg['From'] = GMAIL_ADDRESS
    msg['To'] = KATHY_EMAIL
    msg['Subject'] = f'M2 Submitted — {customer_name}'
    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PW)
        server.send_message(msg)
    log.info(f'M2 email sent to Kathy for {customer_name}')
```

- [ ] **Step 2: Implement start_m2_coperniq() — reusing patterns from m2_automation.py**

```python
def start_m2_coperniq(project_id: int, customer_name: str):
    today = _today_et()

    # Find or create M2 work order
    wos = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}/work-orders', headers=COP_GET).json()
    m2_wo = next(
        (w for w in wos
         if not w.get('isArchived')
         and any(kw in (w.get('title') or '').lower() for kw in ['milestone 2', 'm2'])),
        None,
    )
    if not m2_wo:
        m2_wo = requests.post(
            f'{COPERNIQ_BASE}/projects/{project_id}/work-orders',
            headers=COP_POST,
            json={'templateId': 1907087},  # adjust if M2 has a different template ID — discover via API
        ).json()
        log.info(f'M2 work order created: {m2_wo.get("id")}')
    else:
        log.info(f'M2 work order found: {m2_wo.get("id")}')

    # Find or create M2 form
    forms = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}/forms', headers=COP_GET).json()
    m2_form_stub = next(
        (f for f in forms
         if not f.get('isArchived')
         and any(kw in (f.get('name') or '').lower() for kw in ['milestone 2', 'm2'])),
        None,
    )
    if not m2_form_stub:
        # Discover M2 form template ID
        m2_form_stub = requests.post(
            f'{COPERNIQ_BASE}/projects/{project_id}/forms',
            headers=COP_POST,
            json={'templateId': None},  # discover via: GET /v1/form-templates
        ).json()

    m2_form = requests.get(f'{COPERNIQ_BASE}/forms/{m2_form_stub["id"]}', headers=COP_GET).json()

    # Build field map
    all_props = []
    for layout in m2_form.get('formLayouts', []):
        for prop in layout.get('properties', []):
            all_props.append(prop)
            for field in prop.get('fields', []):
                all_props.append(field)
    field_map = {p['name']: p for p in all_props if 'name' in p}

    # Update form fields
    desired = {
        'Finance Status': 'M2 Submitted',
        'M2 Submitted Date': today,
        'M2 Completed Date': today,
    }
    fields = [
        {'columnId': field_map[name]['columnId'], 'value': value}
        for name, value in desired.items() if name in field_map
    ]
    if fields:
        requests.patch(f'{COPERNIQ_BASE}/forms/{m2_form_stub["id"]}', headers=COP_POST, json={'fields': fields})
    log.info('M2 form updated to M2 Submitted')

    # Set WO to WAITING
    requests.patch(
        f'{COPERNIQ_BASE}/projects/{project_id}/work-orders/{m2_wo["id"]}',
        headers=COP_POST,
        json={'status': 'WAITING'},
    )
    log.info('M2 WO set to WAITING')

    # Leave note
    requests.post(
        f'{COPERNIQ_BASE}/projects/{project_id}/notes',
        headers=COP_POST,
        json={'body': f'M2 submitted for {customer_name}. Documents uploaded to Lux portal. [Sam LeSueur|~id:14206]'},
    )
    log.info('M2 note left on project')
```

- [ ] **Step 3: Discover M2 form template ID**

```python
r = requests.get(f'{BASE}/form-templates', headers=H)
for t in r.json():
    if 'm2' in (t.get('name') or '').lower() or 'milestone 2' in (t.get('name') or '').lower():
        print('M2 form template:', t)
```

Replace `templateId: None` in `start_m2_coperniq()` with the real ID.

- [ ] **Step 4: Commit**

```bash
git add install_automation.py
git commit -m "Add M2 Coperniq kickoff (WO, form, WAITING, note) and Kathy email"
```

---

## Task 13: Wire Up Main Polling Loop

**Files:**
- Modify: `install_automation.py`

- [ ] **Step 1: Add sync wrapper for browser tasks**

```python
import asyncio

def run_browser_tasks(cc_project_id: str, customer_name: str, customer_address: str,
                      bom_files: list[tuple[str, bytes]], cad_file: tuple[str, bytes] | None) -> dict:
    """Run all Playwright tasks and return collected files."""
    from install_browser import export_cc_checklist_pdf, screenshot_tesla_commissioning, upload_to_lux_portal

    async def _run():
        pdf = await export_cc_checklist_pdf(cc_project_id)
        screenshot = await screenshot_tesla_commissioning(customer_address)
        
        lux_files = []
        if pdf:
            lux_files.append(('install_checklist.pdf', pdf))
        if cad_file:
            lux_files.append(cad_file)
        for name, data in bom_files:
            lux_files.append((name, data))
        if screenshot:
            lux_files.append(('tesla_commissioning.png', screenshot))

        await upload_to_lux_portal(customer_name, lux_files)
        return {'pdf': pdf, 'screenshot': screenshot, 'uploaded': len(lux_files)}

    return asyncio.run(_run())
```

- [ ] **Step 2: Implement process_install()**

```python
def process_install(install: dict):
    """Run full post-install workflow for one job."""
    # Extract fields — key names depend on actual Coperniq API response (adjust after Task 2)
    project_id   = install.get('projectId') or install.get('project', {}).get('id')
    customer_name = install.get('title') or install.get('customerName') or install.get('project', {}).get('title')
    
    log.info(f'Processing install: {customer_name} (project {project_id})')

    # Get full project details
    project = requests.get(f'{COPERNIQ_BASE}/projects/{project_id}', headers=COP_GET).json()
    address_obj = project.get('address') or {}
    address_str = f'{address_obj.get("street")}, {address_obj.get("city")}, {address_obj.get("state")} {address_obj.get("zip")}'

    # 1. Check Company Cam
    cc_project = find_company_cam_project(customer_name)
    if not cc_project:
        log.warning(f'No Company Cam project for {customer_name} — skipping')
        return
    if not is_install_checklist_complete(cc_project['id']):
        log.info(f'{customer_name}: install checklist not complete yet — will retry')
        return

    # 2. Get install photos
    photos = get_install_photos(cc_project['id'])

    # 3. Complete Coperniq install WO + form + visit
    complete_install_coperniq(project_id)

    # 4. Send Slack
    send_install_slack(project, photos)

    # 5. Send customer SMS
    send_customer_sms(project_id, customer_name)

    # 6. Download documents
    bom_files = download_bom_from_gmail(customer_name)
    cad_file = download_cad_from_coperniq(project_id)

    # 7. Browser tasks: PDF export, Tesla screenshot, Lux upload
    run_browser_tasks(cc_project['id'], customer_name, address_str, bom_files, cad_file)

    # 8. Email Kathy
    send_m2_email_kathy(customer_name)

    # 9. Start M2
    start_m2_coperniq(project_id, customer_name)

    log.info(f'Completed full install workflow for {customer_name}')
```

- [ ] **Step 3: Implement main polling loop**

```python
def main():
    log.info('Install automation started — polling every 30 minutes.')
    processed = load_processed()

    while True:
        try:
            log.info('Checking Coperniq for today\'s installs...')
            installs = get_todays_installs()
            for install in installs:
                job_key = str(install.get('id') or install.get('visitId') or install.get('workOrderId'))
                if job_key in processed:
                    continue
                try:
                    process_install(install)
                    processed.add(job_key)
                    save_processed(processed)
                except Exception:
                    log.exception(f'Failed to process install {job_key}')
        except Exception:
            log.exception('Unexpected error — will retry next poll.')
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Run end-to-end test on today's install**

```bash
cd /Users/samlesueur/vero-power
python install_automation.py
```

Watch the log output. Verify in Coperniq, Slack, and Lux portal that everything processed correctly.

- [ ] **Step 5: Commit**

```bash
git add install_automation.py
git commit -m "Wire up full install automation polling loop"
```

---

## Task 14: launchd Daemon + Documentation

**Files:**
- Create: `~/Library/LaunchAgents/com.vero.install-automation.plist`
- Create: `install_automation.md`

- [ ] **Step 1: Create launchd plist**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.vero.install-automation</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/samlesueur/vero-power/install_automation.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/samlesueur/vero-power</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/samlesueur/vero-power/install_automation.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/samlesueur/vero-power/install_automation.log</string>
</dict>
</plist>
```

- [ ] **Step 2: Load the daemon**

```bash
launchctl load ~/Library/LaunchAgents/com.vero.install-automation.plist
launchctl list | grep install
```

Expected: `com.vero.install-automation` appears in the list.

- [ ] **Step 3: Create install_automation.md documentation**

Write a markdown file covering: trigger, what it does step by step, how to restart, env vars required, files.

- [ ] **Step 4: Final commit + push**

```bash
git add install_automation.py install_browser.py install_automation.md
git commit -m "Add install automation, browser module, launchd daemon, and docs"
git push origin main
```

---

## Key Unknowns to Resolve During Implementation

These are discovered during the build — not blockers to starting:

| Unknown | How to discover |
|---------|----------------|
| Coperniq calendar/visits API endpoint | Task 2 Step 2-3 |
| Coperniq SMS endpoint | Task 7 Step 1 |
| Coperniq files/docs endpoint for CAD | Task 8 Step 2 |
| M2 form template ID | Task 12 Step 3 |
| Company Cam checklist item JSON structure | Task 4 Step 1 |
| Tesla PowerHub job search/navigation | Task 10 Step 2 |
| Lux portal upload UI structure | Task 11 Step 3 |
| Kathy's email address | Check existing Lux email thread in Gmail |
| Company Cam login method (Google or email/password) | Task 9 Step 2 |
