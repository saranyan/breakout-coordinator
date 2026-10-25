"""Strict access-map resolution. Credentials remain environment references."""
import copy
from pathlib import Path
import tomllib
from urllib.parse import urlparse
from .library import Library
from .protocol import digest


def check_keys(value, allowed, label):
    extra = set(value) - set(allowed)
    if extra:
        raise ValueError(f'Unsupported {label} options: {", ".join(sorted(extra))}')


def connection(spec):
    check_keys(spec, ('adapter', 'auth', 'base_url', 'api_key_env', 'data_policy', 'supported_options'), 'connection')
    adapter = spec.get('adapter')
    if adapter not in ('codex_cli', 'claude_cli', 'openai_chat'):
        raise ValueError('Supported adapters: codex_cli, claude_cli, openai_chat')
    if adapter == 'openai_chat':
        parsed = urlparse(spec.get('base_url', ''))
        if (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1'))
                or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('API base_url needs HTTPS (HTTP allowed only on loopback), without credentials/query')
        if not isinstance(spec.get('api_key_env', ''), str):
            raise ValueError('api_key_env must name an environment variable')
        import re
        if spec.get('api_key_env') and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', spec['api_key_env']):
            raise ValueError('api_key_env must be an environment variable name, not a credential value')
        if spec.get('data_policy', 'ask_before_first_send') not in ('ask_before_first_send', 'local_only'):
            raise ValueError('Unsupported data policy')
        if spec.get('data_policy') == 'local_only' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('local_only connection must use a loopback endpoint')
        options = spec.get('supported_options', [])
        if not isinstance(options, list) or set(options) - {'reasoning_effort', 'max_tokens', 'response_format'}:
            raise ValueError('Unsupported declared API options')
    elif any(k in spec for k in ('base_url', 'api_key_env', 'supported_options')):
        raise ValueError('API settings cannot be used on a CLI connection')
    return copy.deepcopy(spec)


def api_options(participant):
    conn = connection(participant.get('connection', {}))
    if conn['adapter'] != 'openai_chat':
        raise ValueError('API member requires openai_chat connection')
    options = set(conn.get('supported_options', []))
    for field, option in (('effort', 'reasoning_effort'), ('max_output_tokens', 'max_tokens'), ('json_mode', 'response_format')):
        if participant.get(field) and option not in options:
            raise ValueError('Endpoint must declare support for ' + option)
    if participant.get('max_output_tokens') is not None and (not isinstance(participant['max_output_tokens'], int) or participant['max_output_tokens'] <= 0):
        raise ValueError('max_output_tokens must be a positive integer')
    if participant.get('effort') and participant['effort'] not in ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
        raise ValueError('Unsupported API reasoning effort')
    return conn


def resolve(path, team, project):
    raw = Path(path).read_text()
    config = tomllib.loads(raw)
    check_keys(config, ('version', 'connections', 'profiles', 'personalities', 'teams', 'defaults'), 'config')
    if config.get('version', 2) != 2:
        raise ValueError('Access map must have version = 2')
    recipe = config.get('teams', {}).get(team)
    if not recipe:
        raise ValueError('Unknown team recipe: ' + team)
    check_keys(recipe, ('members', 'rounds', 'wall_clock_minutes', 'review_replies', 'close_after_hours'), 'team')
    members = []
    lib = Library(project)
    for member in recipe.get('members', []):
        check_keys(member, ('name', 'profile', 'personality', 'role'), 'member')
        profile = config.get('profiles', {}).get(member.get('profile'))
        if not profile:
            raise ValueError('Unknown model profile')
        check_keys(profile, ('connection', 'model', 'effort', 'max_output_tokens', 'json_mode'), 'profile')
        conn_name = profile['connection']
        conn = connection(config['connections'][conn_name])
        personality_name = member.get('personality', '')
        card = None
        if personality_name:
            if lib.path('personalities', personality_name).exists():
                card = next((r for r in lib.active('personalities') if r['metadata']['name'] == personality_name), None)
                if card is None:
                    raise ValueError('Personality is not active: ' + personality_name)
                personality = card['text']
            else:
                personality_spec = config.get('personalities', {}).get(personality_name)
                if not personality_spec:
                    raise ValueError('Unknown personality: ' + personality_name)
                check_keys(personality_spec, ('instructions',), 'personality')
                personality = personality_spec['instructions']
        else:
            personality = ''
        provider = {'codex_cli': 'codex', 'claude_cli': 'claude', 'openai_chat': 'api'}[conn['adapter']]
        agent = dict(provider=provider, model=profile.get('model'), effort=profile.get('effort'),
                     name=member['name'], role=member.get('role', 'reviewer'), personality=personality,
                     connection=conn, connection_name=conn_name, personality_card=card)
        if provider == 'api':
            opts = set(conn.get('supported_options', []))
            if profile.get('effort') and 'reasoning_effort' not in opts:
                raise ValueError('Endpoint must declare reasoning_effort support')
            if profile.get('max_output_tokens') and 'max_tokens' not in opts:
                raise ValueError('Endpoint must declare max_tokens support')
            if profile.get('json_mode') and 'response_format' not in opts:
                raise ValueError('Endpoint must declare response_format support')
            agent['max_output_tokens'] = profile.get('max_output_tokens')
            agent['json_mode'] = profile.get('json_mode', False)
        elif any(k in profile for k in ('max_output_tokens', 'json_mode')):
            raise ValueError('CLI profile cannot set API-only options')
        members.append(agent)
    defaults = config.get('defaults', {})
    check_keys(defaults, ('rounds', 'wall_clock_minutes', 'review_replies', 'close_after_hours', 'context_bytes'), 'defaults')
    limits = dict(defaults, **{k: v for k, v in recipe.items() if k != 'members'})
    return dict(agents=members, limits=limits, recipe=team, config_hash=digest(raw), config_path=str(Path(path).resolve()))
