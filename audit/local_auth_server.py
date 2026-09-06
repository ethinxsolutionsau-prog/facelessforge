"""Isolated local auth retest server; no production environment or scheduler.

Requires disposable MongoDB bound to 127.0.0.1:27079 and frontend/build.
Uses the actual main.app. The test import helper suppresses only dotenv and
an unrelated absolute teaser-directory mkdir; HTTP handlers are unmodified.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.tests.test_auth_routing import APP
from app.db import init_db
import uvicorn

if __name__ == '__main__':
    init_db()
    uvicorn.run(APP, host='127.0.0.1', port=18763, lifespan='off', access_log=False)
