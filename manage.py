"""Local start/stop/key setup helper. Keys never appear in process arguments."""
from pathlib import Path
from getpass import getpass
import json
import os
import subprocess
import sys
import time
import urllib.request
import psutil

ROOT = Path(__file__).resolve().parent
PID_FILE = ROOT / 'server.pid.json'
URL = 'http://127.0.0.1:8000'

def status():
    try:
        with urllib.request.urlopen(URL + '/api/v1/deployment-status', timeout=2) as response:
            data = json.load(response)
            return data if data.get('provider') == 'Zhipu' else None
    except Exception:
        return None

def stop():
    candidates = []
    if PID_FILE.exists():
        saved = json.loads(PID_FILE.read_text())
        try:
            process = psutil.Process(saved['pid'])
            if abs(process.create_time() - saved['created']) < .01:
                candidates.append(process)
        except psutil.NoSuchProcess:
            pass
    # Recover an existing server started before the launcher, without killing
    # an unrelated application that happens to use the same port.
    for connection in psutil.net_connections(kind='tcp'):
        if connection.status == psutil.CONN_LISTEN and connection.laddr.port == 8000 and connection.pid:
            try:
                candidates.append(psutil.Process(connection.pid))
            except psutil.NoSuchProcess:
                pass
    stopped = set()
    for process in candidates:
        if process.pid in stopped:
            continue
        try:
            if 'local_app:app' not in process.cmdline() or Path(process.cwd()).resolve() != ROOT.resolve():
                raise RuntimeError('Port 8000 belongs to another application; it was not stopped.')
            process.terminate()
            process.wait(timeout=15)
            stopped.add(process.pid)
        except psutil.NoSuchProcess:
            pass
    PID_FILE.unlink(missing_ok=True)
    if status():
        raise RuntimeError('The server is still running outside this process environment. Close its window or run Stop.cmd on your desktop, then Start.cmd.')
    print('Application stopped.')

def start():
    if status():
        print('Already running:', URL)
        return
    with (ROOT / 'server.out.log').open('a', encoding='utf-8') as out, (ROOT / 'server.err.log').open('a', encoding='utf-8') as err:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'local_app:app', '--host', '127.0.0.1', '--port', '8000'], cwd=ROOT, stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
    PID_FILE.write_text(json.dumps({'pid': process.pid, 'created': psutil.Process(process.pid).create_time()}))
    for _ in range(50):
        if process.poll() is not None:
            raise RuntimeError('Server failed to start; see server.err.log.')
        info = status()
        if info:
            print('Open', URL)
            if not info['configured']:
                print('Zhipu API key missing. Run Configure.cmd to finish setup.')
            if os.environ.get('RESUME_KEEP_ALIVE') == '1':
                process.wait()
            return
        time.sleep(1)
    raise RuntimeError('Startup timed out; see server.err.log.')

def configure():
    key = getpass('Enter your Zhipu API key (hidden): ').strip()
    if not key or any(c.isspace() or c in '\"\x27#' for c in key):
        raise ValueError('Invalid key format; no changes made.')
    path = ROOT / '.env'
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    lines = [line.split('=', 1)[0] + '=' + key if line.startswith(('LLM_API_KEY=', 'EMBEDDING_API_KEY=')) else line for line in lines]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    del key, lines
    print('Key saved locally. Restarting...')
    stop()
    start()

if __name__ == '__main__':
    try:
        {'start': start, 'stop': stop, 'configure': configure}[sys.argv[1] if len(sys.argv) > 1 else 'start']()
    except Exception as exc:
        print('Error:', exc)
        sys.exit(1)
