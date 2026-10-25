"""Human-readable project guidance. Markdown is authoritative; revisions are immutable.

Files use TOML front matter (+++) followed by ordinary Markdown. No executable
content is loaded. A run snapshots the full body and hash, never a mutable path.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import time
import tomllib
from .files import atomic_write
from .protocol import digest


KINDS = ('learnings', 'personalities')


class Library:
    def __init__(self, project):
        self.project = Path(project).resolve()
        if not self.project.is_dir():
            raise ValueError('Project must exist')
        self.root = self.project / '.breakout'

    def path(self, kind, name):
        if kind not in KINDS or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', name):
            raise ValueError('Use learnings/personalities and a simple alphanumeric name')
        return self.root / kind / (name + '.md')

    @contextmanager
    def lock(self):
        self.root.mkdir(mode=0o700, exist_ok=True)
        with (self.root / '.lock').open('a') as stream:
            os.chmod(stream.name, 0o600)
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    @staticmethod
    def decode(text):
        if not text.startswith('+++\n') or '\n+++\n' not in text[4:]:
            raise ValueError('Guidance needs TOML front matter between +++ lines')
        header, body = text[4:].split('\n+++\n', 1)
        return dict(metadata=tomllib.loads(header), text=body, sha256=digest(text))

    @staticmethod
    def encode(meta, text):
        lines = []
        for key, value in meta.items():
            if value is not None:
                # Metadata is deliberately restricted to JSON-compatible TOML scalars/lists.
                lines.append(f'{key} = {json.dumps(value, ensure_ascii=False)}')
        return '+++\n' + '\n'.join(lines) + '\n+++\n' + text

    def get(self, kind, name, version=None):
        path = self.path(kind, name)
        if version is not None:
            path = self.root / 'history' / kind / name / f'{int(version)}.md'
        if not path.is_file():
            raise ValueError('Unknown guidance: ' + name)
        record = self.decode(path.read_text())
        record['path'] = str(path)
        return record

    def list(self, kind=None):
        result = []
        for group in ([kind] if kind else KINDS):
            if group not in KINDS:
                raise ValueError('Unknown library kind')
            for path in sorted((self.root / group).glob('*.md')):
                result.append(self.get(group, path.stem))
        return result

    def _write(self, kind, name, text, **metadata):
        path = self.path(kind, name)
        old = self.get(kind, name) if path.exists() else None
        history = self.root / 'history' / kind / name
        history.mkdir(parents=True, exist_ok=True, mode=0o700)
        versions = [int(p.stem) for p in history.glob('*.md') if p.stem.isdigit()]
        version = max(versions + [0]) + 1
        # Preserve manual edits too, without overwriting an immutable old version.
        if old and (not versions or old['sha256'] != digest((history / f'{max(versions)}.md').read_text())):
            atomic_write(history / f'{version}.md', self.encode(dict(old['metadata'], version=version, manual_edit=True), old['text']))
            version += 1
        meta = dict(name=name, kind=kind, version=version, scope=str(self.project),
                    updated=time.time(), **metadata)
        content = self.encode(meta, text)
        atomic_write(history / f'{version}.md', content)
        atomic_write(path, content)
        return self.get(kind, name)

    def save(self, kind, name, text, source='origin instruction', direct=False):
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 128000:
            raise ValueError('Supply nonempty guidance of at most 128 KB')
        with self.lock():
            old = self.get(kind, name) if self.path(kind, name).exists() else None
            previous = old['metadata'].get('version', 0) if old and old['metadata'].get('status') in ('active', 'trial') else (old or {}).get('metadata', {}).get('previous', 0)
            return self._write(kind, name, text, source=source, status='active' if direct else 'candidate',
                               direct=bool(direct), previous=previous)

    def preview(self, name, source):
        path = Path(source).resolve()
        text = path.read_text()
        if len(text.encode()) > 128000:
            raise ValueError('Soul file exceeds 128 KB')
        traits, flagged = [], []
        pattern = re.compile(r'ignore.*(rules|instructions)|permission|credential|secret|publish|execute|shell|send.*(email|slack)', re.I)
        for number, line in enumerate(text.splitlines(), 1):
            if line.strip():
                item = dict(text=line, label='source', source=f'{path}:{number}')
                (flagged if pattern.search(line) else traits).append(item)
        return dict(name=name, source=str(path), source_sha256=digest(text), traits=traits, flagged=flagged,
                    note='Preview only. Review source and infer thinking traits in the origin; save the edited card explicitly. Filtering is a warning aid, not a security boundary.')

    def evaluate(self, kind, name, evidence):
        """Record human/provider evidence, never invent a score from agent agreement."""
        required = ('complaint_fixed', 'unseen', 'hard_constraints_lost', 'time_compared', 'cost_impact', 'evidence')
        if not isinstance(evidence, dict) or any(k not in evidence for k in required):
            raise ValueError('Evaluation requires ' + ', '.join(required))
        if 'ILLUSTRATIVE ONLY' in json.dumps(evidence) or 'REPLACE:' in json.dumps(evidence):
            raise ValueError('Example placeholders are not evaluation evidence')
        unseen = evidence['unseen']
        passed = (evidence['complaint_fixed'] is True and isinstance(unseen, list) and len(unseen) >= 3
                  and any(x.get('preference') == 'new' for x in unseen)
                  and all(x.get('blind') is True and x.get('preference') in ('new', 'tie', 'old')
                          and x.get('major_regression') is False and x.get('reference') for x in unseen)
                  and evidence['hard_constraints_lost'] == 0 and evidence['time_compared'] is True
                  and bool(evidence['evidence']) and bool(evidence['cost_impact']))
        with self.lock():
            record = self.get(kind, name)
            if record['metadata'].get('status') != 'candidate':
                raise ValueError('Only a candidate can be evaluated')
            folder = self.root / 'evaluations' / kind / name
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            data = dict(evidence=evidence, passed=passed, candidate_hash=record['sha256'], created=time.time())
            atomic_write(folder / f"{record['metadata']['version']}.json", json.dumps(data, indent=2) + '\n')
            return data

    def approve(self, kind, name):
        with self.lock():
            record = self.get(kind, name)
            meta = record['metadata']
            evaluation = self.root / 'evaluations' / kind / name / f"{meta['version']}.json"
            if meta.get('status') != 'candidate' or not evaluation.is_file():
                raise ValueError('An inferred change needs a passing evaluation before approval')
            ev = json.loads(evaluation.read_text())
            if not ev['passed'] or ev['candidate_hash'] != record['sha256']:
                raise ValueError('Evaluation did not pass for this exact candidate')
            return self._write(kind, name, record['text'], status='trial', direct=False,
                               source=meta.get('source', ''), previous=meta.get('previous', 0),
                               evaluation=str(evaluation), trial_runs=[])

    def outcome(self, kind, name, run_id, accepted, constraint_lost=False, metric_worse=False):
        with self.lock():
            record = self.get(kind, name)
            meta = record['metadata']
            if meta.get('status') != 'trial':
                raise ValueError('Guidance is not in a trial')
            runs = meta.get('trial_runs', [])
            if run_id in runs:
                return record
            if not accepted or constraint_lost or metric_worse:
                return self._restore(kind, name, meta.get('previous', 0), reason=f'Trial paused after a weak result in {run_id}; cause unproven. Review before retrying.')
            runs = runs + [run_id]
            return self._write(kind, name, record['text'], status='active' if len(runs) >= 5 else 'trial',
                               direct=False, previous=meta.get('previous', 0), source=meta.get('source', ''), trial_runs=runs)

    def _restore(self, kind, name, version, reason='User undo'):
        current = self.get(kind, name)
        old = self.get(kind, name, version) if version else current
        return self._write(kind, name, old['text'], status='active' if version else 'disabled',
                           direct=old['metadata'].get('direct', False), restored_from=version,
                           previous=old['metadata'].get('previous', 0), source=reason)

    def undo(self, kind, name, version=None):
        with self.lock():
            record = self.get(kind, name)
            return self._restore(kind, name, record['metadata'].get('previous', 0) if version is None else version)

    def disable(self, kind, name):
        with self.lock():
            record = self.get(kind, name)
            return self._write(kind, name, record['text'], status='disabled', direct=True,
                               previous=record['metadata']['version'], source='User disabled')

    def delete(self, kind, name):
        """Explicit deletion of reusable guidance, not already recorded run snapshots."""
        import shutil
        with self.lock():
            self.path(kind, name).unlink(missing_ok=True)
            for group in ('history', 'evaluations'):
                folder = self.root / group / kind / name
                if folder.exists():
                    shutil.rmtree(folder)
        return dict(deleted=name, note='Future guidance and its library history removed. Existing run snapshots remain separate records.')

    def active(self, kind='learnings'):
        records = []
        for r in self.list(kind):
            # A proposed candidate must not erase an already active preference.
            if r['metadata'].get('status') == 'candidate' and r['metadata'].get('previous'):
                r = self.get(kind, r['metadata']['name'], r['metadata']['previous'])
            if r['metadata'].get('status') in ('active', 'trial'):
                records.append(r)
        return records
