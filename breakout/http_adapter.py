"""OpenAI-compatible chat completions, without tools or implicit retries."""
import json
import os
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .config import connection
from .protocol import prompt, validate
from .providers import Interrupted
from .store import uid


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('API redirects are not followed; configure the final endpoint explicitly')


class HTTPAdapter:
    def request(self, participant, suffix, payload=None, timeout=20):
        conn = connection(participant['connection'])
        headers = {'Content-Type': 'application/json', 'User-Agent': 'breakout/0.2'}
        env = conn.get('api_key_env')
        if env:
            key = os.environ.get(env)
            if not key:
                raise ValueError('API credential environment variable is not set: ' + env)
            headers['Authorization'] = 'Bearer ' + key
        req = Request(conn['base_url'].rstrip('/') + suffix,
                      data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            with build_opener(NoRedirect()).open(req, timeout=min(timeout, 60)) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('API response exceeds size limit')
                return json.loads(raw)
        except (OSError, ValueError) as exc:
            # Never store provider error bodies or authorization headers.
            raise ValueError('API request failed; check endpoint, model, authentication and options locally') from None

    def preflight(self, participant):
        result = self.request(participant, '/models')
        models = [item.get('id') for item in result.get('data', [])]
        if participant['model'] not in models:
            raise ValueError('Selected model is not advertised by endpoint /models')
        return {'model_advertised': True, 'options': 'declared by configuration; rejected options fail without fallback'}

    def turn(self, participant, context, cancel, timeout):
        if cancel.is_set():
            raise Interrupted('Cancelled before dispatch')
        history = list(participant.get('api_history', []))
        history.append({'role': 'user', 'content': prompt(context)})
        payload = dict(model=participant['model'], messages=history, stream=False)
        if participant.get('effort'):
            payload['reasoning_effort'] = participant['effort']
        if participant.get('max_output_tokens'):
            payload['max_tokens'] = participant['max_output_tokens']
        if participant.get('json_mode'):
            payload['response_format'] = {'type': 'json_object'}
        result = self.request(participant, '/chat/completions', payload, timeout)
        choice = result['choices'][0]
        if choice.get('finish_reason') not in ('stop', None) or choice['message'].get('tool_calls'):
            raise ValueError('API did not finish a plain-text discussion response')
        envelope = validate(json.loads(choice['message']['content']))
        # Optional usage metadata is separate from the strict response schema.
        participant['_usage'] = result.get('usage')
        return participant.get('child_id') or 'api-' + uid(), envelope
