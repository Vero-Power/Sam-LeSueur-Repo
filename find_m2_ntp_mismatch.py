import requests, os, time
from dotenv import load_dotenv
load_dotenv()

BASE = 'https://api.coperniq.io/v1'
H  = {'x-api-key': os.getenv('COPERNIQ_API_KEY')}
PH = {'x-api-key': os.getenv('COPERNIQ_API_KEY'), 'Content-Type': 'application/json'}

def get_form_finance_status(proj_id, keyword):
    forms = requests.get(f'{BASE}/projects/{proj_id}/forms', headers=H).json()
    stub = next((f for f in forms if not f.get('isArchived') and keyword in (f.get('name') or '')), None)
    if not stub:
        return None, None, None
    full = requests.get(f'{BASE}/forms/{stub["id"]}', headers=H).json()
    props = []
    for layout in full.get('formLayouts', []):
        for prop in layout.get('properties', []):
            props.append(prop)
            for field in prop.get('fields', []): props.append(field)
    fm = {p['name']: p for p in props if 'name' in p}
    fin = fm.get('Finance Status', {}).get('value', '')
    fin_str = ' '.join(fin) if isinstance(fin, list) else str(fin or '')
    completed = full.get('status') == 'COMPLETED' or full.get('isCompleted')
    return stub['id'], fm, fin_str, completed

def has_completed_m2_wo(proj_id):
    wos = requests.get(f'{BASE}/projects/{proj_id}/work-orders', headers=H).json()
    return any(
        'M2' in (w.get('title') or '') and not w.get('isArchived') and w.get('isCompleted')
        for w in wos
    )

# Paginate all projects
mismatches = []
offset = 0
limit = 50
print('Scanning all projects...')

while True:
    r = requests.get(f'{BASE}/projects', params={'companyId': 392, 'limit': limit, 'offset': offset}, headers=H)
    projects = r.json()
    if not projects or not isinstance(projects, list):
        break

    for proj in projects:
        proj_id = proj['id']
        title = proj.get('title', '')
        status = proj.get('status', '')

        if status in ('CANCELLED', 'ARCHIVED'):
            continue

        # Check NTP form Finance Status
        result = get_form_finance_status(proj_id, 'Notice to Proceed')
        if result[0] is None:
            continue
        ntp_form_id, ntp_fm, ntp_fin, _ = result

        if 'NTP Approved' not in ntp_fin:
            continue  # Not NTP Approved, skip

        # Has NTP Approved — check if M2 form or WO is completed (meaning should be M2 Approved)
        m2_result = get_form_finance_status(proj_id, 'M2')
        m2_form_id, m2_fm, m2_fin, m2_completed = m2_result if m2_result[0] else (None, None, '', False)

        m2_wo_done = has_completed_m2_wo(proj_id)

        if m2_completed or m2_wo_done or 'M2 Approved' in (m2_fin or ''):
            print(f'MISMATCH: {title} ({proj_id}) — NTP form says "{ntp_fin}" but M2 form completed={m2_completed}, M2 WO done={m2_wo_done}, M2 form status="{m2_fin}"')
            mismatches.append({'title': title, 'proj_id': proj_id, 'ntp_form_id': ntp_form_id, 'ntp_fm': ntp_fm})

        time.sleep(0.15)

    if len(projects) < limit:
        break
    offset += limit
    print(f'  scanned {offset} projects...')

print(f'\nTotal mismatches: {len(mismatches)}')

# Fix them all
if mismatches:
    print('\nFixing...')
    for m in mismatches:
        if 'Finance Status' in m['ntp_fm']:
            r = requests.patch(f'{BASE}/forms/{m["ntp_form_id"]}', json={
                'fields': [{'columnId': m['ntp_fm']['Finance Status']['columnId'], 'value': 'M2 Approved'}]
            }, headers=PH)
            print(f'  {m["title"]} ({m["proj_id"]}): {r.status_code} -> M2 Approved')
