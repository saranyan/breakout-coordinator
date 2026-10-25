"""Read-only model discovery through installed provider interfaces."""
import json
import os
import re
import selectors
import subprocess
import time


def codex_models(timeout=15):
    proc = subprocess.Popen(['codex', 'app-server'], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    buffer = b''
    deadline = time.monotonic() + timeout

    def send(value):
        proc.stdin.write(json.dumps(value).encode() + b'\n')
        proc.stdin.flush()

    def receive(request_id):
        nonlocal buffer
        while time.monotonic() < deadline:
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                message = json.loads(line)
                if message.get('id') == request_id:
                    if 'error' in message:
                        raise ValueError('Codex model discovery rejected the request')
                    return message['result']
            if not selector.select(max(.01, deadline-time.monotonic())):
                break
            data = os.read(proc.stdout.fileno(), 65536)
            if not data:
                raise ValueError('Codex app-server exited during discovery')
            buffer += data
        raise ValueError('Codex model discovery timed out')

    try:
        send({'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'breakout', 'version': '0.1.0'},
            'capabilities': {'experimentalApi': False}}})
        receive(1)
        send({'method': 'initialized'})
        models, cursor, request_id = [], None, 2
        while True:
            send({'id': request_id, 'method': 'model/list', 'params': {'cursor': cursor, 'limit': 100}})
            result = receive(request_id)
            models.extend(result['data'])
            cursor = result.get('nextCursor')
            if not cursor:
                return {'source': 'installed Codex app-server model/list', 'models': models}
            request_id += 1
    finally:
        selector.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        proc.stdin.close()
        proc.stdout.close()


def models():
    result = {}
    try:
        result['codex'] = codex_models()
    except (OSError, ValueError, KeyError) as exc:
        result['codex'] = {'models': [], 'error': str(exc), 'verified': False}
    try:
        help_text = subprocess.run(['claude', '--help'], capture_output=True, text=True, timeout=5, check=True).stdout
        section = help_text.split('--model <model>', 1)[1].split('\n  -', 1)[0]
        aliases = re.findall(r"'([^']+)'", section)
        result['claude'] = {'models': aliases, 'source': 'installed CLI help aliases',
                            'account_availability_verified': False,
                            'note': 'Explicit full model IDs are also accepted; provider validates availability. No fallback is configured.'}
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        result['claude'] = {'models': [], 'error': str(exc), 'verified': False}
    return result
