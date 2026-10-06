"""Local process control shared by the Windows launcher and PowerShell wrappers."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener
import uuid


ROOT = Path(__file__).resolve().parent.parent
BRANCH = "scanner-audit-and-v4"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ControlError(RuntimeError):
    pass


def safe_text(value: str) -> str:
    value = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[redacted]@", value)
    value = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "[redacted]", value)
    value = re.sub(r"(?i)((?:access_token|request_token|api_key|api_secret|password)\s*[:=]\s*)[^&\s]+", r"\1[redacted]", value)
    for key, secret in os.environ.items():
        if re.search(r"token|secret|password|api_key", key, re.I) and len(secret) >= 6:
            value = value.replace(secret, "[redacted]")
    return value


class ServerController:
    def __init__(self, root=ROOT, port=8766, host="127.0.0.1", emit=None):
        self.root = Path(root).resolve()
        self.port = int(port)
        if not 1024 <= self.port <= 65535:
            raise ControlError("Choose a port between 1024 and 65535.")
        self.host = host
        self.probe_host = "127.0.0.1" if host == "0.0.0.0" else host
        self.url = f"http://{self.probe_host}:{self.port}"
        self.logs = self.root / "logs"
        self.emit = emit or (lambda text: None)
        candidate = self.root / ".venv" / "Scripts" / "python.exe"
        self.python = str(candidate if candidate.exists() else Path(sys.executable).with_name("python.exe")
                          if sys.executable.lower().endswith("pythonw.exe") else Path(sys.executable))

    def say(self, text):
        text = safe_text(str(text))
        self.emit(text)
        try:
            self.logs.mkdir(parents=True, exist_ok=True)
            with (self.logs / "launcher.log").open("a", encoding="utf-8") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {text}\n")
        except OSError:
            pass

    def _run(self, args, timeout=30):
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
        try:
            result = subprocess.run(args, cwd=self.root, env=env, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=timeout,
                                    creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ControlError(safe_text(f"Unable to run {Path(args[0]).name}: {exc}")) from exc
        if result.returncode:
            raise ControlError(safe_text((result.stderr or result.stdout or "Command failed").strip()))
        return result.stdout.strip()

    @contextmanager
    def operation(self):
        self.logs.mkdir(parents=True, exist_ok=True)
        with (self.logs / "server-control.lock").open("a+b") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ControlError("Another start, stop, or update is in progress. Wait for it to finish.") from exc
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _health(self):
        try:
            # Local control must not be routed through a VPN or system proxy.
            with build_opener(ProxyHandler({})).open(self.url + "/api/health", timeout=2) as response:
                payload = json.loads(response.read(65536))
                return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            return {}

    def _port_open(self):
        try:
            with socket.create_connection((self.probe_host, self.port), timeout=0.3):
                return True
        except OSError:
            return False

    def status(self):
        health = self._health()
        try:
            own = (health.get("service") == "trading-analysis"
                   and bool(health.get("project_root"))
                   and Path(health.get("project_root", "")).resolve() == self.root
                   and int(health.get("port", 0)) == self.port and int(health.get("pid", 0)) > 0)
        except (ValueError, OSError, TypeError):
            own = False
        if own:
            return {"state": "running", "health": health, "url": self.url}
        if health or self._port_open():
            return {"state": "occupied", "health": {}, "url": self.url,
                    "message": f"Port {self.port} is busy, but this project's server could not be verified. See server logs."}
        return {"state": "stopped", "health": {}, "url": self.url}

    def _listener_pids(self):
        output = self._run(["netstat", "-ano", "-p", "tcp"], timeout=10)
        pids = set()
        for line in output.splitlines():
            parts = line.split()
            if len(parts) == 5 and parts[0] == "TCP" and parts[3] == "LISTENING" and parts[1].endswith(f":{self.port}"):
                pids.add(int(parts[4]))
        return pids

    def _kill(self, pid, tree=False):
        if os.name != "nt":
            raise ControlError("Process stopping is currently supported on Windows only.")
        args = ["taskkill", "/PID", str(pid)]
        if tree:
            args.append("/T")
        self._run(args + ["/F"], timeout=15)

    def start(self, timeout=60):
        with self.operation():
            return self._start(timeout)

    def _start(self, timeout=60):
        current = self.status()
        if current["state"] == "running":
            self.say(f"Server already running at {self.url} (PID {current['health']['pid']}).")
            return current
        if current["state"] == "occupied":
            raise ControlError(current["message"])
        self.logs.mkdir(parents=True, exist_ok=True)
        launch_id = uuid.uuid4().hex
        env = {**os.environ, "TRADING_SERVER_LAUNCH_ID": launch_id}
        self.say(f"Starting server on port {self.port} using {self.python}...")
        with (self.logs / f"web_{self.port}.out.log").open("ab") as out, (self.logs / f"web_{self.port}.err.log").open("ab") as err:
            process = subprocess.Popen([self.python, "-m", "trading_analysis.web_app", "--host", self.host,
                                        "--port", str(self.port)], cwd=self.root, env=env, stdout=out, stderr=err,
                                       stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            health = self.status()
            if health["state"] == "running" and health["health"].get("launch_id") == launch_id:
                (self.logs / f"web_{self.port}.pid").write_text(str(health["health"]["pid"]), encoding="ascii")
                self.say(f"Server ready at {self.url}. Build {health['health'].get('code_version', '-')}; PID {health['health']['pid']}.")
                return health
            if process.poll() is not None:
                raise ControlError(f"Server exited during startup. Open web_{self.port}.err.log in View Logs.")
            time.sleep(0.3)
        # Only clean up the process tree created by this operation, never an arbitrary listener.
        if process.poll() is None:
            self._kill(process.pid, tree=True)
            process.wait(timeout=10)
        raise ControlError(f"Server did not become ready within {timeout}s and was stopped. Open web_{self.port}.err.log in View Logs.")

    def stop(self):
        with self.operation():
            return self._stop()

    def _stop(self):
        current = self.status()
        if current["state"] == "stopped":
            self.say("Server is already stopped.")
            return current
        if current["state"] != "running":
            raise ControlError(current["message"] + " No process was stopped.")
        pid = int(current["health"]["pid"])
        if pid not in self._listener_pids():
            raise ControlError("Server identity changed. Refresh status and try again; no process was stopped.")
        self.say(f"Stopping this project's server (PID {pid}); scanner alerts will stop.")
        self._kill(pid)
        deadline = time.monotonic() + 10
        while self._port_open() and time.monotonic() < deadline:
            time.sleep(0.2)
        if self._port_open():
            raise ControlError("Port is still busy after stopping. Check View Logs before restarting.")
        (self.logs / f"web_{self.port}.pid").unlink(missing_ok=True)
        self.say("Server stopped. Stored candles and trade history were preserved.")
        return {"state": "stopped", "health": {}, "url": self.url}

    def _check_update(self):
        root = Path(self._run(["git", "rev-parse", "--show-toplevel"])).resolve()
        if root != self.root:
            raise ControlError("Open the launcher from the project's Git working folder.")
        branch = self._run(["git", "branch", "--show-current"])
        if branch != BRANCH:
            raise ControlError(f"Update requires branch {BRANCH}; current branch is {branch or 'detached'}. Nothing changed.")
        if self._run(["git", "status", "--porcelain", "--untracked-files=normal"]):
            raise ControlError("Local file changes were found. Update was cancelled without overwriting them. Ask the project maintainer to review them.")
        self._run(["git", "remote", "get-url", "origin"])

    def update(self):
        with self.operation():
            self._check_update()
            current = self.status()
            if current["state"] == "occupied":
                raise ControlError(current["message"])
            restart = current["state"] == "running"
            if restart:
                self._stop()
            self.say(f"Pulling origin/{BRANCH} (fast-forward only)...")
            try:
                self.say(self._run(["git", "pull", "--ff-only", "origin", BRANCH], timeout=180))
                self.say("Installing application dependencies...")
                self.say(self._run([self.python, "-m", "pip", "install", "--disable-pip-version-check",
                                    "-r", "requirements.txt"], timeout=300))
            except ControlError as exc:
                raise ControlError(f"Update did not finish. The server remains stopped; no reset or rollback was performed. {exc}") from exc
            revision = self._run(["git", "rev-parse", "--short", "HEAD"])
            if restart:
                self._start()
            self.say(f"Update complete: {revision}. Reopen the launcher to load any launcher changes.")
            return self.status()


def main():
    parser = argparse.ArgumentParser(description="Trading Desk server control")
    parser.add_argument("action", choices=("start", "stop", "status", "update"))
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    try:
        controller = ServerController(port=args.port, host=args.host, emit=print)
        result = getattr(controller, args.action)()
        if args.action == "status":
            print(json.dumps(result))
    except Exception as exc:
        print(safe_text(str(exc)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
