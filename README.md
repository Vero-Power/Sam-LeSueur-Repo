# Vero Power — NTP Approval Automation

Monitors Gmail for LUX Financial NTP approval emails and automatically updates Coperniq.

## What it does

When an email with subject `Vero LLC NTP Approval: [Customer Name]` arrives:

1. Finds the customer's project in Coperniq
2. Finds or creates the Notice to Proceed work order
3. Checks off all checklist items
4. Finds or creates the Notice to Proceed form
5. Sets Finance Status, dates, Stipulations, Finance Provider
6. Completes the form and work order
7. Leaves a comment on the project

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set up Gmail API credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or use an existing one)
3. Enable the **Gmail API**
4. Go to **Credentials → Create Credentials → OAuth 2.0 Client ID**
5. Application type: **Desktop app**
6. Download the JSON file and save it as `credentials.json` in this folder

### 3. Run

```bash
python ntp_automation.py
```

On first run, a browser window will open for Gmail OAuth authorization. After that, it runs headlessly and polls every 2 minutes.

## Files

| File | Description |
|------|-------------|
| `ntp_automation.py` | Main script |
| `credentials.json` | Gmail OAuth credentials (**not committed**) |
| `token.json` | Gmail auth token, auto-generated (**not committed**) |
| `processed_emails.json` | Tracks already-processed email IDs (**not committed**) |
