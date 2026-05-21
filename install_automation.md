# Install Automation

Monitors Coperniq for completed solar installs and runs the full post-install workflow.

## Trigger

- Polls every **30 minutes**
- Looks for projects in Coperniq with `install_scheduled_date` = today
- For each project, checks Company Cam for a completed VERO SOLAR INSTALLER CHECKLIST
- Skips jobs already processed (tracked in `processed_installs.json`)
- Retries incomplete checklists on the next poll

## What It Does

1. Finds today's Solar Installation projects in Coperniq
2. Checks Company Cam for completed VERO SOLAR INSTALLER CHECKLIST
3. Completes the Solar Installation work order, form, and field visit in Coperniq
4. Sends Slack message to #vero with panel + battery photos, tagging setter and closer
5. Sends customer follow-up SMS via Twilio (requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER in .env)
6. Downloads BOM from Gmail (`[Customer] Solar Materials` email) and CAD/planset from Coperniq
7. Exports Company Cam install photos as a multi-page PDF (via CC API + Pillow — no browser needed). Filters to 'installed panel' and 'installed battery' photos only, excludes serial number/barcode tasks.
8. Uploads all documents (checklist PDF, CAD/planset, BOM) to Lux portal
9. Emails kathy.treanor@luxfinancial.io that M2 was submitted
10. Finds or creates the M2 work order and form in Coperniq, sets Finance Status → M2 Submitted, WO → WAITING, leaves a note

## Slack Format

```
Customer: [Name]
Setter: @[tag]
Closer: @[tag]
[X]kW+battery 🔋
Area: [City]
```

Plus panel and battery photos from the Company Cam installer checklist.

## How It Runs

- Polls every **30 minutes** via a `while True` loop
- Runs as a launchd daemon on the Mac mini (auto-restarts on failure/reboot)
- Processed project IDs saved to `processed_installs.json` to prevent reprocessing

## One-Time Setup

### Create Lux portal session

Before running for the first time, create a saved Google OAuth session for the Lux portal:

```bash
cd /Users/samlesueur/vero-power
python create_lux_session.py
```

This opens a browser window. Log in with sam@veropwr.com and complete any Google prompts. The session is saved to `lux_session.json` and reused automatically.

### Load the launchd daemon

```bash
launchctl load ~/Library/LaunchAgents/com.vero.install-automation.plist
launchctl list | grep install
```

## Restart

```bash
# Kill the process — launchd auto-restarts it
pkill -f install_automation.py
```

## Logs

```bash
tail -f install_automation.log
```

## Environment Variables Required

| Variable | Description |
|----------|-------------|
| `GMAIL_ADDRESS` | Gmail address to monitor |
| `GMAIL_APP_PASSWORD` | Gmail app password |
| `COPERNIQ_API_KEY` | Coperniq API key |
| `SLACK_BOT_TOKEN` | Slack bot token |
| `COMPANY_CAM_API_KEY` | Company Cam API key |
| `LUX_GOOGLE_PASSWORD` | Google password for Lux portal login |
| `TESLA_CLIENT_ID` | Tesla PowerHub API client ID |
| `TESLA_CLIENT_SECRET` | Tesla PowerHub API client secret |
| `TESLA_GROUP_ID` | Tesla PowerHub group ID |
| `KATHY_EMAIL` | Kathy's email at Lux Financial |
| `TWILIO_ACCOUNT_SID` | Twilio account SID for customer SMS |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_FROM_NUMBER` | Twilio phone number to send SMS from |

## Files

| File | Description |
|------|-------------|
| `install_automation.py` | Main script |
| `install_browser.py` | Playwright browser automation (Lux upload, Tesla screenshot) |
| `create_lux_session.py` | One-time script to create saved Lux Google OAuth + 2FA session |
| `create_tesla_session.py` | One-time script to create saved Tesla PowerHub browser session |
| `install_automation.log` | Log output |
| `processed_installs.json` | Tracks processed project IDs — do not delete |
| `lux_session.json` | Saved Lux portal session — do not commit |
| `tesla_session.json` | Saved Tesla PowerHub session — do not commit |
