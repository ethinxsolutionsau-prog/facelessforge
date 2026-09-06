# FacelessForge Deployment Review

## Scope

This review covers the audit-copy changes only. No file under `/opt/facelessforge/deploy` was modified and no image, container, DNS record, or production service was changed.

## Proposed runtime change

The Dockerfile continues to start `uvicorn main:app --host 0.0.0.0 --port 8080`. `main.py` imports the backend `server.app`. The reviewed server change explicitly mounts the narrow `/api` auth router and prevents the frontend catch-all from matching `/api/*`. The verified host mapping `7001:8080` is unchanged.

## Evidence before release

- Production `POST /api/auth/login`, `/register`, `/forgot-password`, and `/reset-password`: HTTP 405, `Allow: GET`, JSON `{"detail":"Method Not Allowed"}`.
- Production `GET /api/users/me`: HTTP 200 `text/html`, React index document.
- Audit-copy OpenAPI: POST login/register, GET users/me, POST logout/forgot/reset.
- Audit-copy real-Mongo checks: 3 passed.
- Focused local TestSprite login: 7/7 passed (`8b5b715a-2f5e-4902-9b00-c10b03d31a06`).
- Focused local TestSprite reset: 9/9 passed (`4978552e-74fa-4560-991b-96d4626c3d8a`).
- Frontend build: passed with existing hook-dependency warnings.

## Release gates

1. Build the frontend and image from the audit branch in CI; do not copy a local build or local storage artifacts.
2. Supply production secrets through the existing secret mechanism only. The auth patch does not add credentials or bypass tenant/role checks.
3. Verify the container listens on 8080 and preserve the host mapping `7001:8080`.
4. Run read-only smoke checks for login, registration, forgot/reset, users/me, health, and unknown `/api/*` routes. Confirm JSON content types and no SPA HTML for API paths.
5. Run the complete TestSprite project against the deployed revision. Use a dedicated QA account, disposable tenant, and disposable project fixtures.
6. Configure and verify the Google OAuth client ID, client secret, and callback URI before accepting the YouTube integration.
7. Verify password-reset mail delivery and single-use expiry with a QA mailbox.
8. Review the full diff and image contents for local secrets, generated recordings, and build artifacts before promotion.

## Decision

**Do not deploy from this audit session.** The production auth defect is confirmed, the audit-copy repair is locally verified, and the authenticated feature suite still requires QA fixtures and external OAuth/mail configuration. A separate release owner must review and promote the branch after these gates pass.
