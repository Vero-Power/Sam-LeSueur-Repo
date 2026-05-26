# WO Assignment Automation

Polls Coperniq every 5 minutes for work orders assigned to Sam that are still ASSIGNED status. Moves them to WAITING and leaves the appropriate note.

## Trigger

- WO is assigned to Sam (user ID 14206)
- WO status is `ASSIGNED`
- WO template is one of the handled types below
- WO not already processed (tracked in `processed_wo_assignments.json`)

## What It Does Per WO Type

| WO | Template | Action |
|----|----------|--------|
| ✅ Verofication | 1907069 | Move to WAITING, leave note tagging Daxton |
| ⚡️ Electrical Review | 1907084 | Move to WAITING, leave note tagging Daxton |
| 🧾 Notice to Proceed (NTP) W/O | 1907087 | Move to WAITING, leave note: "Waiting on underwriting review." |
| 🪖 Change Order | 1907067 | Move to WAITING, leave note: "Waiting on change order details." tagging Sam |
| ⏳ Milestone 2 (M2) W/O | 1907088 | Move to WAITING, leave note: "Waiting on install to be completed." |
| 🛠️ Construction Review | 1907081 | Skip — Sam should not be assigned these |

## How It Runs

- Polls every **5 minutes** via a `while True` loop
- Runs as a launchd daemon on the Mac mini (auto-restarts on failure/reboot)
- Processed WO IDs saved to `processed_wo_assignments.json` to prevent reprocessing
- Retries automatically on Coperniq rate limits (up to 5 retries, 15s spacing)

## Load the daemon

```bash
launchctl load ~/Library/LaunchAgents/com.vero.wo-automation.plist
launchctl list | grep wo
```

## Restart

```bash
pkill -f wo_automation.py
# launchd auto-restarts it
```

## Logs

```bash
tail -f wo_automation.log
```

## Key User IDs

| Name | Coperniq ID |
|------|-------------|
| Sam LeSueur | 14206 |
| Daxton Dillon | 14205 |

## Environment Variables Required

| Variable | Description |
|----------|-------------|
| `COPERNIQ_API_KEY` | Coperniq API key |

## Files

| File | Description |
|------|-------------|
| `wo_automation.py` | Main script |
| `wo_automation.log` | Log output |
| `processed_wo_assignments.json` | Tracks processed WO IDs — do not delete |
| `~/Library/LaunchAgents/com.vero.wo-automation.plist` | launchd daemon config |
