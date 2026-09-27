# Reborn IT Operations

Personal infrastructure inventory, maintenance checklists, device heartbeats, and optional read-only AWS inventory and billing.

The Python backend owns SQLite and automation. The Electron desktop connects to that backend through a loopback SSH tunnel; closing a desktop does not stop monitoring. Windows and Linux clients can share one inventory.

## Development

Requires Python 3.10+, Node.js, and npm. Run `python server.py` for the loopback web interface. Install desktop dependencies with `npm ci`, then run `npm start` in another terminal. The desktop requires a running backend at `http://127.0.0.1:8741` or a saved loopback connection setting.

Run `python -m unittest discover -s tests -v` and `npm run check` for validation. Install `requirements-aws.txt` for the optional AWS collector and its tests.

## Deployment

Host the backend independently. Keep its `data/` directory, SQLite database, AWS profile, scoped reporter tokens, and Watchdog connection mapping private. The UI is loopback-only and needs a secure tunnel for remote use; it is not a publicly authenticated web service.

Client settings remain outside versioned application packages. Linux uses `$XDG_CONFIG_HOME/reborn-it-operations` (normally `~/.config/reborn-it-operations`). Existing Windows installations keep the sibling `data/` directory. `REBORN_DATA_DIR` can explicitly select the existing settings location.

Maintenance completion records checked and unchecked steps plus review notes. Automation observations retain the latest state and bounded failures. AWS billing refreshes require an explicit fee acknowledgement; the monthly schedule reuses cached billing between runs.

## Signed desktop releases

Reborn Update Agent adopts the existing installation, stages signed packages, launches a candidate, and accepts health only after the UI loads successfully. Backend files and inventory are excluded from client packages.

`scripts/build-release.cjs <win-x64|linux-x64> <native-updater-artifacts>` builds a clean directory under `release-staging/<version>/<rid>`. Use Reborn Release Publisher with the private machine-local release configuration. Only the public verification key and feed URLs belong in this repository; signing keys never do.

Linux requires an available Chromium sandbox. The launch script uses an existing system-owned Chrome sandbox helper when present; it never disables sandboxing.
