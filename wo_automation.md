# WO Assignment Automation

Polls all Coperniq projects every 5 minutes for work orders assigned to Sam (or where Sam is a collaborator) that are still ASSIGNED status. Moves them to WAITING and leaves the appropriate note.

## Trigger

- WO is assigned to Sam (user ID 14206) OR Sam is a collaborator on the WO
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
| Signed Design / Planset Review | 2121772 | Move to WAITING, leave note tagging Clay |
| 🛠️ Construction Review | 1907081 | Skip — Sam should not be assigned these |
| 🥷🏿 Solar Installation | 1907074/1907085 | Skip — always stays ASSIGNED |

## How It Runs

- Polls every **5 minutes** via a `while True` loop
- Scans all 197 Coperniq projects by iterating project numbers 1–250+
- Project number → project ID map cached in `all_projects.json`, refreshed every hour
- New projects (numbers above current max) discovered automatically on each refresh
- Checks both primary assignee AND collaborators for Sam
- Runs as a launchd daemon on the Mac mini (auto-restarts on failure/reboot)
- Processed WO IDs saved to `processed_wo_assignments.json` to prevent reprocessing

## Why Project Number Scanning

The Coperniq `/work-orders` API is hard-capped at 20 results (always returns the oldest 20, ignores pagination and filters). The only reliable way to get all WOs is to iterate through each project individually using `GET /projects?number=N`.

## Key User IDs

| Name | Coperniq ID |
|------|-------------|
| Sam LeSueur | 14206 |
| Daxton Dillon | 14205 |
| Clay Neser | 14204 |

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
| `all_projects.json` | Cached project number→ID map — safe to delete (rebuilds on restart) |
| `~/Library/LaunchAgents/com.vero.wo-automation.plist` | launchd daemon config |
