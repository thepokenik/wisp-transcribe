"""Agent-facing `transcribe` command: submit, wait, fetch and inspect jobs."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import math
from urllib.parse import urlsplit

from . import __version__
from .blocks import write_blocks
from .client import (Api, CliError, SCHEMA_VERSION, EXIT_CONFIG,
                     EXIT_INTERNAL, EXIT_TIMEOUT, EXIT_TRANSCRIPTION,
                     EXIT_UNAVAILABLE, file_identity, resolve_key, safe_url,
                     utc_now)

EXIT_CODES = """Códigos de saída:
  0 sucesso
  2 configuração, argumentos ou arquivo inválido
  3 autenticação recusada
  4 serviço indisponível, fila cheia ou contrato incompatível
  5 tempo de espera esgotado (a tarefa continua no servidor)
  6 falha de transcrição ou mídia inválida
  7 tarefa não encontrada
  8 conflito de idempotência (outra requisição em andamento)
  1 erro inesperado do cliente"""


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def log(message):
    print('transcribe: ' + message, file=sys.stderr, flush=True)


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError, OSError):
            pass


def add_common(parser):
    parser.add_argument('--json', action='store_true',
                        help='Escreve um único objeto JSON no stdout; progresso vai para stderr.')
    parser.add_argument('--url', default=os.environ.get('TRANSCRIPTION_URL', 'http://127.0.0.1:8000'),
                        help='URL do serviço (padrão: TRANSCRIPTION_URL).')
    parser.add_argument('--http-timeout', type=float,
                        default=env_float('TRANSCRIPTION_HTTP_TIMEOUT', 60),
                        help='Timeout em segundos para consultas e downloads.')
    parser.add_argument('--upload-timeout', type=float,
                        default=env_float('TRANSCRIPTION_UPLOAD_TIMEOUT', 3600),
                        help='Timeout em segundos para o upload do arquivo.')


def add_idempotency(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--idempotency-key',
                       help='Chave explícita para repetir o envio sem duplicar a tarefa.')
    group.add_argument('--no-idempotency', action='store_true',
                       help='Não envia chave de idempotência (permite tarefas duplicadas).')


class AgentParser(argparse.ArgumentParser):
    def error(self, message):
        raise CliError('CONFIG_INVALID', message, EXIT_CONFIG)


def build_parser():
    parser = AgentParser(
        prog='transcribe', description='CLI do serviço de transcrição, com saída estável para agentes.',
        epilog=EXIT_CODES, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', action='version', version='transcribe ' + __version__)
    subparsers = parser.add_subparsers(dest='command', required=True)

    run = subparsers.add_parser('run', help='Envia, aguarda e baixa a transcrição.')
    add_common(run)
    add_idempotency(run)
    run.add_argument('file', help='Áudio ou vídeo a transcrever.')
    run.add_argument('--language', default='pt', help='Idioma; vazio para detectar (padrão: pt).')
    run.add_argument('--prompt', default='', help='Contexto com nomes e termos esperados.')
    run.add_argument('--output', default='./resultado', help='Diretório dos resultados.')
    run.add_argument('--block-seconds', type=int,
                     default=env_int('TRANSCRIPTION_BLOCK_SECONDS', 0),
                     help='Divide a saída em blocos com essa duração (0 desativa).')
    run.add_argument('--timeout', type=float,
                     default=env_float('TRANSCRIPTION_WAIT_TIMEOUT', 1800),
                     help='Espera máxima em segundos pela conclusão.')
    run.add_argument('--poll-interval', type=float,
                     default=env_float('TRANSCRIPTION_POLL_INTERVAL', 3),
                     help='Intervalo de consulta do estado em segundos.')

    submit = subparsers.add_parser('submit', help='Envia arquivo sem aguardar.')
    add_common(submit)
    add_idempotency(submit)
    submit.add_argument('file')
    submit.add_argument('--language', default='pt')
    submit.add_argument('--prompt', default='')

    status = subparsers.add_parser('status', help='Consulta o estado de uma tarefa.')
    add_common(status)
    status.add_argument('job_id')

    wait = subparsers.add_parser('wait', help='Aguarda a conclusão de uma tarefa.')
    add_common(wait)
    wait.add_argument('job_id')
    wait.add_argument('--timeout', type=float,
                      default=env_float('TRANSCRIPTION_WAIT_TIMEOUT', 1800))
    wait.add_argument('--poll-interval', type=float,
                      default=env_float('TRANSCRIPTION_POLL_INTERVAL', 3))

    fetch = subparsers.add_parser('fetch', help='Baixa resultados de uma tarefa concluída.')
    add_common(fetch)
    fetch.add_argument('job_id')
    fetch.add_argument('--output', default='./resultado')
    fetch.add_argument('--block-seconds', type=int,
                       default=env_int('TRANSCRIPTION_BLOCK_SECONDS', 0))

    doctor = subparsers.add_parser('doctor', help='Verifica configuração, autenticação e contrato.')
    add_common(doctor)
    return parser


def require_file(value):
    path = Path(value).expanduser()
    if not path.is_file():
        raise CliError('CONFIG_INVALID', 'Arquivo não encontrado: {}'.format(path), EXIT_CONFIG)
    if path.stat().st_size == 0:
        raise CliError('CONFIG_INVALID', 'Arquivo vazio: {}'.format(path), EXIT_CONFIG)
    return path


def validate_url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise CliError('CONFIG_INVALID',
                       'TRANSCRIPTION_URL deve ser uma URL http(s): {}'.format(value), EXIT_CONFIG)
    return value


def build_api(args):
    key = resolve_key()
    if not key:
        raise CliError('CONFIG_INVALID',
                       'Defina TRANSCRIPTION_API_KEY no ambiente.', EXIT_CONFIG)
    return Api(validate_url(args.url), key, timeout=args.http_timeout,
               upload_timeout=args.upload_timeout)


def idempotency_for(args, path, language, prompt):
    if args.no_idempotency:
        return None
    if args.idempotency_key:
        key = args.idempotency_key.strip()
        if not 1 <= len(key) <= 200 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise CliError('CONFIG_INVALID',
                           '--idempotency-key deve ter entre 1 e 200 caracteres.', EXIT_CONFIG)
        return key
    return file_identity(path, language, prompt)


def read_records(path):
    records = []
    try:
        with Path(path).open(encoding='utf-8') as source:
            for number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                record = json.loads(line)
                if not isinstance(record, dict) or not all(
                        field in record for field in ('start', 'end', 'text')):
                    raise ValueError('campos ausentes')
                records.append(record)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CliError('INVALID_RESPONSE',
                       'JSONL inválido em {} ({}).'.format(path, exc), EXIT_UNAVAILABLE) from None
    return records


def write_json_atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.part')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def job_failure(job):
    code = job.get('error_code') or 'TRANSCRIPTION_FAILED'
    message = job.get('error') or 'A transcrição falhou.'
    return CliError(code, message, EXIT_TRANSCRIPTION,
                    {'job_id': job.get('id'), 'state': job.get('state'), 'error_code': code})


def wait_job(api, job_id, timeout, poll_interval):
    if timeout < 0:
        raise CliError('CONFIG_INVALID', '--timeout não pode ser negativo.', EXIT_CONFIG)
    if poll_interval <= 0:
        raise CliError('CONFIG_INVALID', '--poll-interval deve ser positivo.', EXIT_CONFIG)
    deadline = time.monotonic() + timeout
    last_state = None
    while True:
        try:
            job = api.job(job_id)
        except CliError as exc:
            exc.detail.update(job_id=job_id, resume='transcribe wait {} --json'.format(job_id))
            raise
        state = job.get('state')
        if state != last_state:
            log('Tarefa {}: {}'.format(job_id, state))
            last_state = state
        if state == 'completed':
            return job
        if state == 'failed':
            raise job_failure(job)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CliError(
                'TIMEOUT',
                'Tempo de espera esgotado. A tarefa continua no servidor em {}.'.format(state or '?'),
                EXIT_TIMEOUT,
                {'job_id': job_id, 'state': state,
                 'resume': 'transcribe wait {} --json'.format(job_id)})
        time.sleep(min(poll_interval, remaining))


def fetch_result(api, job_id, output, block_seconds):
    if block_seconds < 0:
        raise CliError('CONFIG_INVALID', '--block-seconds não pode ser negativo.', EXIT_CONFIG)
    job = api.job(job_id)
    state = job.get('state')
    if state == 'failed':
        raise job_failure(job)
    if state != 'completed':
        raise CliError('JOB_NOT_READY',
                       'A tarefa ainda está em {}; aguarde a conclusão.'.format(state),
                       EXIT_UNAVAILABLE, {'job_id': job_id, 'state': state})
    manifest = api.manifest(job_id)
    output = Path(output).expanduser()
    output.mkdir(parents=True, exist_ok=True)
    log('Baixando resultados da tarefa {}...'.format(job_id))
    api.download(job_id, 'txt', output / 'transcript.txt')
    api.download(job_id, 'jsonl', output / 'transcript.jsonl')
    records = read_records(output / 'transcript.jsonl')
    blocks = write_blocks(records, output, block_seconds)
    files = {
        'text': str((output / 'transcript.txt').resolve()),
        'segments': str((output / 'transcript.jsonl').resolve()),
        'manifest': str((output / 'manifest.json').resolve()),
    }
    local_manifest = dict(manifest)
    local_manifest.update({
        'fetched_at': utc_now(),
        'source': {'url': api.display_url, 'job_id': job_id},
        'files': files,
        'blocks': blocks,
    })
    write_json_atomic(output / 'manifest.json', local_manifest)
    return job, files, blocks, local_manifest


def handle_submit(args):
    api = build_api(args)
    path = require_file(args.file)
    key = idempotency_for(args, path, args.language, args.prompt)
    log('Enviando {}...'.format(path.name))
    response = api.submit(path, args.language, args.prompt, key)
    payload = {'schema_version': SCHEMA_VERSION, 'ok': True,
               'job_id': response['id'], 'state': response.get('state', 'queued')}
    if response.get('replayed'):
        payload['replayed'] = True
    return payload


def handle_status(args):
    api = build_api(args)
    job = api.job(args.job_id)
    return {'schema_version': SCHEMA_VERSION, 'ok': True, 'job_id': job.get('id'),
            'state': job.get('state'), 'job': job}


def handle_wait(args):
    api = build_api(args)
    job = wait_job(api, args.job_id, args.timeout, args.poll_interval)
    return {'schema_version': SCHEMA_VERSION, 'ok': True, 'job_id': job.get('id'),
            'state': job.get('state'), 'job': job}


def handle_fetch(args):
    api = build_api(args)
    job, files, blocks, manifest = fetch_result(api, args.job_id, args.output, args.block_seconds)
    return {'schema_version': SCHEMA_VERSION, 'ok': True, 'job_id': job.get('id'),
            'state': 'completed', 'files': files, 'blocks': blocks,
            'metadata': result_metadata(manifest)}


def handle_run(args):
    api = build_api(args)
    path = require_file(args.file)
    key = idempotency_for(args, path, args.language, args.prompt)
    log('Enviando {}...'.format(path.name))
    response = api.submit(path, args.language, args.prompt, key)
    job_id = response['id']
    log('Tarefa {} em {}.'.format(job_id, response.get('state', 'queued')))
    job = wait_job(api, job_id, args.timeout, args.poll_interval)
    try:
        job, files, blocks, manifest = fetch_result(api, job_id, args.output, args.block_seconds)
    except CliError as exc:
        exc.detail.update(job_id=job_id, resume='transcribe fetch {} --json'.format(job_id))
        raise
    payload = {'schema_version': SCHEMA_VERSION, 'ok': True, 'job_id': job_id,
               'state': 'completed', 'files': files, 'blocks': blocks,
               'metadata': result_metadata(manifest)}
    if response.get('replayed'):
        payload['replayed'] = True
    return payload


def result_metadata(manifest):
    return {key: manifest.get(key) for key in
            ('language', 'requested_language', 'duration_seconds', 'model',
             'segment_count', 'text_characters', 'options')}


def doctor(args):
    checks = []
    key = resolve_key()
    checks.append({'name': 'config.api_key', 'ok': bool(key),
                   'detail': 'TRANSCRIPTION_API_KEY definida' if key
                   else 'TRANSCRIPTION_API_KEY ausente'})
    try:
        url = validate_url(args.url)
        url_ok = True
        checks.append({'name': 'config.url', 'ok': True, 'detail': url})
    except CliError as exc:
        url = args.url
        url_ok = False
        checks.append({'name': 'config.url', 'ok': False, 'detail': exc.message})
    base = {'schema_version': SCHEMA_VERSION, 'cli_version': __version__,
            'base_url': safe_url(url) if url_ok else url, 'checks': checks}
    if not key or not url_ok:
        base.update(ok=False, error={'code': 'CONFIG_INVALID',
                                     'message': 'Corrija a configuração antes de usar a CLI.'})
        return base, EXIT_CONFIG
    api = Api(url, key, timeout=args.http_timeout, upload_timeout=args.upload_timeout)
    try:
        capabilities = api.capabilities()
    except CliError as exc:
        checks.append({'name': 'service.reachable', 'ok': False, 'detail': exc.message})
        base.update(ok=False, capabilities=None,
                    error={'code': exc.code, 'message': exc.message})
        return base, exc.exit_code
    checks.append({'name': 'service.reachable', 'ok': True,
                   'detail': '{} (modelo {})'.format(capabilities.get('service', 'serviço'),
                                                    capabilities.get('model', '?'))})
    compatible = capabilities.get('schema_version') == SCHEMA_VERSION
    checks.append({'name': 'contract.schema', 'ok': compatible,
                   'detail': 'schema_version={} (CLI espera {})'.format(
                       capabilities.get('schema_version'), SCHEMA_VERSION)})
    formats = capabilities.get('output_formats') or []
    formats_ok = all(fmt in formats for fmt in ('txt', 'jsonl', 'manifest'))
    checks.append({'name': 'outputs.manifest', 'ok': formats_ok,
                   'detail': 'formatos: {}'.format(', '.join(formats) or 'nenhum')})
    ok = all(check['ok'] for check in checks)
    base.update(ok=ok, capabilities=capabilities)
    if not compatible:
        base['error'] = {'code': 'INCOMPATIBLE_CONTRACT',
                         'message': 'O contrato do serviço não é compatível com esta CLI.'}
        return base, EXIT_UNAVAILABLE
    if not formats_ok:
        base['error'] = {'code': 'INCOMPLETE_SERVICE',
                         'message': 'O serviço não oferece os formatos de saída esperados.'}
        return base, EXIT_UNAVAILABLE
    return base, 0


def handle(args):
    if args.command == 'doctor':
        return doctor(args)
    handlers = {'run': handle_run, 'submit': handle_submit, 'status': handle_status,
                'wait': handle_wait, 'fetch': handle_fetch}
    return handlers[args.command](args)


def human(payload):
    if 'checks' in payload:
        print('Serviço: {}'.format(payload.get('base_url')))
        for check in payload['checks']:
            print('[{}] {}: {}'.format('ok' if check['ok'] else 'falha',
                                       check['name'], check['detail']))
        return
    if payload.get('job') is not None:
        job = payload['job']
        print('ID: {}'.format(job.get('id')))
        print('Estado: {}'.format(job.get('state')))
        print('Áudio processado: {}s'.format(job.get('processed_seconds', 0)))
        if job.get('result'):
            print('Idioma: {}'.format(job['result'].get('language')))
            print('Duração: {}s'.format(job['result'].get('duration_seconds')))
        if job.get('error'):
            print('Erro ({}): {}'.format(job.get('error_code', '?'), job.get('error')))
        return
    if payload.get('files'):
        print('Tarefa {} concluída.'.format(payload.get('job_id')))
        print('Texto: {}'.format(payload['files']['text']))
        print('Segmentos: {}'.format(payload['files']['segments']))
        print('Manifesto: {}'.format(payload['files']['manifest']))
        if payload.get('blocks'):
            print('Blocos: {} em {}'.format(len(payload['blocks']), Path(payload['blocks'][0]['text']).parent))
        return
    if payload.get('job_id'):
        print('Tarefa enviada: {} ({})'.format(payload['job_id'], payload.get('state')))
        if payload.get('replayed'):
            print('Tarefa já existente para esta chave de idempotência.')


def main(argv=None):
    configure_stdio()
    parser = build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    json_mode = '--json' in raw_args
    try:
        args = parser.parse_args(raw_args)
        for name in ('http_timeout', 'upload_timeout', 'poll_interval', 'timeout'):
            value = getattr(args, name, None)
            if value is not None and (not math.isfinite(value) or value < 0 or
                                      (name != 'timeout' and value == 0)):
                raise CliError('CONFIG_INVALID', 'Valor inválido para --' + name.replace('_', '-'), EXIT_CONFIG)
        if getattr(args, 'block_seconds', 0) < 0:
            raise CliError('CONFIG_INVALID', '--block-seconds não pode ser negativo.', EXIT_CONFIG)
        result = handle(args)
    except CliError as exc:
        payload = {'schema_version': SCHEMA_VERSION, 'ok': False,
                   'error': {'code': exc.code, 'message': exc.message}}
        payload.update(exc.detail)
        if json_mode:
            emit(payload)
        else:
            log('{}: {}'.format(exc.code, exc.message))
        return exc.exit_code
    except KeyboardInterrupt:
        log('Interrompido. A tarefa pode continuar no servidor; use status/wait/fetch com o ID.')
        return 130
    except Exception as exc:  # Keep agent-facing output parseable.
        if json_mode:
            emit({'schema_version': SCHEMA_VERSION, 'ok': False,
                  'error': {'code': 'INTERNAL_ERROR', 'message': str(exc)}})
        else:
            log('INTERNAL_ERROR: {}'.format(exc))
        return EXIT_INTERNAL
    payload, exit_code = result if isinstance(result, tuple) else (result, 0)
    if args.json:
        emit(payload)
    else:
        human(payload)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
