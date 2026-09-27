"""Device-originated report; endpoint and scoped credential live in config.json."""
import fcntl
import json
import os
import pathlib
import subprocess
import sys
import urllib.request
import datetime

ROOT = pathlib.Path(__file__).resolve().parent

def main():
    with open(ROOT / 'run.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        result = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        try:
            config = json.loads((ROOT / 'config.json').read_text())
            observed = json.loads(subprocess.check_output([sys.executable, str(ROOT / 'Probe-NeonNano.py')], text=True, timeout=30))
            changes = {'status':'ONLINE', 'last_heartbeat':result['at'], 'details.last_reboot':observed['boot']}
            if observed.get('storage'):
                changes['details.storage'] = observed['storage']
            request = urllib.request.Request(config['endpoint'], data=json.dumps({'changes':changes}).encode(), headers={'Authorization':'Bearer '+config['token'], 'Content-Type':'application/json'}, method='POST')
            with urllib.request.urlopen(request, timeout=20) as response:
                response.read()
            result['state'] = 'reported'
        except Exception as error:
            result.update(state='failed', error=type(error).__name__)
        # Overwrite one result; never log tokens or grow a report queue.
        (ROOT / 'last-result.json').write_text(json.dumps(result))
        os.chmod(ROOT / 'last-result.json', 0o600)

if __name__ == '__main__':
    main()
