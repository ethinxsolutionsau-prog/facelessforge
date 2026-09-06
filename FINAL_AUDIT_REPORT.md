# FacelessForge FULL AUDIT REPORT

## Audit Summary

- **Target:** https://facelessforge.ethinx.solutions
- **Audit copy:** `/opt/facelessforge/facelessforge-audit-20260906-163809`
- **TestSprite project:** `FacelessForge Production`
- **Project ID:** `833859c5-d1f8-4034-87af-739b19505cfb`
- **Tests created:** 19 (15 broad production tests and 4 focused audit-copy tests)
- **Final TestSprite inventory after all reruns:** 19 total; 6 passed, 13 failed, 0 blocked
- **Final production batch:** 16 dispatched; 4 passed, 12 failed, 0 timed out
- **Focused audit-copy reruns:** login passed, password reset passed, registration failed at the fixture/flow boundary
- **Deployment:** none; `/opt/facelessforge/deploy` was not modified

The production target is still running the pre-fix build. The audit-copy patch is therefore verified locally, while production auth remains unresolved until a separately reviewed deployment is made.

## Tests Created

The broad suite covered landing/static resources; invalid and valid login; registration and invite handling; forgot/reset password; persistence and logout; dashboard and projects; project creation/detail; asset library; analytics/automation; YouTube OAuth; protected APIs; render/media; tenant isolation; and unknown routes/API consistency.

Focused audit-copy cases:

- `791fcc29-8227-477a-bd67-dd37ca183f6f` — repaired login user flow
- `4dc37534-3024-4d71-aa13-3ea78a7c3f60` — repaired password reset flow
- `cf4d783b-966a-4c70-ac0c-c18c4e598265` — repaired registration flow
- `48b13895-b97c-4b03-b2a7-3dc3d7d10602` — deterministic invalid-login response check

## Tests Executed

- Initial production login reproduction: `3587603d-69c4-4f85-8add-ece84cc3bc0b` — failed with visible `Method Not Allowed`.
- Final broad production regression: `audit/testsprite/final-regression.xml` and `audit/testsprite/final-regression-summary.json` — 4/16 passed, 12/16 failed.
- Production batch run IDs: `181fd908-de87-4649-8751-1ea55b0cd37e`, `a440c975-b872-42a7-ab46-e8d01dcd2168`, `305f74d1-f12c-4347-8c1b-84ac58a2c897`, `73ad1c9f-39f8-410b-9ea6-791b746343cc`, `988734f0-f7b4-427c-8d9d-333e824f7e7e`, `87b55c8a-5aaf-4d30-84f2-cd8673397baa`, `18be6123-2e33-427a-a48a-85f59e5a2c8b`, `42550996-79e5-4531-9d5e-062ecf04bf26`, `ea441779-f851-4b49-8bd2-84fc6f5bdf4b`, `9aa57f38-fe08-4b69-aeef-be2d6d412d7d`, `e26fb7a1-d630-4382-9b56-043378463a28`, `c7cdb8f1-b397-4e9f-b888-60196fee5cba`, `d316be02-c52c-40d3-a911-ab3e8c9865ef`, `5d02f4df-6308-4c97-a057-2debeed11370`, `ea70ec5d-0c65-4d43-b499-812010881048`, `005f1e43-c48d-40d0-a3fe-0948218a8907`.
- Original production login rerun: `8c234125-1a40-4c90-bf4e-d5b40f3c85f5` — terminal failed.
- Audit-copy login rerun: `8b5b715a-2f5e-4902-9b00-c10b03d31a06` — passed 7/7.
- Audit-copy reset rerun: `4978552e-74fa-4560-991b-96d4626c3d8a` — passed 9/9.
- Audit-copy registration rerun: `8764079a-ebf7-4e96-b2a9-02667ef34657` — terminal failed because the generated runner reused an already registered fixture email.
- Isolated Mongo/ASGI regression: `3 passed, 7 warnings`.
- Frontend production build: completed successfully with existing ESLint hook-dependency warnings.

## Passed

- Production landing/static resources, asset library, unknown-route handling, and the broad forgot/reset scenario were reported passed by TestSprite.
- Audit-copy login flow: real POST login, JSON 401 for invalid credentials, and signed-out state passed.
- Audit-copy reset flow: invalid token handling and valid single-use reset flow passed.
- Local route tests confirmed JSON 401/404 behavior, secure HttpOnly auth cookies, case-insensitive email login, logout, refresh persistence, duplicate registration rejection, and invalid token rejection.

## Failed

| Defect / scope | Severity | Reproduction and terminal result |
|---|---|---|
| Production auth routes unavailable | Critical | TestSprite runs `3587603d-69c4-4f85-8add-ece84cc3bc0b` and `8c234125-1a40-4c90-bf4e-d5b40f3c85f5`; direct probes show `POST /api/auth/login` and `POST /api/auth/register` return **405**, `Allow: GET`, `content-type: application/json`, body `{"detail":"Method Not Allowed"}`. `POST /api/auth/forgot-password` and `/api/auth/reset-password` show the same 405. |
| `/api/users/me` is intercepted by the production SPA fallback | Critical | `GET /api/users/me` returns **200**, `text/html; charset=utf-8`, with the React index document instead of an API response. |
| Authenticated product flows cannot start on production | High | Dashboard/projects `87b55c8a-5aaf-4d30-84f2-cd8673397baa`, create/detail `a440c975-b872-42a7-ab46-e8d01dcd2168`, analytics/automation `18be6123-2e33-427a-a48a-85f59e5a2c8b`, protected API `ea441779-f851-4b49-8bd2-84fc6f5bdf4b`, render/media `d316be02-c52c-40d3-a911-ab3e8c9865ef`, tenant isolation `ea70ec5d-0c65-4d43-b499-812010881048`, and persistence/logout `005f1e43-c48d-40d0-a3fe-0948218a8907` all terminated blocked or failed before authenticated assertions. |
| YouTube OAuth cannot be exercised | High | `e26fb7a1-d630-4382-9b56-043378463a28` terminated before Settings because production login failed. The known Google callback exchange remains `invalid_client` and requires external OAuth configuration. |
| Registration focused runner has no stable disposable fixture | Medium | `8764079a-ebf7-4e96-b2a9-02667ef34657` terminated with `Email already registered`; the local API registration itself returns 200 for a new account. The app intentionally gates `/app` on `forge_invite`, so the test must seed an invite or assert the waitlist redirect. |

## Blockers

- Production was intentionally not deployed, so production reruns necessarily exercise the old build.
- No approved QA account or seeded tenant/project fixtures were available for authenticated production scenarios.
- Google OAuth client configuration remains an external dependency; `invalid_client` cannot be repaired from this repository without the correct client credentials and redirect configuration.
- TestSprite V3 frontend runs do not expose browser network metadata in the result bundle; exact request evidence came from the exported test code and independent read-only HTTP probes.

## Root Causes

The Dockerfile starts `uvicorn main:app` on port 8080. `main.py` adds `backend` to `sys.path` and imports `server.app`. In the audit copy, the effective server previously imported the small `app.routes` package (`fix_router`), while the account handlers lived in the dormant monolithic router module. The runtime app therefore had no POST handlers for `/api/auth/login` or `/api/auth/register`.

The catch-all frontend route accepted every GET path. A POST to an API path matched that GET-only route and produced 405; a GET to `/api/users/me` fell through to the React index document. The frontend caller is `frontend/src/lib/auth.jsx`: `api.post("/auth/login")`, `api.post("/auth/register")`, and `fetch(`${API}/users/me`)`, with `API` defined as `${REACT_APP_BACKEND_URL}/api` in `frontend/src/lib/api.js`.

## Code Changes

- `backend/app/routes/auth.py`: added a narrow `/api` auth router for register, login, logout, current-user, forgot-password, and single-use reset-password handlers. It reuses the existing bcrypt/JWT/cookie/auth dependencies, strips password hashes from responses, preserves role validation, and keeps generic reset responses.
- `backend/server.py`: explicitly includes the auth router and changes the SPA converter to exclude `api` paths, preserving API JSON 401/404/405 responses. R2 fallback credentials in the audit copy now come only from environment variables; no secret values are added by this fix.
- `frontend/craco.config.js`: allows the existing `.ts`/`.tsx` imports so the audit-copy production build completes.
- `audit/tests/test_auth_routing.py`, `audit/local_auth_server.py`, and `audit/testsprite/*`: disposable local verification harness, plans, and TestSprite evidence. Test artifacts under `audit/testsprite/runs/` are ignored and contain raw runner output.

The change deliberately does not mount the large dormant generation/project router. Authenticated project APIs remain a separate review item because enabling that router blindly would expand permissions and tenant-facing surface area.

## Regression Results

- Isolated real-Mongo ASGI checks: **3 passed**, 7 non-fatal framework warnings.
- Focused local TestSprite login: **7/7 passed**.
- Focused local TestSprite reset: **9/9 passed**.
- Focused local registration: **terminal failed** on a reused fixture email; no 405 was observed.
- Frontend build: **passed** with existing ESLint warnings.
- Production TestSprite regression: **4/16 passed, 12/16 failed**; no production behavior changed because deployment was prohibited.
- `git diff --check`: passed.

## External Configuration Issues

- Google YouTube OAuth client/secret or redirect configuration is invalid (`invalid_client`).
- No approved production QA account, disposable tenant, or seeded project fixtures were supplied.
- Password reset token persistence is implemented in the audit copy, but an email transport/provider is not wired by this patch; delivery requires the configured mail integration.

## Remaining Risks

- Production login, registration, password reset, and current-user API behavior remain broken until the reviewed audit-copy changes are deployed by an authorized release process.
- Authenticated dashboard, project, analytics, automation, render, and tenant-isolation flows need a seeded QA tenant and a post-deployment TestSprite run.
- OAuth callback success and email delivery remain unverified external integrations.

## Final Status

**PARTIAL** — the root cause is confirmed and the smallest audit-copy auth/routing fix passes local unit/integration and focused user-flow checks, but production remains unchanged and the full production suite still has unresolved blockers and failures.
