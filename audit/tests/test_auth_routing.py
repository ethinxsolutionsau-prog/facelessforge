"""Real Mongo + ASGI checks. No production environment or services are used.

The server's background lifespan is deliberately not started: it schedules
publishing jobs unrelated to auth. Only /app/teasers mkdir and dotenv loading
are suppressed during import; main.app and its HTTP handlers are unmodified.
"""
import asyncio
import contextlib
import importlib
import io
import os
from pathlib import Path
import sys
import uuid
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def load_app():
    os.environ.clear()
    os.environ.update(
        PATH='/usr/bin:/bin',
        JWT_SECRET='isolated-audit-signing-key-never-used-in-production',
        MONGO_URL='mongodb://127.0.0.1:27079/?serverSelectionTimeoutMS=3000',
        DB_NAME='ff_audit_test_' + uuid.uuid4().hex,
        DOTENV_DISABLED='1',
    )
    original_mkdir = Path.mkdir

    def local_mkdir(path, *args, **kwargs):
        if str(path) == '/app/teasers':
            return
        return original_mkdir(path, *args, **kwargs)

    with patch.object(Path, 'mkdir', local_mkdir), \
         patch('dotenv.load_dotenv', return_value=False), \
         contextlib.redirect_stdout(io.StringIO()):
        main = importlib.import_module('main')
    assert main.app is sys.modules['server'].app
    return main.app


APP = load_app()


def test_effective_entrypoint_registers_only_reviewed_core_auth():
    paths = APP.openapi()['paths']
    assert 'post' in paths['/api/auth/login']
    assert 'post' in paths['/api/auth/register']
    assert 'get' in paths['/api/users/me']
    assert '/api/projects' not in paths  # dormant core router is not enabled
    assert not any(
        getattr(m, '__file__', None) == str(ROOT / 'backend/app/routes.py')
        for m in list(sys.modules.values())
    )


def test_auth_roundtrip_and_authorization_with_real_mongo():
    async def check():
        from app import db
        db.init_db()
        database = db.get_db()
        await database.users.create_index('email', unique=True)
        transport = httpx.ASGITransport(app=APP)
        try:
            async with httpx.AsyncClient(transport=transport, base_url='https://audit.local') as client:
                response = await client.get('/api/users/me')
                assert response.status_code == 401
                assert response.headers['content-type'].startswith('application/json')
                response = await client.post('/api/auth/login', json={
                    'email': 'nonexistent@example.com', 'password': 'invalid-password',
                })
                assert response.status_code == 401
                assert response.json() == {'detail': 'Invalid email or password'}
                assert (await client.post('/api/auth/login', json={})).status_code == 422
                assert (await client.get('/api/auth/login')).status_code == 405
                forgot = await client.post('/api/auth/forgot-password', json={
                    'email': 'unknown@example.com',
                })
                assert forgot.status_code == 200
                assert forgot.json() == {
                    'ok': True,
                    'message': 'If that email exists, a reset link has been issued.',
                }
                reset = await client.post('/api/auth/reset-password', json={
                    'token': 'invalid-audit-reset-token',
                    'new_password': 'Local-Only-QA-Password-123',
                })
                assert reset.status_code == 400
                assert reset.json()['detail'].startswith('This reset link is invalid')
                payload = {'name': 'FF QA local', 'email': 'audit-a@example.com',
                           'password': 'Local-Only-QA-Password-123', 'role': 'creator'}
                response = await client.post('/api/auth/register', json={**payload, 'role': 'admin'})
                assert response.status_code == 422
                response = await client.post('/api/auth/register', json=payload)
                assert response.status_code == 200
                user_a = response.json()
                assert user_a['role'] == 'creator'
                assert 'password_hash' not in user_a and '_id' not in user_a
                assert all('HttpOnly' in value and 'Secure' in value
                           for value in response.headers.get_list('set-cookie'))
                stored = await database.users.find_one({'id': user_a['id']})
                assert stored['password_hash'] != payload['password']
                assert (await client.get('/api/users/me')).json()['id'] == user_a['id']
                assert (await client.post('/api/auth/register', json=payload)).status_code == 400
                assert (await client.post('/api/auth/logout')).status_code == 200
                assert (await client.get('/api/users/me')).status_code == 401
                response = await client.post('/api/auth/login', json={
                    'email': payload['email'].upper(), 'password': payload['password'],
                })
                assert response.status_code == 200
                assert (await client.get('/api/users/me')).json()['id'] == user_a['id']
                # A fresh browser session using the issued cookie remains the same user.
                async with httpx.AsyncClient(transport=transport, base_url='https://audit.local',
                                             cookies=client.cookies) as refreshed:
                    assert (await refreshed.get('/api/users/me')).json()['id'] == user_a['id']
                # Independent account identity; no inferred tenant or admin escalation.
                async with httpx.AsyncClient(transport=transport, base_url='https://audit.local') as other:
                    response = await other.post('/api/auth/register', json={**payload, 'email': 'audit-b@example.com'})
                    assert response.status_code == 200
                    assert response.json()['id'] != user_a['id']
                    assert (await other.get('/api/users/me')).json()['email'] == 'audit-b@example.com'
                client.cookies.clear()
                assert (await client.get('/api/users/me', headers={'Authorization': 'Bearer invalid'})).status_code == 401
                from app.auth import create_refresh_token
                response = await client.get('/api/users/me', headers={
                    'Authorization': 'Bearer ' + create_refresh_token(user_a['id']),
                })
                assert response.status_code == 401
                assert (await client.get('/api/health')).status_code == 200
        finally:
            # This name is generated above and can only refer to the isolated QA DB.
            assert database.name.startswith('ff_audit_test_')
            await database.client.drop_database(database.name)
            db.close_db()
    asyncio.run(check())


def test_spa_does_not_intercept_api_misses():
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=APP), base_url='https://audit.local') as client:
            for path in ['/api', '/api/ff-audit-nonexistent', '/api/projects']:
                response = await client.get(path)
                assert response.status_code == 404
                assert response.headers['content-type'].startswith('application/json')
            # Only run the browser-page assertion when a real local build exists.
            if (ROOT / 'frontend/build/index.html').exists():
                response = await client.get('/login')
                assert response.status_code == 200
                assert response.headers['content-type'].startswith('text/html')
    asyncio.run(check())
