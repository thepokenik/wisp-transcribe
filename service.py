"""Single-process CPU transcription service. Run behind a TLS proxy or on a VPN."""
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from queue import Full, Queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

SCHEMA_VERSION = 1
DATA = Path(os.environ.get('DATA_DIR', './data')).resolve()
IDEMPOTENCY = DATA / 'idempotency'
KEY = os.environ.get('TRANSCRIPTION_API_KEY', '')
MAX_BYTES = int(os.environ.get('MAX_UPLOAD_MB', '500')) * 1024 * 1024
MAX_PROMPT = 2000
MAX_LANGUAGE = 10
JOBS = Queue(maxsize=int(os.environ.get('MAX_QUEUE', '10')))
LOCK = threading.Lock()
INFLIGHT = {}
MODEL = None
MODEL_NAME = os.environ.get('WHISPER_MODEL', 'small')
CONNECTION_TIMEOUT = int(os.environ.get('CONNECTION_TIMEOUT', '300'))
ERROR_CODES = (
    'UNAUTHORIZED', 'NOT_FOUND', 'JOB_NOT_FOUND', 'JOB_NOT_READY', 'JOB_INTERRUPTED',
    'INVALID_REQUEST', 'INVALID_MEDIA', 'PAYLOAD_TOO_LARGE', 'QUEUE_FULL',
    'UPLOAD_INTERRUPTED', 'IDEMPOTENCY_CONFLICT', 'TRANSCRIPTION_FAILED', 'INTERNAL_ERROR',
)


class InvalidMedia(Exception):
    pass


def iso_time(value):
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='seconds')


def write_json(folder, name, value):
    temporary = folder / (name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(folder / name)


def save(folder, job):
    job['updated_at'] = round(time.time(), 3)
    with LOCK:
        write_json(folder, 'status.json', job)


def load_job(folder):
    with LOCK:
        return json.loads((folder / 'status.json').read_text(encoding='utf-8'))


def job_summary(job, replay=False):
    summary = {'schema_version': SCHEMA_VERSION, 'id': job['id'], 'state': job['state'],
               'status_url': '/jobs/' + job['id']}
    if replay:
        summary['replayed'] = True
    if job['state'] == 'completed':
        summary['files'] = {'text': '/jobs/' + job['id'] + '/txt',
                            'segments': '/jobs/' + job['id'] + '/jsonl',
                            'manifest': '/jobs/' + job['id'] + '/manifest'}
    if job.get('error_code'):
        summary['error_code'] = job['error_code']
    return summary


def idempotency_digest(key):
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def find_replay(key):
    """Return the job previously created for this key, when it can be reused."""
    digest = idempotency_digest(key)
    mapping_file = IDEMPOTENCY / (digest + '.json')
    with LOCK:
        try:
            mapping = json.loads(mapping_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
    try:
        job = load_job(DATA / mapping['job_id'])
    except (OSError, ValueError, KeyError):
        with LOCK:
            mapping_file.unlink(missing_ok=True)
        return None
    if job.get('state') in ('queued', 'running', 'completed'):
        return job
    # A failed or interrupted job must not consume the key: the caller can retry.
    with LOCK:
        mapping_file.unlink(missing_ok=True)
    return None


def remember(key, job_id):
    digest = idempotency_digest(key)
    try:
        with LOCK:
            write_json(IDEMPOTENCY, digest + '.json',
                       {'job_id': job_id, 'created_at': round(time.time(), 3)})
    except OSError:
        logging.exception('Could not persist idempotency key for %s', job_id)


def validate_media(path):
    """Reject files without a decodable audio stream before loading Whisper."""
    try:
        import av
    except ImportError:
        # PyAV is installed in the container; skip the preflight when unavailable.
        return
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise InvalidMedia('no audio stream')
    except InvalidMedia:
        raise
    except Exception as exc:
        raise InvalidMedia(str(exc)) from exc


def load_model():
    global MODEL
    if MODEL is None:
        from faster_whisper import WhisperModel
        MODEL = WhisperModel(MODEL_NAME, device='cpu', compute_type='int8',
                             cpu_threads=int(os.environ.get('CPU_THREADS', '8')),
                             download_root=str(DATA / 'models'))
    return MODEL


def transcribe(folder, job):
    from faster_whisper import BatchedInferencePipeline
    pipeline = BatchedInferencePipeline(model=load_model())
    segments, info = pipeline.transcribe(str(folder / 'input.media'),
        language=job['language'] or None, initial_prompt=job['prompt'] or None,
        beam_size=1, batch_size=int(os.environ.get('BATCH_SIZE', '8')), vad_filter=True)
    count = 0
    characters = 0
    with (folder / 'transcript.jsonl').open('w', encoding='utf-8') as output, \
         (folder / 'transcript.txt').open('w', encoding='utf-8') as plain:
        for segment in segments:
            record = {'start': round(segment.start, 2), 'end': round(segment.end, 2), 'text': segment.text}
            output.write(json.dumps(record, ensure_ascii=False) + '\n')
            plain.write(segment.text.strip() + '\n')
            count += 1
            characters += len(segment.text.strip())
            job['processed_seconds'] = record['end']
            save(folder, job)
    duration = getattr(info, 'duration', None)
    after_vad = getattr(info, 'duration_after_vad', None)
    job.update(
        language=info.language,
        completed_at=round(time.time(), 3),
        processed_seconds=round(float(duration), 2) if duration else job['processed_seconds'],
        state='completed',
        result={
            'language': info.language,
            'language_probability': (round(float(info.language_probability), 4)
                                     if getattr(info, 'language_probability', None) is not None else None),
            'duration_seconds': round(float(duration), 2) if duration is not None else None,
            'duration_after_vad_seconds': round(float(after_vad), 2) if after_vad is not None else None,
            'model': MODEL_NAME,
            'segment_count': count,
            'text_characters': characters,
            'options': {
                'beam_size': 1,
                'batch_size': int(os.environ.get('BATCH_SIZE', '8')),
                'vad_filter': True,
                'requested_language': job.get('requested_language') or None,
                'has_prompt': bool(job['prompt']),
            },
        },
    )
    job.pop('error', None)
    job.pop('error_code', None)


def build_manifest(folder, job):
    result = job.get('result') or {}
    files = {'text': 'transcript.txt', 'segments': 'transcript.jsonl'}
    return {
        'schema_version': SCHEMA_VERSION,
        'job_id': job['id'],
        'state': job['state'],
        'created_at': iso_time(job.get('created_at')),
        'completed_at': iso_time(job.get('completed_at')),
        'language': result.get('language') or job.get('language'),
        'requested_language': job.get('requested_language', job.get('language')) or None,
        'duration_seconds': result.get('duration_seconds'),
        'model': result.get('model') or MODEL_NAME,
        'options': result.get('options') or {},
        'segment_count': result.get('segment_count'),
        'text_characters': result.get('text_characters'),
        'files': files,
        'sizes_bytes': {name: (folder / name).stat().st_size for name in files.values()
                        if (folder / name).exists()},
    }


def ensure_manifest(folder, job):
    path = folder / 'manifest.json'
    with LOCK:
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
    value = build_manifest(folder, job)
    with LOCK:
        write_json(folder, 'manifest.json', value)
    return value


def capabilities():
    return {
        'schema_version': SCHEMA_VERSION,
        'service': 'wisp-transcribe',
        'model': MODEL_NAME,
        'output_formats': ['txt', 'jsonl', 'manifest'],
        'statuses': ['uploading', 'queued', 'running', 'completed', 'failed'],
        'error_codes': list(ERROR_CODES),
        'limits': {
            'max_upload_bytes': MAX_BYTES,
            'max_upload_mb': MAX_BYTES // (1024 * 1024),
            'max_queue': JOBS.maxsize,
            'max_prompt_chars': MAX_PROMPT,
            'max_language_chars': MAX_LANGUAGE,
        },
        'features': {
            'idempotency': True,
            'manifest': True,
            'capabilities': True,
            'client_side_blocks': True,
        },
    }


def worker():
    while True:
        folder, job = JOBS.get()
        try:
            job['state'] = 'running'
            save(folder, job)
            validate_media(folder / 'input.media')
            transcribe(folder, job)
            ensure_manifest(folder, job)
        except InvalidMedia as exc:
            logging.warning('Invalid media for %s: %s', job['id'], exc)
            job.update(state='failed', error_code='INVALID_MEDIA',
                       error='Arquivo de áudio ou vídeo inválido ou sem faixa de áudio.')
        except Exception as exc:
            logging.exception('Transcription failed: %s', job['id'])
            invalid = type(exc).__module__.split('.')[0] == 'av'
            job.update(state='failed',
                       error_code='INVALID_MEDIA' if invalid else 'TRANSCRIPTION_FAILED',
                       error=('Arquivo de áudio ou vídeo inválido ou sem faixa de áudio.'
                              if invalid else 'Falha na transcrição. Consulte o log do servidor.'))
        finally:
            (folder / 'input.media').unlink(missing_ok=True)
            save(folder, job)
            JOBS.task_done()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(CONNECTION_TIMEOUT)

    def log_message(self, fmt, *args):
        # Avoid logging query strings, filenames or user prompts.
        logging.info('%s %s', self.command, getattr(self, 'response_status', '-'))

    def reply(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_body(code, body, 'application/json; charset=utf-8')

    def fail(self, code, error_code, message):
        self.reply(code, {'schema_version': SCHEMA_VERSION, 'code': error_code,
                          'error': message, 'message': message})

    def send_body(self, code, body, content_type):
        self.response_status = code
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        if hmac.compare_digest(self.headers.get('Authorization', '').encode('utf-8'),
                               ('Bearer ' + KEY).encode('utf-8')):
            return True
        self.fail(401, 'UNAUTHORIZED', 'Chave de acesso inválida.')
        return False

    def drain(self, length):
        """Consume a body we will not store, so the client can read the reply."""
        remaining = length
        try:
            while remaining:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
        except OSError:
            pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/':
            self.send_body(200, Path(__file__).with_name('index.html').read_bytes(),
                           'text/html; charset=utf-8')
            return
        if path == '/health':
            self.reply(200, {'status': 'ok', 'schema_version': SCHEMA_VERSION})
            return
        if not self.authorized():
            return
        if path == '/capabilities':
            self.reply(200, capabilities())
            return
        parts = path.strip('/').split('/')
        if len(parts) not in (2, 3) or parts[0] != 'jobs':
            self.fail(404, 'NOT_FOUND', 'Não encontrado.')
            return
        try:
            job_id = str(UUID(parts[1]))
        except ValueError:
            self.fail(404, 'JOB_NOT_FOUND', 'Tarefa não encontrada.')
            return
        folder = DATA / job_id
        try:
            job = load_job(folder)
        except (OSError, ValueError):
            self.fail(404, 'JOB_NOT_FOUND', 'Tarefa não encontrada.')
            return
        job.setdefault('schema_version', SCHEMA_VERSION)
        if len(parts) == 2:
            self.reply(200, job)
            return
        fmt = parts[2]
        if fmt not in ('txt', 'jsonl', 'manifest'):
            self.fail(404, 'NOT_FOUND', 'Não encontrado.')
            return
        if job['state'] != 'completed':
            self.fail(409, 'JOB_NOT_READY', 'Resultado ainda não está disponível.')
            return
        if fmt == 'manifest':
            self.reply(200, ensure_manifest(folder, job))
            return
        try:
            self.send_body(200, (folder / ('transcript.' + fmt)).read_bytes(),
                           'text/plain; charset=utf-8')
        except OSError:
            self.fail(404, 'NOT_FOUND', 'Resultado não encontrado.')
            return

    def do_POST(self):
        if not self.authorized():
            return
        parsed = urlparse(self.path)
        if parsed.path != '/jobs':
            self.fail(404, 'NOT_FOUND', 'Não encontrado.')
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if self.headers.get('Transfer-Encoding') or length <= 0:
            self.fail(400, 'INVALID_REQUEST',
                      'Envie Content-Length e o arquivo no corpo binário.')
            return
        if length > MAX_BYTES:
            self.fail(413, 'PAYLOAD_TOO_LARGE',
                      'Arquivo acima do limite configurado ({} MB).'.format(MAX_BYTES // (1024 * 1024)))
            return
        params = parse_qs(parsed.query, keep_blank_values=True)
        language = params.get('language', ['pt'])[0]
        prompt = params.get('prompt', [''])[0]
        if len(language) > MAX_LANGUAGE or len(prompt) > MAX_PROMPT:
            self.fail(400, 'INVALID_REQUEST', 'Idioma ou contexto inválido.')
            return
        key = self.headers.get('Idempotency-Key')
        if key is not None:
            key = key.strip()
            if not 1 <= len(key) <= 200 or any(ord(char) < 33 or ord(char) > 126 for char in key):
                self.fail(400, 'INVALID_REQUEST', 'Cabeçalho Idempotency-Key inválido.')
                return
            replay = find_replay(key)
            if replay is not None:
                self.drain(length)
                self.reply(200, job_summary(replay, replay=True))
                return
        if JOBS.full():
            self.drain(length)
            self.fail(429, 'QUEUE_FULL', 'Fila cheia. Tente novamente mais tarde.')
            return
        digest = idempotency_digest(key) if key else None
        if digest:
            with LOCK:
                conflict = digest in INFLIGHT
                if not conflict:
                    INFLIGHT[digest] = None
            if conflict:
                self.drain(length)
                self.fail(409, 'IDEMPOTENCY_CONFLICT',
                          'Outra requisição com a mesma chave está em andamento.')
                return
        job_id = str(uuid4())
        folder = DATA / job_id
        job = {'schema_version': SCHEMA_VERSION, 'id': job_id, 'state': 'uploading',
               'language': language, 'requested_language': language, 'prompt': prompt,
               'created_at': round(time.time(), 3), 'processed_seconds': 0}
        try:
            # Recheck after reserving the key: another upload may have finished
            # between the first lookup and our reservation.
            if key:
                replay = find_replay(key)
                if replay is not None:
                    self.drain(length)
                    self.reply(200, job_summary(replay, replay=True))
                    return
            folder.mkdir()
            save(folder, job)
            remaining = length
            with (folder / 'input.media').open('wb') as output:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise OSError('Incomplete upload')
                    output.write(chunk)
                    remaining -= len(chunk)
            job['state'] = 'queued'
            save(folder, job)
            JOBS.put_nowait((folder, job))
            if key:
                remember(key, job_id)
        except Full:
            (folder / 'input.media').unlink(missing_ok=True)
            job.update(state='failed', error_code='QUEUE_FULL',
                       error='Fila cheia. Envie novamente mais tarde.')
            save(folder, job)
            self.fail(429, 'QUEUE_FULL', job['error'])
            return
        except OSError:
            (folder / 'input.media').unlink(missing_ok=True)
            job.update(state='failed', error_code='UPLOAD_INTERRUPTED',
                       error='Upload interrompido. Envie novamente.')
            try:
                save(folder, job)
            except OSError:
                logging.exception('Could not persist failed upload for %s', job_id)
            self.fail(503, 'UPLOAD_INTERRUPTED', job['error'])
            return
        finally:
            if digest:
                with LOCK:
                    INFLIGHT.pop(digest, None)
        self.reply(202, job_summary(job))


def main():
    if len(KEY) < 24:
        raise SystemExit('Defina TRANSCRIPTION_API_KEY com pelo menos 24 caracteres.')
    DATA.mkdir(parents=True, exist_ok=True)
    IDEMPOTENCY.mkdir(parents=True, exist_ok=True)
    for status in DATA.glob('*/status.json'):
        job = json.loads(status.read_text(encoding='utf-8'))
        if job['state'] in ('uploading', 'queued', 'running'):
            job.update(state='failed', error_code='JOB_INTERRUPTED',
                       error='Servidor reiniciado. Envie o arquivo novamente.')
            (status.parent / 'input.media').unlink(missing_ok=True)
            save(status.parent, job)
    logging.basicConfig(level=logging.INFO)
    threading.Thread(target=worker, daemon=True).start()
    server = ThreadingHTTPServer((os.environ.get('HOST', '127.0.0.1'),
                                  int(os.environ.get('PORT', '8000'))), Handler)
    server.daemon_threads = True
    logging.info('Listening on %s:%s', *server.server_address)
    server.serve_forever()


if __name__ == '__main__':
    main()
