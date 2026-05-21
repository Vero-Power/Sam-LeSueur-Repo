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
4. Sends Slack to #vero with panel + battery photos, tagging setter and closer
5. Sends customer a follow-up SMS with referral ask
6. Downloads BOM from Gmail and CAD/planset from Coperniq
7. Exports Company Cam checklist as PDF (Playwright)
8. Uploads all docs to Lux Financial portal (Playwright)
9. Emails Kathy at Lux that M2 was submitted
10. Creates M2 work order + form in Coperniq, sets Finance Status → M2 Submitted, WO → WAITING

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

All three automations run as launchd daemons on the Mac mini so they survive reboots and auto-restart on failure.

Plist files are in `~/Library/LaunchAgents/`. To restart a daemon after a code change:

```bash
# Kill the process — launchd will auto-restart it
pkill -f ntp_automation.py
pkill -f m2_automation.py
pkill -f ntp_stip_automation.py
```

> **Note:** `processed_stip_emails.json` is loaded into memory at startup. Restart the daemon after editing it manually.

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
| `install_automation.py` | Install Completion automation |
| `install_browser.py` | Playwright browser automation for install (CC PDF, Lux upload) |
| `create_lux_session.py` | One-time Lux portal Google OAuth session creation |
| `processed_installs.json` | Tracks processed install project IDs — **not committed** |
| `install_automation.log` | Install automation log output |
| `lux_session.json` | Saved Lux portal session — **not committed** |
