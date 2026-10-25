"""Owner-only Unix socket service. Hooks never run scheduler work themselves."""
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import threading
import time
from .engine import Engine
from .notifications import notify
from .store import Store, home_path

MAX_REQUEST = 8 * 1024 * 1024
PROTOCOL_VERSION = 2


def socket_path(home=None):
    return Path(home or home_path()).expanduser().resolve() / "daemon.sock"


def request(method, home=None, timeout=10, **params):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        path = socket_path(home)
        try:
            client.connect(str(path))
        except PermissionError as exc:
            raise PermissionError(
                exc.errno,
                f"Access denied connecting to breakout socket {path}. "
                "The daemon may already be running. Check socket ownership and "
                "session sandbox permissions; try 'breakout daemon status' in "
                "an ordinary terminal. Starting another daemon will not fix denied access."
            ) from exc
        client.sendall(json.dumps(dict(method=method, params=params)).encode() + b"\n")
        with client.makefile("rb") as stream:
            line = stream.readline(MAX_REQUEST + 1)
        if not line or len(line) > MAX_REQUEST:
            raise ValueError("Invalid daemon response")
        response = json.loads(line)
    if "error" in response:
        raise ValueError(response["error"])
    return response["result"]


class Service:
    def __init__(self, engine):
        self.engine = engine
        self.stop = threading.Event()

    def dispatch(self, method, params):
        e = self.engine
        if method == "ping":
            with e.lock:
                busy = bool(e.active) or any(r['status'] == 'working' for r in e.store.all())
            return {"status": "running", "pid": os.getpid(), 'protocol_version': PROTOCOL_VERSION, 'busy': busy}
        if method == "shutdown":
            self.stop.set()
            with e.lock:
                for cancel in e.active.values():
                    cancel.set()
            return {"status": "stopping"}
        if method == "create":
            return e.create(**params)
        if method == 'launch':
            return e.launch(**params)
        if method == "control":
            return e.control(**params)
        if method == 'review':
            return e.review_control(**params)
        if method == 'open':
            with e.lock:
                return [dict(id=r['id'], status=r['status'], task=r['task'],
                             team=[p['name'] for p in r['participants']]) for r in e.store.all()
                        if r.get('version', 1) >= 2 and r['status'] != 'closed'
                        and r.get('origin', {}).get('provider') == params['provider']
                        and r.get('origin', {}).get('session_id') == params['session_id']]
        if method == "mailbox":
            return e.mailbox(**params)
        if method == "acknowledge":
            return e.acknowledge(**params)
        if method == "reconcile":
            return e.reconcile(**params)
        with e.lock:
            if method == "register":
                return e.store.register(params)
            if method == "sessions":
                return e.store.sessions()
            if method == "get":
                run = e.store.get(params["run_id"])
                run["attempts"] = [dict(r) for r in e.store.db.execute(
                    "SELECT id,participant,status,error,created FROM attempts WHERE run_id=? ORDER BY created", (run["id"],))]
                run["deliveries"] = [dict(r) for r in e.store.db.execute("SELECT * FROM deliveries WHERE run_id=?", (run["id"],))]
                return run
            if method == "list":
                return [dict(id=r["id"], status=r["status"], task=r["task"], turns=r["turns"],
                             simulate=r["simulate"]) for r in e.store.all()]
        raise ValueError("Unknown method")

    def work(self):
        while not self.stop.is_set():
            self.engine.expire_reviews()
            with self.engine.lock:
                runs = [r["id"] for r in self.engine.store.all() if r["status"] == "working"]
            for run_id in runs:
                if self.stop.is_set():
                    return
                try:
                    self.engine.step(run_id)
                except Exception as exc:
                    with self.engine.lock, self.engine.store.db:
                        run = self.engine.store.get(run_id)
                        run.update(status="failed", diagnostic=str(exc))
                        self.engine.store.save(run)
            with self.engine.lock, self.engine.store.db:
                notices = []
                for run in self.engine.store.all():
                    if run['result'] and not run.get('notification'):
                        # Claim once before dispatch: a crash must not cause a burst of duplicate notices.
                        run['notification'] = 'attempted; outcome not yet recorded'
                        self.engine.store.save(run)
                        notices.append(run['id'])
            for run_id in notices:
                outcome = notify(run_id)
                with self.engine.lock, self.engine.store.db:
                    run = self.engine.store.get(run_id)
                    run['notification'] = outcome
                    self.engine.store.save(run)
            self.stop.wait(.1)


def serve(home=None):
    os.umask(0o077)
    state = Path(home or home_path()).expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if state.stat().st_uid != os.getuid():
        raise ValueError('State directory must belong to the current user')
    state.chmod(0o700)
    lock_file = (state / "daemon.lock").open("a")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        raise ValueError("A daemon already owns this state directory")
    store = Store(state)
    engine = Engine(store)
    engine.recover()
    service = Service(engine)
    path = socket_path(state)
    if path.exists():
        path.unlink()  # safe only after acquiring the singleton lock

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(10)
            try:
                raw = self.rfile.readline(MAX_REQUEST + 1)
                if len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
                    raise ValueError("Request too large or incomplete")
                data = json.loads(raw)
                result = service.dispatch(data["method"], data.get("params", {}))
                response = {"result": result}
            except Exception as exc:
                response = {"error": str(exc)}
            self.wfile.write(json.dumps(response).encode() + b"\n")

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = False

    try:
        with Server(str(path), Handler) as server:
            path.chmod(0o600)
            server.timeout = .2
            worker = threading.Thread(target=service.work, name="breakout-scheduler")
            worker.start()

            def stop_handler(*_):
                service.dispatch("shutdown", {})

            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGTERM, stop_handler)
                signal.signal(signal.SIGINT, stop_handler)
            while not service.stop.is_set():
                server.handle_request()
            # Adapters have bounded preflight/termination timeouts. Never close SQLite
            # while a worker can still persist a final receipt or uncertain outcome.
            worker.join()
    finally:
        with engine.lock:
            for cancel in engine.active.values():
                cancel.set()
        path.unlink(missing_ok=True)
        store.db.close()
        lock_file.close()
