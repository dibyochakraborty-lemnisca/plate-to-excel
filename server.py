import json
import logging
import os
import re
import sqlite3
import threading
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from extraction import Plate, extract_plate, normalize_image
from protocol import ReadingTemplate, assign_wells, experiment_workbook, trajectories

load_dotenv(Path(__file__).with_name('.env'))
ROOT = Path(__file__).parent
DATA = Path(os.getenv('DATA_DIR', ROOT / 'data'))
logger = logging.getLogger('plate')


@contextmanager
def database():
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA / 'lab.sqlite3', timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db():
    with database() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS experiments(
          id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL,
          started_at TEXT NOT NULL, sheet_id TEXT NOT NULL DEFAULT '', sync_status TEXT NOT NULL DEFAULT 'not linked',
          sync_error TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id), config TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS captures(
          id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id), template_id TEXT NOT NULL REFERENCES templates(id),
          template TEXT NOT NULL, filename TEXT NOT NULL, captured_at TEXT NOT NULL, original_captured_at TEXT NOT NULL,
          sample_index INTEGER NOT NULL, image BLOB NOT NULL, status TEXT NOT NULL DEFAULT 'uploaded',
          plate TEXT, readings TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS edits(id INTEGER PRIMARY KEY, capture_id TEXT NOT NULL REFERENCES captures(id),
          edited_at TEXT NOT NULL, before_json TEXT NOT NULL);
        ''')
        db.execute("UPDATE captures SET status='failed',error='Processing was interrupted by a server restart. Retry extraction.' WHERE status='processing'")
        db.execute("UPDATE experiments SET sync_status='pending' WHERE sync_status='syncing'")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include a time zone.')
    return parsed.astimezone(timezone.utc).isoformat()


def get_experiment(db, experiment_id):
    row = db.execute('SELECT * FROM experiments WHERE id=?', (experiment_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Experiment not found.')
    return dict(row)


def get_capture(db, capture_id):
    row = db.execute('SELECT * FROM captures WHERE id=?', (capture_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Photo not found.')
    return row


def public_capture(row):
    result = {key: row[key] for key in row.keys() if key != 'image'}
    for key in ('template', 'plate', 'readings'):
        result[key] = json.loads(result[key]) if result[key] else None
    return result


def dirty(db, experiment_id):
    db.execute("UPDATE experiments SET version=version+1,sync_status=CASE WHEN sheet_id='' THEN 'not linked' ELSE 'pending' END,sync_error='' WHERE id=?", (experiment_id,))


def process_one():
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM captures WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
        if not row:
            return False
        db.execute("UPDATE captures SET status='processing',error='' WHERE id=?", (row['id'],))
    try:
        plate = extract_plate(bytes(row['image']), os.environ['GEMINI_API_KEY'], os.getenv('GEMINI_MODEL', 'gemini-3.8-flash'))
        try:
            readings = assign_wells(plate, ReadingTemplate.model_validate_json(row['template']), row['sample_index'])
            mapping_error = ''
        except ValueError as exc:
            readings = []
            mapping_error = str(exc)
        with database() as db:
            db.execute("UPDATE captures SET status='ready',plate=?,readings=?,error=?,revision=revision+1 WHERE id=?",
                       (plate.model_dump_json(), json.dumps(readings), mapping_error, row['id']))
            dirty(db, row['experiment_id'])
    except Exception as exc:
        logger.warning('Extraction failed: %s', type(exc).__name__)
        message = str(exc) if isinstance(exc, ValueError) else 'Extraction failed. Check photo clarity, Gemini access and available credits, then retry.'
        with database() as db:
            db.execute("UPDATE captures SET status='failed',error=? WHERE id=?", (message[:500], row['id']))
    return True


def sync_one():
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM experiments WHERE sync_status='pending' AND sheet_id!='' LIMIT 1").fetchone()
        if not row:
            return False
        experiment = dict(row)
        captures = [public_capture(item) for item in db.execute('SELECT * FROM captures WHERE experiment_id=? ORDER BY captured_at,id', (row['id'],))]
        db.execute("UPDATE experiments SET sync_status='syncing' WHERE id=?", (row['id'],))
    try:
        from sheets_sync import sync_experiment
        sync_experiment(experiment, captures)
        with database() as db:
            db.execute("UPDATE experiments SET sync_status=CASE WHEN version=? THEN 'synced' ELSE 'pending' END,sync_error='' WHERE id=?", (experiment['version'], experiment['id']))
    except Exception as exc:
        logger.warning('Sheets sync failed: %s', type(exc).__name__)
        with database() as db:
            db.execute("UPDATE experiments SET sync_status='failed',sync_error=? WHERE id=? AND version=?",
                       ('Google Sheets sync failed. Verify server credentials, Sheets API access and that the sheet is shared with the service account. Your data is saved here.', experiment['id'], experiment['version']))
    return True


def worker(stop):
    while not stop.is_set():
        try:
            worked = process_one()
            worked = sync_one() or worked
        except Exception:
            logger.exception('Worker could not access queued work')
            worked = False
        stop.wait(0.2 if worked else 2)


@asynccontextmanager
async def lifespan(app):
    init_db()
    stop = threading.Event()
    thread = threading.Thread(target=worker, args=(stop,), daemon=True)
    if os.getenv('DISABLE_WORKER') != '1':
        thread.start()
    yield
    stop.set()
    if thread.is_alive():
        thread.join(timeout=3)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware('http')
async def security(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('x-plate-request') != '1':
        return Response('Use the app to submit this request.', status_code=403)
    if int(request.headers.get('content-length', '0')) > 21 * 1024 * 1024:
        return Response('Photo exceeds 20 MB.', status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    if request.url.path.startswith('/api'):
        response.headers['Cache-Control'] = 'no-store'
    return response


class ExperimentInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    started_at: str
    sheet_id: str = Field(default='', max_length=250)

    @field_validator('started_at')
    @classmethod
    def valid_time(cls, value):
        return timestamp(value)

    @field_validator('name')
    @classmethod
    def valid_name(cls, value):
        if not value.strip():
            raise ValueError('Experiment ID is required.')
        return value.strip()

    @field_validator('sheet_id')
    @classmethod
    def valid_sheet(cls, value):
        value = value.strip()
        if not value:
            return ''
        match = re.search(r'/spreadsheets/d/([\w-]+)', value)
        value = match.group(1) if match else value
        if not re.fullmatch(r'[A-Za-z0-9_-]{20,120}', value):
            raise ValueError('Paste a Google spreadsheet URL or ID.')
        return value


@app.get('/api/experiments')
def experiments():
    with database() as db:
        return [dict(row) for row in db.execute('SELECT * FROM experiments ORDER BY rowid DESC')]


@app.post('/api/experiments')
def create_experiment(data: ExperimentInput):
    experiment_id = str(uuid4())
    with database() as db:
        try:
            db.execute('INSERT INTO experiments(id,name,started_at,sheet_id) VALUES (?,?,?,?)',
                       (experiment_id, data.name, data.started_at, data.sheet_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'An experiment with that ID already exists.')
        return get_experiment(db, experiment_id)


@app.put('/api/experiments/{experiment_id}')
def update_experiment(experiment_id: str, data: ExperimentInput):
    with database() as db:
        get_experiment(db, experiment_id)
        try:
            db.execute('UPDATE experiments SET name=?,started_at=?,sheet_id=? WHERE id=?', (data.name, data.started_at, data.sheet_id, experiment_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'An experiment with that ID already exists.')
        dirty(db, experiment_id)
        return get_experiment(db, experiment_id)


@app.get('/api/experiments/{experiment_id}')
def experiment_detail(experiment_id: str):
    with database() as db:
        result = get_experiment(db, experiment_id)
        result['templates'] = [dict(id=row['id'], **json.loads(row['config'])) for row in db.execute('SELECT * FROM templates WHERE experiment_id=? ORDER BY rowid', (experiment_id,))]
        result['captures'] = [public_capture(row) for row in db.execute('SELECT * FROM captures WHERE experiment_id=? ORDER BY captured_at,id', (experiment_id,))]
    result['trajectories'] = trajectories(result['captures'], result['started_at'])
    return result


@app.post('/api/experiments/{experiment_id}/templates')
def create_template(experiment_id: str, data: ReadingTemplate):
    with database() as db:
        get_experiment(db, experiment_id)
        template_id = str(uuid4())
        db.execute('INSERT INTO templates VALUES (?,?,?)', (template_id, experiment_id, data.model_dump_json()))
    return dict(id=template_id, **data.model_dump())


@app.post('/api/experiments/{experiment_id}/photos')
async def upload_photo(experiment_id: str, id: str = Form(), template_id: str = Form(), captured_at: str = Form(),
                       photo: UploadFile = File()):
    try:
        UUID(id)
        captured_at = timestamp(captured_at)
    except ValueError:
        raise HTTPException(422, 'Invalid capture ID or timestamp.')
    with database() as db:
        get_experiment(db, experiment_id)
        previous = db.execute('SELECT * FROM captures WHERE id=?', (id,)).fetchone()
        if previous:
            if previous['experiment_id'] != experiment_id:
                raise HTTPException(409, 'Capture ID already used.')
            return public_capture(previous)
        template = db.execute('SELECT * FROM templates WHERE id=? AND experiment_id=?', (template_id, experiment_id)).fetchone()
        if not template:
            raise HTTPException(422, 'Select a template from this experiment.')
    data = await photo.read(20 * 1024 * 1024 + 1)
    try:
        normalized = normalize_image(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT * FROM captures WHERE id=?', (id,)).fetchone()
        if previous:
            if previous['experiment_id'] != experiment_id:
                raise HTTPException(409, 'Capture ID already used.')
            return public_capture(previous)
        sample = db.execute('SELECT COALESCE(MAX(sample_index),-1)+1 FROM captures WHERE experiment_id=? AND template_id=?', (experiment_id, template_id)).fetchone()[0]
        db.execute('INSERT INTO captures(id,experiment_id,template_id,template,filename,captured_at,original_captured_at,sample_index,image) VALUES (?,?,?,?,?,?,?,?,?)',
                   (id, experiment_id, template_id, template['config'], (photo.filename or 'Photo')[:200], captured_at, captured_at, sample, normalized))
        dirty(db, experiment_id)
        return public_capture(db.execute('SELECT * FROM captures WHERE id=?', (id,)).fetchone())


@app.get('/api/photos/{capture_id}/image')
def photo_image(capture_id: str):
    with database() as db:
        row = get_capture(db, capture_id)
        return Response(bytes(row['image']), media_type='image/png')


@app.post('/api/photos/{capture_id}/process')
def queue_photo(capture_id: str):
    if not os.getenv('GEMINI_API_KEY'):
        raise HTTPException(503, 'The server needs a Gemini API key. Your photo is saved.')
    with database() as db:
        row = get_capture(db, capture_id)
        if row['status'] in ('uploaded', 'failed'):
            db.execute("UPDATE captures SET status='queued',error='' WHERE id=?", (capture_id,))
    return {'ok': True}


class ReviewedReading(BaseModel):
    flask: str = Field(min_length=1, max_length=80)
    replicate: int = Field(ge=1)
    row: str
    column: str
    value: float | None = Field(allow_inf_nan=False)


class ReviewInput(BaseModel):
    revision: int
    captured_at: str
    sample_index: int = Field(ge=0)
    readings: list[ReviewedReading]

    @field_validator('captured_at')
    @classmethod
    def valid_time(cls, value):
        return timestamp(value)


@app.put('/api/photos/{capture_id}/review')
def save_review(capture_id: str, data: ReviewInput):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = get_capture(db, capture_id)
        if row['revision'] != data.revision:
            raise HTTPException(409, 'This photo was changed elsewhere. Reload before editing again.')
        if not row['plate'] or row['status'] not in ('ready', 'reviewed'):
            raise HTTPException(409, 'Wait for extraction before reviewing.')
        plate = Plate.model_validate_json(row['plate'])
        template = ReadingTemplate.model_validate_json(row['template'])
        expected = {(f'FL-{i:02d}', r) for i in range(template.flask_start, template.flask_start + template.flask_count) for r in range(1, template.replicates + 1)}
        wells = {(well.row, well.column) for well in plate.wells}
        pairs, used_wells, cleaned = set(), set(), []
        try:
            plate_number = assign_wells(plate, template, data.sample_index)[0]['plate_number']
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        import math
        for reading in data.readings:
            item = reading.model_dump()
            pair = (item.get('flask'), item.get('replicate'))
            well = (item.get('row'), item.get('column'))
            value = item.get('value')
            if pair not in expected or pair in pairs or well not in wells or well in used_wells:
                raise HTTPException(422, 'Each flask replicate must map to one distinct well from the photo.')
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise HTTPException(422, 'OD must be a finite number or blank.')
            pairs.add(pair)
            used_wells.add(well)
            original = next(well_value for well_value in plate.wells if (well_value.row, well_value.column) == well)
            cleaned.append(dict(flask=pair[0], replicate=pair[1], row=well[0], column=well[1], value=value,
                                status='missing' if value is None else ('readable' if value == original.value else 'corrected'),
                                plate_number=plate_number))
        if pairs != expected:
            raise HTTPException(422, 'Include every flask and replicate from the template.')
        db.execute('INSERT INTO edits(capture_id,edited_at,before_json) VALUES (?,?,?)', (capture_id, utc_now(), json.dumps(public_capture(row))))
        db.execute("UPDATE captures SET readings=?,captured_at=?,sample_index=?,status='reviewed',revision=revision+1 WHERE id=?",
                   (json.dumps(cleaned), data.captured_at, data.sample_index, capture_id))
        dirty(db, row['experiment_id'])
    return {'ok': True}


class RemapInput(BaseModel):
    revision: int
    template_id: str
    sample_index: int = Field(ge=0)


@app.post('/api/photos/{capture_id}/remap')
def remap(capture_id: str, data: RemapInput):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = get_capture(db, capture_id)
        if row['revision'] != data.revision or row['status'] not in ('ready', 'reviewed'):
            raise HTTPException(409, 'Reload this photo before changing its mapping.')
        template = db.execute('SELECT * FROM templates WHERE id=? AND experiment_id=?', (data.template_id, row['experiment_id'])).fetchone()
        if not template:
            raise HTTPException(422, 'Template not found in this experiment.')
        try:
            readings = assign_wells(Plate.model_validate_json(row['plate']), ReadingTemplate.model_validate_json(template['config']), data.sample_index)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        db.execute('INSERT INTO edits(capture_id,edited_at,before_json) VALUES (?,?,?)', (capture_id, utc_now(), json.dumps(public_capture(row))))
        db.execute("UPDATE captures SET template_id=?,template=?,sample_index=?,readings=?,status='ready',error='',revision=revision+1 WHERE id=?",
                   (template['id'], template['config'], data.sample_index, json.dumps(readings), capture_id))
        dirty(db, row['experiment_id'])
    return {'ok': True}


@app.post('/api/experiments/{experiment_id}/sync')
def retry_sync(experiment_id: str):
    with database() as db:
        experiment = get_experiment(db, experiment_id)
        if not experiment['sheet_id']:
            raise HTTPException(422, 'Add a Google Sheet URL in experiment settings first.')
        dirty(db, experiment_id)
    return {'ok': True}


@app.get('/api/experiments/{experiment_id}/export')
def export(experiment_id: str):
    data = experiment_detail(experiment_id)
    return Response(experiment_workbook(data, data['captures']), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': 'attachment; filename="experiment.xlsx"'})


@app.get('/api/config')
def config():
    return dict(sheets_configured=bool(os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON') or os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE') or os.getenv('SHEETS_IMPERSONATE_ACCOUNT') or os.getenv('GOOGLE_CLOUD_PROJECT')))


@app.get('/health')
def health():
    return {'ok': True}


@app.get('/')
def index():
    return FileResponse(ROOT / 'web/index.html')


@app.get('/sw.js')
def service_worker():
    return FileResponse(ROOT / 'web/sw.js', media_type='application/javascript', headers={'Cache-Control': 'no-cache'})


app.mount('/static', StaticFiles(directory=ROOT / 'web'), name='static')
