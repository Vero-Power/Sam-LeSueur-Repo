# Clock Automation

Monitors #corporate-operations Slack channel for Sam's messages and automatically clocks in/out on the employee dashboard.

## Trigger

Watches for messages from Sam LeSueur (U0AB51A9J9H) in #corporate-operations (C0AB50H2K9R).

## Clock IN

Any message containing one of these phrases (case-insensitive):
- `locked in`, `locked yin`
- `yoked in`, `yoked yin`
- `clocked in`
- `checking in`, `check in`, `checkin`
- `in the office`, `at the office`, `in the building`

**Example:** `yoked in`

Calls `POST https://disputes.veropwr.com/api/timeclock/clock-in`. Skips if already clocked in.

## Clock OUT

Any message containing both `arrived` and `left`.

**Example:**
```
arrived 8:03
left 5:12
```

Calls `POST https://disputes.veropwr.com/api/timeclock/clock-out`. Skips if not clocked in.

## How It Runs

- Polls Slack every **2 minutes**
- Runs as a launchd daemon on the Mac mini (auto-restarts on failure/reboot)
- State saved to `clock_state.json` to track last processed message timestamp

## Dashboard

`https://employee-dashboard-grading.vercel.app/sam`

Timeclock API base: `https://disputes.veropwr.com/api/timeclock`

## Restart

```bash
pkill -f clock_automation.py
# launchd auto-restarts it
```

## Logs

```bash
tail -f clock_automation.log
```

## Files

| File | Description |
|------|-------------|
| `clock_automation.py` | Main script |
| `clock_automation.log` | Log output |
| `clock_state.json` | Tracks last processed Slack message — do not delete |
