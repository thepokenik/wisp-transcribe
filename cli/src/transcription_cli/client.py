"""HTTP client with stable errors and exit codes for agent consumption."""
import json
import os
from pathlib import Path
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

SCHEMA_VERSION = 1

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_CONFIG = 2
EXIT_AUTH = 3
EXIT_UNAVAILABLE = 4
EXIT_TIMEOUT = 5
EXIT_TRANSCRIPTION = 6
EXIT_NOT_FOUND = 7
EXIT_CONFLICT = 8


class CliError(Exception):
    def __init__(self, code, message, exit_code, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.detail = detail or {}


def exit_code_for(code, status):
    if code == 'UNAUTHORIZED' or status == 401:
        return EXIT_AUTH
    if code in ('JOB_NOT_FOUND', 'NOT_FOUND') and status == 404:
        return EXIT_NOT_FOUND
    if code in ('INVALID_REQUEST', 'PAYLOAD_TOO_LARGE'):
        return EXIT_CONFIG
    if code in ('TRANSCRIPTION_FAILED', 'INVALID_MEDIA', 'JOB_INTERRUPTED'):
        return EXIT_TRANSCRIPTION
    if code == 'IDEMPOTENCY_CONFLICT':
        return EXIT_CONFLICT
    if code in ('QUEUE_FULL', 'JOB_NOT_READY', 'UPLOAD_INTERRUPTED',
                'SERVICE_UNAVAILABLE', 'INVALID_RESPONSE', 'TIMEOUT'):
        return EXIT_UNAVAILABLE
    if status >= 500 or status in (409, 429):
        return EXIT_UNAVAILABLE
    if status >= 400:
        return EXIT_CONFIG
    return EXIT_INTERNAL


def safe_url(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if parsed.port:
        host += ':' + str(parsed.port)
    return urlunsplit((parsed.scheme, host, parsed.path, '', ''))


class Api:
    def __init__(self, base_url, api_key, timeout=60.0, upload_timeout=3600.0):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.upload_timeout = upload_timeout

    @property
    def display_url(self):
        return safe_url(self.base_url)

    def _url(self, path, query=None):
        url = self.base_url + path
        if query:
            url += '?' + urlencode(query)
        return url

    def _headers(self, extra=None):
        headers = {'Authorization': 'Bearer ' + self.api_key, 'Accept': 'application/json'}
        if extra:
            headers.update(extra)
        return headers

    def _error(self, status, raw):
        try:
            body = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            body = {}
        code = body.get('code') or ('NOT_FOUND' if status == 404 else 'HTTP_' + str(status))
        message = (body.get('message') or body.get('error')
                   or 'O serviço respondeu com HTTP {}.'.format(status))
        return CliError(code, message, exit_code_for(code, status))

    def _request(self, method, path, query=None, body=None, headers=None, timeout=None):
        request = Request(self._url(path, query), data=body, method=method,
                          headers=self._headers(headers))
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                return response.status, response.read()
        except HTTPError as exc:
            raise self._error(exc.code, exc.read()) from None
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, 'reason', exc)
            timed_out = isinstance(reason, (TimeoutError, socket.timeout))
            raise CliError('TIMEOUT' if timed_out else 'SERVICE_UNAVAILABLE',
                           'Tempo esgotado ao falar com {}.'.format(self.display_url) if timed_out
                           else 'Serviço indisponível em {}: {}'.format(self.display_url, reason),
                           EXIT_TIMEOUT if timed_out else EXIT_UNAVAILABLE) from None

    def _json(self, method, path, query=None, body=None, headers=None, timeout=None):
        status, raw = self._request(method, path, query, body, headers, timeout)
        try:
            return json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise CliError('INVALID_RESPONSE',
                           'Resposta do serviço não é JSON válido (HTTP {}).'.format(status),
                           EXIT_UNAVAILABLE) from None

    def capabilities(self):
        return self._json('GET', '/capabilities')

    def submit(self, path, language, prompt, idempotency_key=None):
        path = Path(path)
        size = path.stat().st_size
        headers = {'Content-Type': 'application/octet-stream', 'Content-Length': str(size)}
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        with path.open('rb') as media:
            return self._json('POST', '/jobs',
                              query={'language': language, 'prompt': prompt},
                              body=media, headers=headers, timeout=self.upload_timeout)

    def job(self, job_id):
        return self._json('GET', '/jobs/' + job_id)

    def manifest(self, job_id):
        return self._json('GET', '/jobs/' + job_id + '/manifest')

    def download(self, job_id, fmt, destination):
        destination = Path(destination)
        temporary = destination.with_name(destination.name + '.part')
        request = Request(self._url('/jobs/' + job_id + '/' + fmt),
                          headers=self._headers({'Accept': 'text/plain'}))
        try:
            with urlopen(request, timeout=self.timeout) as response, \
                 temporary.open('wb') as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
        except HTTPError as exc:
            temporary.unlink(missing_ok=True)
            raise self._error(exc.code, exc.read()) from None
        except (URLError, TimeoutError, OSError) as exc:
            temporary.unlink(missing_ok=True)
            reason = getattr(exc, 'reason', exc)
            timed_out = isinstance(reason, (TimeoutError, socket.timeout))
            raise CliError('TIMEOUT' if timed_out else 'SERVICE_UNAVAILABLE',
                           'Falha ao baixar {} de {}: {}'.format(fmt, self.display_url, reason),
                           EXIT_TIMEOUT if timed_out else EXIT_UNAVAILABLE) from None
        temporary.replace(destination)


def file_identity(path, language, prompt):
    """Derive a stable idempotency key from content and transcription options."""
    import hashlib
    digest = hashlib.sha256()
    with open(path, 'rb') as media:
        for chunk in iter(lambda: media.read(1024 * 1024), b''):
            digest.update(chunk)
    options = hashlib.sha256(
        json.dumps([language, prompt], ensure_ascii=False).encode('utf-8')).hexdigest()
    return 'auto-{}-{}'.format(digest.hexdigest(), options[:16])


def utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def resolve_key():
    return os.environ.get('TRANSCRIPTION_API_KEY', '').strip()
