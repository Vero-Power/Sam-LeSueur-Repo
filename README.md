# Vero Power — Email & Install Automations

Python scripts running on the office Mac mini that monitor Gmail and Coperniq, automatically handling NTP approvals, NTP stipulations, M2 approvals, install completions, and M3 submissions. Replaces all Zapier workflows.

All automations run as launchd daemons and auto-restart on failure or reboot.

---

## Automations

### `ntp_automation.py` — NTP Approval
Watches for `Vero LLC NTP Approval: [Customer Name]` emails from Lux Financial.

**Actions:**
1. Finds the customer's project in Coperniq
2. Finds or creates the Notice to Proceed work order (template 1907087)
3. Checks off all WO checklist items and marks it COMPLETED
4. Finds or creates the NTP form (template 1191546)
5. Skips if Finance Status is already M2 Approved or M2 Submitted
6. Sets Finance Status → NTP Approved, Stipulations → NA, Finance Provider → Lux Financial
7. Marks the form COMPLETED and leaves a note

📄 See [ntp_automation.md](ntp_automation.md) for full details.

---

### `m2_automation.py` — M2 Approval
Watches for `Vero LLC M2 Approval: [Customer Name]` emails from Lux Financial.

**Actions:**
1. Finds the customer's project in Coperniq
2. Parses system size, product type, monthly payment, and payouts from the email body
3. Fills and completes the M2 form
4. Checks off all WO checklist items and marks the WO COMPLETED
5. Leaves a note and replies "Thank you!" to Lux

📄 See [m2_automation.md](m2_automation.md) for full details.

---

### `ntp_stip_automation.py` — NTP Stipulation
Watches for `Vero NTP Stipulation: [Customer Name]` emails from Lux Financial.

**Actions:**
1. Skips if project is already NTP Approved, M2 Approved, M2 Submitted, CANCELLED, or ON_HOLD
2. Starts the NTP phase in Coperniq if not already in progress (waits up to 60s)
3. Re-fetches WOs after phase start (Coperniq hides WOs in NOT_STARTED phases — prevents duplicate WO bug)
4. Finds or creates the NTP work order and form
5. Sets Finance Status → Pending Stipulation, sets Stipulations dropdown
6. Sets NTP WO to WAITING and leaves a note tagging Sam LeSueur
7. Notifies the rep — Slack to their `-ops` channel if found, otherwise emails the rep directly
8. Replies to Lux: "Hi Kathy, Thank you for the heads up — we are on it!"

📄 See [ntp_stip_automation.md](ntp_stip_automation.md) for full details.

---

### `install_automation.py` — Install Completion
Polls Coperniq every 30 minutes for solar installs completed today.

**Actions:**
1. Finds today's Solar Installation projects in Coperniq
2. Checks Company Cam for a completed VERO SOLAR INSTALLER CHECKLIST
3. Completes the install work order, form, and field visit in Coperniq
4. Sends Slack to #vero with all install photos in one grouped message, tagging setter and closer
5. Sends customer a follow-up SMS via Coperniq's built-in Communication API
6. Downloads BOM from Gmail (`[Customer] Solar Materials` email) and CAD/planset from Coperniq
7. Exports Company Cam install photos as a multi-page PDF (CC API + Pillow, no browser)
8. Takes a Tesla PowerHub commissioning screenshot (Tesla GridLogic API + HTML renderer, no browser login)
9. Uploads all 4 documents to Lux Financial portal via Playwright (persistent Chrome profile)
10. Emails Kathy at Lux that M2 was submitted
11. Creates M2 work order + form in Coperniq, sets Finance Status → M2 Submitted, WO → WAITING

📄 See [install_automation.md](install_automation.md) for full details.

---

### `m3_automation.py` — M3 Submission
Polls Coperniq every 30 minutes for projects where PTO has been granted (PTO Submitted + PTO Approved WOs both COMPLETED, M3 WO still WAITING).

**Actions:**
1. Takes a Tesla commissioning screenshot via Tesla GridLogic API
2. Downloads PTO Letter from Coperniq M3 form
3. Uploads both to Lux portal **Pending PTO** section (Proof of Commissionsing + PTO Letter)
4. Updates M3 form: Finance Status → M3 Submitted, sets submitted date and Finance Provider
5. Updates Commissioning form: status → Completed, sets complete date, marks form COMPLETED
6. Sets Commissioning WO → COMPLETED (patches each checklist item individually, then `status: COMPLETED`)
7. Emails Kathy and Mike Paris at Lux Financial
8. Leaves a note in Coperniq

📄 See [m3_automation.md](m3_automation.md) for full details.

---

## Helper Scripts

### `install_browser.py`
Playwright + API tasks shared by install and M3 automations:
- `export_cc_checklist_pdf(cc_project_id)` — CC API + Pillow, no browser needed (SYNC, do not await)
- `screenshot_tesla_commissioning(...)` — Tesla GridLogic API + HTML rendering, no browser login
- `upload_to_lux_portal(customer_name, files)` — persistent Chrome profile (`lux_browser_profile/`)

📄 See [install_browser.md](install_browser.md) for full details.

---

### `create_lux_session.py`
One-time setup: creates persistent Chrome profile for Lux portal. Run once on Mac mini — handles Google OAuth, iPhone push approval, and Lux 2FA automatically. Re-run if session expires.

📄 See [create_lux_session.md](create_lux_session.md) for full details.

---

### `find_m2_ntp_mismatch.py`
Utility script — scans all Coperniq projects and finds any where the NTP form still says "NTP Approved" but the M2 WO or form is already completed. Fixes mismatches by setting NTP form Finance Status → M2 Approved.

Run manually when needed:
```bash
python3 find_m2_ntp_mismatch.py
```

---

### `test_jondrea.py`
Test runner — runs the full install pipeline on Jondrea Freeman (Coperniq project 793003, Company Cam 99879909). Use to verify the install automation end-to-end.

### `test_seth_m3.py`
Test runner — runs the full M3 pipeline on Seth Riklin (Coperniq project 796539). Use to verify the M3 automation end-to-end.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

Create a `.env` file:

```
GMAIL_ADDRESS=sam@veropwr.com
GMAIL_APP_PASSWORD=your_app_password
COPERNIQ_API_KEY=your_api_key
SLACK_BOT_TOKEN=your_slack_bot_token
COMPANY_CAM_API_KEY=your_cc_api_key
LUX_GOOGLE_PASSWORD=your_google_password
TESLA_CLIENT_ID=your_tesla_client_id
TESLA_CLIENT_SECRET=your_tesla_client_secret
TESLA_GROUP_ID=your_tesla_group_id
KATHY_EMAIL=kathy.treanor@luxfinancial.io
```

### 3. One-time Lux portal setup

```bash
python3 create_lux_session.py
```

Logs into Lux portal via Google OAuth + Gmail 2FA and saves a persistent Chrome profile. Only needed once (re-run if session expires).

---

## Running as background services (launchd)

All automations run as launchd daemons in `~/Library/LaunchAgents/`. To restart after a code change:

```bash
pkill -f ntp_automation.py
pkill -f m2_automation.py
pkill -f ntp_stip_automation.py
pkill -f install_automation.py
pkill -f m3_automation.py
# launchd auto-restarts each one
```

To check all are running:
```bash
launchctl list | grep vero
```

> **Note:** `processed_stip_emails.json` is loaded at startup — restart the daemon after editing it manually.

---

## Files

| File | Description |
|------|-------------|
| `ntp_automation.py` | NTP Approval automation |
| `m2_automation.py` | M2 Approval automation |
| `ntp_stip_automation.py` | NTP Stipulation automation |
| `install_automation.py` | Install Completion automation (main orchestrator) |
| `m3_automation.py` | M3 Submission automation |
| `install_browser.py` | Playwright + API tasks: CC photos PDF, Tesla screenshot, Lux upload |
| `create_lux_session.py` | One-time setup: persistent Chrome profile for Lux portal |
| `create_tesla_session.py` | Not needed — Tesla uses API credentials, no browser login |
| `find_m2_ntp_mismatch.py` | Utility: finds and fixes projects with mismatched M2/NTP statuses |
| `test_jondrea.py` | Test runner: runs full install pipeline on Jondrea Freeman |
| `test_seth_m3.py` | Test runner: runs full M3 pipeline on Seth Riklin |
| `ntp_automation.md` | NTP automation docs |
| `m2_automation.md` | M2 automation docs |
| `ntp_stip_automation.md` | NTP stipulation automation docs |
| `install_automation.md` | Install automation docs |
| `m3_automation.md` | M3 automation docs |
| `install_browser.md` | install_browser.py docs |
| `create_lux_session.md` | create_lux_session.py docs |
| `requirements.txt` | Python dependencies |
| `.env` | Credentials — **not committed** |
| `processed_emails.json` | Tracks processed NTP approval emails — **not committed** |
| `processed_m2_emails.json` | Tracks processed M2 approval emails — **not committed** |
| `processed_stip_emails.json` | Tracks processed stipulation emails — **not committed** |
| `processed_installs.json` | Tracks processed install project IDs — **not committed** |
| `processed_m3_projects.json` | Tracks processed M3 project IDs — **not committed** |
| `lux_browser_profile/` | Persistent Chrome profile for Lux portal — **not committed** |

---

## Environment Variables

| Variable | Used By | Description |
|----------|---------|-------------|
| `GMAIL_ADDRESS` | All | Gmail address to monitor |
| `GMAIL_APP_PASSWORD` | All | Gmail app password |
| `COPERNIQ_API_KEY` | All | Coperniq API key |
| `SLACK_BOT_TOKEN` | ntp_stip, install | Slack bot token |
| `COMPANY_CAM_API_KEY` | install | Company Cam API key |
| `LUX_GOOGLE_PASSWORD` | install, m3 | Google password for Lux portal |
| `TESLA_CLIENT_ID` | install, m3 | Tesla GridLogic API client ID |
| `TESLA_CLIENT_SECRET` | install, m3 | Tesla GridLogic API client secret |
| `TESLA_GROUP_ID` | install, m3 | Tesla GridLogic Vero group ID |
| `KATHY_EMAIL` | install, m3 | Kathy Treanor's email at Lux Financial |
