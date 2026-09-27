"""Independent backend entry point for Windows pythonw or a Linux service."""
import logging
import logging.handlers
import pathlib
import sys

if __name__ == '__main__':
    arguments = sys.argv[1:]
    db = pathlib.Path(arguments[arguments.index('--db')+1]) if '--db' in arguments else pathlib.Path(__file__).parent/'data/inventory.sqlite3'
    db.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(db.parent/'backend.log', maxBytes=512000, backupCount=2)
    logging.basicConfig(handlers=[handler], level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    class LogStream:
        def write(self, message):
            if message.strip(): logging.info(message.strip())
        def flush(self): pass
    sys.stdout = sys.stderr = LogStream()
    try:
        from server import main
        main()
    except SystemExit:
        raise
    except Exception:
        logging.exception('Backend stopped unexpectedly')
        raise
