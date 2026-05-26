# M3 Submission Automation

Polls Coperniq every 30 minutes for projects where PTO has been granted, then submits M3 to the Lux portal, updates Coperniq, and notifies Lux.

## Trigger

- Both **PTO Submitted** WO (template 1907082) and **PTO Approved** WO (template 1907092) are `COMPLETED`
- **M3 W/O** (template 1907089) is still `WAITING` (not yet submitted)
- Project not already in `processed_m3_projects.json`

## What It Does

1. Takes a Tesla PowerHub commissioning screenshot via Tesla GridLogic API (no browser login needed)
2. Downloads the PTO Letter PDF from the M3 form in Coperniq (field columnId 17266985)
3. Uploads to Lux portal **Pending PTO** section:
   - **Proof of Commissionsing** (Lux has a typo — double 's') → Tesla commissioning screenshot
   - **PTO Letter** → PTO Letter PDF from Coperniq
4. Updates **M3 form** (template 1191548) in Coperniq:
   - Finance Status → `M3 Submitted`
   - M3 Submitted Date → today
   - Finance Provider → `Lux Financial`
5. Updates **Commissioning form** (template 1191545) in Coperniq:
   - Commissioning Status → `Completed`
   - Commissioning Complete Date → today
   - Then marks form status → `COMPLETED`
6. Sets **Commissioning W/O** (template 1907086) → `COMPLETED`:
   - Completes each checklist item individually via `/work-orders/{id}/checklist/{item_id}`
   - Then patches WO with `{'status': 'COMPLETED'}` — **not** `isCompleted: true` (that does nothing)
7. Sends email to kathy.treanor@luxfinancial.io and michael.paris@luxfinancial.io
8. Leaves a note in Coperniq: *"M3 Submitted — automated via PTO approval. Lux portal updated, Kathy and Mike notified."*

## Key Coperniq IDs

| Resource | Template/Column ID |
|----------|--------------------|
| PTO Submitted WO template | 1907082 |
| PTO Approved WO template | 1907092 |
| M3 W/O template | 1907089 |
| Commissioning W/O template | 1907086 |
| M3 form template | 1191548 |
| Commissioning form template | 1191545 |
| M3 form — Finance Status column | 17266976 |
| M3 form — M3 Submitted Date column | 17266977 |
| M3 form — Finance Provider column | 17266979 |
| M3 form — PTO Letter column | 17266985 |
| Commissioning form — Monitoring Upload column | 17267028 |
| Commissioning form — Status column | 17267029 |
| Commissioning form — Complete Date column | 17267030 |

## How It Runs

- Polls every **30 minutes** via a `while True` loop
- Runs as a launchd daemon on the Mac mini (auto-restarts on failure/reboot)
- Processed project IDs saved to `processed_m3_projects.json` to prevent reprocessing
- Retries automatically on Coperniq rate limits (up to 5 retries, 15s spacing)

## Load the daemon

```bash
launchctl load ~/Library/LaunchAgents/com.vero.m3-automation.plist
launchctl list | grep m3
```

## Restart

```bash
pkill -f m3_automation.py
# launchd auto-restarts it
```

## Logs

```bash
tail -f m3_automation.log
```

## Environment Variables Required

| Variable | Description |
|----------|-------------|
| `GMAIL_ADDRESS` | Gmail address |
| `GMAIL_APP_PASSWORD` | Gmail app password |
| `COPERNIQ_API_KEY` | Coperniq API key |
| `TESLA_CLIENT_ID` | Tesla GridLogic API client ID |
| `TESLA_CLIENT_SECRET` | Tesla GridLogic API client secret |
| `TESLA_GROUP_ID` | Tesla GridLogic Vero group ID |
| `KATHY_EMAIL` | Kathy's email at Lux Financial |

## Files

| File | Description |
|------|-------------|
| `m3_automation.py` | Main script |
| `m3_automation.log` | Log output |
| `processed_m3_projects.json` | Tracks processed project IDs — do not delete |
| `~/Library/LaunchAgents/com.vero.m3-automation.plist` | launchd daemon config |
