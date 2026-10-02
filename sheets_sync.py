import json
import os
import subprocess

import google.auth
from google.oauth2.credentials import Credentials as TokenCredentials
from io import BytesIO

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials
from openpyxl import load_workbook

from protocol import experiment_workbook, trajectories


def sync_experiment(experiment, captures):
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    if os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON'):
        credentials = Credentials.from_service_account_info(json.loads(os.environ['GOOGLE_SERVICE_ACCOUNT_JSON']), scopes=scopes)
    elif os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE'):
        credentials = Credentials.from_service_account_file(os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], scopes=scopes)
    elif os.getenv('SHEETS_IMPERSONATE_ACCOUNT'):
        token = subprocess.run(['gcloud', 'auth', 'print-access-token',
                                '--account=' + os.environ['SHEETS_GCLOUD_ACCOUNT'],
                                '--impersonate-service-account=' + os.environ['SHEETS_IMPERSONATE_ACCOUNT'],
                                '--scopes=' + scopes[0]], capture_output=True, text=True, check=True, timeout=30)
        credentials = TokenCredentials(token=token.stdout.strip())
    else:
        credentials, _ = google.auth.default(scopes=scopes)
    with AuthorizedSession(credentials) as session:
        write_spreadsheet(session, experiment, captures)


def write_spreadsheet(session, experiment, captures):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{experiment['sheet_id']}"
    response = session.get(url, params={'fields': 'sheets(properties,charts(chartId))'}, timeout=30)
    response.raise_for_status()
    existing = {sheet['properties']['title']: sheet for sheet in response.json().get('sheets', [])}
    next_id = max([sheet['properties']['sheetId'] for sheet in existing.values()] + [0]) + 1
    workbook = load_workbook(BytesIO(experiment_workbook(experiment, captures)))
    requests = []
    for tab_name in ('Readings', 'Trajectories', 'Photos'):
        name = f"Plate {experiment['id'][:8]} {tab_name}"
        tab = existing.get(name)
        values = list(workbook[tab_name].values)
        if tab:
            sheet_id = tab['properties']['sheetId']
            requests.append({'updateSheetProperties': {'properties': {'sheetId': sheet_id, 'gridProperties': {'rowCount': max(1000, len(values)), 'columnCount': 26}}, 'fields': 'gridProperties.rowCount,gridProperties.columnCount'}})
        else:
            sheet_id = next_id
            next_id += 1
            requests.append({'addSheet': {'properties': {'sheetId': sheet_id, 'title': name, 'gridProperties': {'rowCount': max(1000, len(values)), 'columnCount': 26, 'frozenRowCount': 1}}}})
        rows = []
        for row in values:
            cells = []
            for value in row:
                if value is None:
                    cells.append({})
                else:
                    key = 'numberValue' if isinstance(value, (int, float)) else 'stringValue'
                    cells.append({'userEnteredValue': {key: value}})
            rows.append({'values': cells})
        requests.append({'updateCells': {'range': {'sheetId': sheet_id}, 'rows': rows, 'fields': 'userEnteredValue'}})
        if tab_name == 'Trajectories':
            for chart in (tab or {}).get('charts', []):
                requests.append({'deleteEmbeddedObject': {'objectId': chart['chartId']}})
            points = trajectories(captures, experiment['started_at'])
            for index, flask in enumerate(sorted({point['flask'] for point in points})):
                indices = [i + 1 for i, point in enumerate(points) if point['flask'] == flask]
                def source(column):
                    return {'sourceRange': {'sources': [{'sheetId': sheet_id, 'startRowIndex': min(indices), 'endRowIndex': max(indices) + 1, 'startColumnIndex': column, 'endColumnIndex': column + 1}]}}
                requests.append({'addChart': {'chart': {'spec': {'title': f'{flask} · reviewed OD', 'basicChart': {
                    'chartType': 'SCATTER', 'legendPosition': 'NO_LEGEND', 'headerCount': 0,
                    'axis': [{'position': 'BOTTOM_AXIS', 'title': 'Hours from start'}, {'position': 'LEFT_AXIS', 'title': 'Mean OD'}],
                    'domains': [{'domain': source(2)}], 'series': [{'series': source(3), 'targetAxis': 'LEFT_AXIS', 'lineStyle': {'type': 'SOLID', 'width': 2}}]}},
                    'position': {'overlayPosition': {'anchorCell': {'sheetId': sheet_id, 'rowIndex': index * 18, 'columnIndex': 8}, 'widthPixels': 600, 'heightPixels': 320}}}}})
    response = session.post(url + ':batchUpdate', json={'requests': requests}, timeout=60)
    response.raise_for_status()
