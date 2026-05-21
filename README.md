# Vero Power — Email Automations

Python scripts running on the office Mac mini that monitor Gmail and automatically update Coperniq, replacing all Zapier workflows.

## Automations

### `ntp_automation.py` — NTP Approval
Watches for emails with subject `Vero LLC NTP Approval: [Customer Name]` from Lux Financial.

**Actions:**
1. Finds the customer's project in Coperniq
2. Finds or creates the Notice to Proceed work order (under NTP phase)
3. Checks off all WO checklist items
4. Finds or creates the NTP form
5. Sets Finance Status → NTP Approved, Stipulations → NA, Finance Provider → Lux Financial
6. Completes the form and work order

---

### `m2_automation.py` — M2 Approval
Watches for emails with subject `Vero LLC M2 Approval: [Customer Name]` from Lux Financial.

**Actions:**
1. Finds the customer's project in Coperniq
2. Fills M2 form fields
3. Completes the work order
4. Leaves a note on the project
5. Replies "Thank you!" to Lux Financial

---

### `ntp_stip_automation.py` — NTP Stipulation
Watches for emails with subject `Vero NTP Stipulation: [Customer Name]` from Lux Financial.

**Actions:**
1. Skips if project is already NTP Approved, M2 Approved, M2 Submitted, CANCELLED, or ON_HOLD
2. Starts the NTP phase in Coperniq if not already in progress
3. Finds or creates the NTP work order (re-checks after phase start to avoid duplicates — Coperniq hides WOs in NOT_STARTED phases)
4. Updates NTP form: Finance Status → Pending Stipulation, sets Stipulations dropdown
5. Sets NTP work order to WAITING
6. Leaves a note tagging Sam LeSueur
7. Notifies the rep — Slack message to their `-ops` channel if it exists, otherwise emails the rep directly
8. Replies to Lux: "Hi Kathy, Thank you for the heads up — we are on it!"

---

### `install_automation.py` — Install Completion
Watches Coperniq every 30 minutes for solar installs completed today (VERO SOLAR INSTALLER CHECKLIST done in Company Cam).

**Actions:**
1. Finds today's Solar Installation projects in Coperniq
2. Checks Company Cam for a completed VERO SOLAR INSTALLER CHECKLIST
3. Completes the install work order, form, and field visit in Coperniq
4. Sends Slack to #vero with all install photos in one grouped message, tagging setter and closer
5. Sends customer a follow-up SMS via Twilio with referral ask
6. Downloads BOM from Gmail ("Customer Solar Materials" email attachment) and CAD/planset from Coperniq
7. Downloads the 25 most recent Company Cam project photos and creates a multi-page PDF
8. Generates a Tesla PowerHub commissioning screenshot (via Tesla GridLogic API + HTML renderer) showing customer address, Non-Export Mode, battery specs
9. Uploads all 4 docs to Lux Financial portal via Playwright: Installation Photos, CAD/Plan Set, Bill of Materials, Commissioning Screen Shot
10. Emails Kathy at Lux that M2 was submitted
11. Creates M2 work order + form in Coperniq, sets Finance Status → M2 Submitted, WO → WAITING

**install_browser.py** handles all Playwright/browser automation:
- `export_cc_checklist_pdf(cc_project_id)` — downloads CC photos via API, creates PDF with Pillow (no browser login needed)
- `screenshot_tesla_commissioning(...)` — generates commissioning report PNG using Tesla GridLogic API + HTML rendering (no browser login needed)
- `upload_to_lux_portal(customer_name, files)` — logs into Lux portal using persistent Chrome profile, finds job, uploads files to correct sections

**One-time setup required (run once on Mac mini):**
```bash
python3 create_lux_session.py   # Set up Lux portal Chrome session (Google OAuth + 2FA)
```
After running, `lux_browser_profile/` is saved and reused on every run — no re-login needed.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

Create a `.env` file in this folder:

```
GMAIL_ADDRESS=sam@veropwr.com
GMAIL_APP_PASSWORD=your_app_password
COPERNIQ_API_KEY=your_api_key
SLACK_BOT_TOKEN=your_slack_bot_token
```

### 3. Run a script

```bash
python ntp_automation.py
python m2_automation.py
python ntp_stip_automation.py
```

Each script polls Gmail every 2 minutes and runs indefinitely. All network calls (IMAP and Coperniq/Slack API) have a 30-second timeout so a hung connection won't freeze the process.

---

## Running as background services (launchd)

All automations run as launchd daemons on the Mac mini so they survive reboots and auto-restart on failure.

Plist files are in `~/Library/LaunchAgents/`. To restart a daemon after a code change:

```bash
# Kill the process — launchd will auto-restart it
pkill -f ntp_automation.py
pkill -f m2_automation.py
pkill -f ntp_stip_automation.py
pkill -f install_automation.py
```

> **Note:** `processed_stip_emails.json` is loaded into memory at startup. Restart the daemon after editing it manually.

---

## Environment variables (`.env`)

```
GMAIL_ADDRESS=sam@veropwr.com
GMAIL_APP_PASSWORD=...
COPERNIQ_API_KEY=...
COPERNIQ_BEARER_TOKEN=...
SLACK_BOT_TOKEN=...
LUX_GOOGLE_PASSWORD=...
TESLA_PASSWORD=...
COMPANY_CAM_API_KEY=...
KATHY_EMAIL=kathy.treanor@luxfinancial.io
TESLA_CLIENT_ID=...
TESLA_CLIENT_SECRET=...
TESLA_GROUP_ID=...
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+18324302030
```

---

## Files

| File | Description |
|------|-------------|
| `ntp_automation.py` | NTP Approval automation |
| `m2_automation.py` | M2 Approval automation |
| `ntp_stip_automation.py` | NTP Stipulation automation |
| `requirements.txt` | Python dependencies |
| `.env` | Credentials — **not committed** |
| `processed_emails.json` | Tracks processed NTP approval emails — **not committed** |
| `processed_m2_emails.json` | Tracks processed M2 approval emails — **not committed** |
| `processed_stip_emails.json` | Tracks processed stipulation emails — **not committed** |
| `ntp_automation.log` | NTP automation log output |
| `m2_automation.log` | M2 automation log output |
| `ntp_stip_automation.log` | Stipulation automation log output |
| `install_automation.py` | Install Completion automation (main orchestrator) |
| `install_browser.py` | Playwright + API browser tasks: CC photos PDF, Tesla commissioning screenshot, Lux upload |
| `create_lux_session.py` | One-time setup: Lux portal Google OAuth + 2FA using persistent Chrome profile |
| `create_tesla_session.py` | One-time setup: Tesla PowerHub browser login (not needed — API used instead) |
| `test_jondrea.py` | Test runner: runs full install pipeline on Jondrea Freeman |
| `processed_installs.json` | Tracks processed install project IDs — **not committed** |
| `install_automation.log` | Install automation log output |
| `lux_session.json` | Saved Lux portal session — **not committed** |
| `lux_browser_profile/` | Persistent Chrome profile for Lux portal — **not committed** |
| `.tesla_browser_profile/` | Persistent Chrome profile for Tesla PowerHub — **not committed** |
