# AURA AI — Authentication Fix Report

**Date:** 22 August 2026
**Site tested:** https://aura-ai-psi-self.vercel.app
**Scope:** login and signup — reproduce, diagnose, fix, verify
**Deployed:** no. Nothing committed, pushed or deployed. One file modified locally.

---

## 1. Problem

Login appeared broken. Entering credentials on the sign-in form produced the
error **"Session expired"**.

That message is what made this hard to diagnose. It describes a session that
timed out — which points a user at cookies, tokens and session storage, none of
which were at fault. Nothing on screen suggested the password itself was the
issue, so the natural conclusion was that authentication was failing at a
systemic level.

Reproduced in Chrome on the live site: submitting `udaysk2008@gmail.com` with a
deliberately wrong password rendered `Session expired` in the form's error
panel.

---

## 2. Root Cause

`frontend/lib/api.ts`, in the shared `request()` helper, line 67 (before fix):

```ts
if (res.status === 401) {
  clearToken();
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
  throw new ApiError(401, "Session expired");
}
```

A blanket interceptor that treats **every** HTTP 401 as an expired session.

That is correct for authenticated requests — a 401 from `/api/auth/me` really
does mean the token is dead. But the same helper carries `POST /api/auth/login`,
where 401 has an entirely different meaning: *those credentials are wrong.*
There is no session at that point; the user has not logged in yet.

The interceptor ran first and threw before the generic error branch below could
read the response body, so the server's actual message was discarded:

| Endpoint | Server response | What the user saw (before) |
| --- | --- | --- |
| `POST /api/auth/login` | `401 {"detail":"Incorrect email or password"}` | `Session expired` |
| `GET /api/auth/me` | `401 {"detail":"Invalid or expired token"}` | `Session expired` (correct) |

Both 401s were collapsed into one message. The backend was behaving correctly
throughout; only the client's interpretation was wrong.

### What was *not* the cause

Ruled out by direct testing against the live API before any code was changed:

- **Backend auth logic** — every path returns the correct status (see §7).
- **The account** — `udaysk2008@gmail.com` is registered. `POST /api/auth/register`
  returns `409 That email is already registered`.
- **Database** — `/health/ready` reports `{"status":"ready","checks":{"database":"ok"}}`.
- **Session handling** — a live token was present and `GET /api/auth/me` returned
  `200` with the correct user object.
- **Protected routes** — `/dashboard` while logged out correctly redirects to `/login`.
- **CORS, environment variables, Vercel config** — no errors in console or network.
- **Redirect loop** — none. Post-login routing to `/onboarding` is intentional
  (`app/(app)/layout.tsx` redirects while `assistant.onboarded` is false).

---

## 3. Login Fix

One change, `frontend/lib/api.ts`. The two credential-checking endpoints are
exempted from the session-expiry interceptor so their 401 falls through to the
existing generic branch, which already reads `body.detail` correctly:

```ts
const isCredentialCheck =
  path === "/api/auth/login" || path === "/api/auth/register";

if (res.status === 401 && !isCredentialCheck) {
  clearToken();
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
  throw new ApiError(401, "Session expired");
}
```

**Why this shape.** The exemption is an exact match on two literal paths, so
every other request keeps its previous behaviour byte for byte. Session expiry
still clears the token and redirects. No new dependency, no API change, no
change to the auth flow itself — the fix is purely in how one status code is
interpreted.

**Secondary effect, also fixed:** the old code called `clearToken()` on a failed
login. Harmless in practice, but it meant a mistyped password destroyed an
existing session in that tab. That no longer happens.

---

## 4. Signup Investigation

Signup does **not** have the same fault, because `POST /api/auth/register` never
returns 401. Its failure modes are:

| Case | Status | Handled by | Message shown |
| --- | --- | --- | --- |
| Email already registered | `409` | generic branch | `That email is already registered` |
| Password under 8 chars | `422` | client-side guard | `Password must be at least 8 characters.` |
| Malformed email | `422` | generic branch | validation detail from server |

All three were already correct and were verified working (§7).

`/api/auth/register` was nonetheless included in the exemption. It is a
credential-checking endpoint by nature, and if it ever returns 401 the same
misleading message would appear. This is defensive, not a fix for observed
behaviour.

**No new account was created.** The existing test account was not deleted,
modified, or password-reset. Whether `udaykhandagale3@gmail.com` exists could
not be determined without registering it — the login endpoint deliberately
returns an identical 401 for "no such user" and "wrong password" to prevent
email enumeration. **If that account was never registered, "Incorrect email or
password" is correct behaviour, not a bug.**

---

## 5. Signup Fix

None required. The only change touching signup is its inclusion in the
`isCredentialCheck` exemption, described above.

---

## 6. Files Changed

| File | Change | Why |
| --- | --- | --- |
| `frontend/lib/api.ts` | Added `isCredentialCheck` and one condition on the 401 interceptor (+8 lines incl. comment) | The root cause. Lets the login endpoint's real error reach the form. |

**One file. No other file in the repository was modified.** No dependencies
added, no configuration touched, no UI redesign, no refactoring, and no change
to backend code, database, Vercel settings or Supabase.

---

## 7. Verification

### Live API probes (before the fix, no real password used)

| Probe | Result |
| --- | --- |
| `POST /api/auth/login` — registered email, wrong password | `401 Incorrect email or password` |
| `POST /api/auth/login` — second email, wrong password | `401` identical message (enumeration-safe) |
| `POST /api/auth/register` — existing email | `409 That email is already registered` |
| `POST /api/auth/register` — 5-char password | `422 String should have at least 8 characters` |
| `POST /api/auth/register` — malformed email | `422 An email address must have an @-sign` |
| `GET /api/auth/me` — with live token | `200`, correct user |
| `GET /health/ready` | `{"status":"ready","checks":{"database":"ok"}}` |

### Browser reproduction

- Live site, wrong password submitted through the form → **`Session expired`** (the bug).
- `/dashboard` while logged out → correctly redirected to `/login`.
- No console errors at any point.

### Static checks after the fix

| Check | Result |
| --- | --- |
| `tsc --noEmit` (whole frontend) | clean, 0 errors |
| `next build` | `✓ Compiled successfully` |
| `pytest tests/test_password_auth.py` | 14 passed |
| `git status` | only `M frontend/lib/api.ts` — nothing staged or committed |

### Browser verification of the fix

Ran the patched frontend and backend locally and submitted an unregistered
email with an invalid password through the actual form:

| Build | Message rendered |
| --- | --- |
| Live site (unpatched) | `Session expired` |
| Local (patched) | **`Incorrect email or password`** |

### Regression checks

| Check | Result |
| --- | --- |
| `/api/auth/me` with a wrongly-signed token | `401 Invalid or expired token` — interceptor still fires, token still cleared |
| `/api/auth/login` with bad credentials | `401 Incorrect email or password` — now surfaces |
| Signup toggle ("Create one") | Switches correctly: heading, Name field, button label all update |
| Signup password validation | Blocks submit, shows `Password must be at least 8 characters.` |
| Protected route while logged out | Redirects to `/login` |
| Login form rendering | Correct; demo button correctly hidden in production, shown locally |

The two 401 classes are now provably distinct and routed to different handlers.

---

## 8. Remaining Issues

**1. Could not verify a successful login end to end.**
I do not enter passwords into forms, so the "correct credentials → dashboard"
step was not executed by me. It is strongly evidenced rather than directly
observed: a valid session token existed in the browser, `/api/auth/me` returned
`200`, and since demo login and Google OAuth are both disabled, that token can
only have been produced by a successful password login or registration.
**Please confirm this step yourself after deploying.**

**2. I logged you out of that browser.** While testing I stashed the session
token on `window` and then navigated, which discarded it. My error. Sign in
again — nothing was damaged, and the account is intact.

**3. Onboarding swallows its errors silently.** `frontend/app/onboarding/page.tsx`:

```js
} catch {
  setBusy(false);   // no message, no console log
}
```

If `POST /api/assistant/hatch` fails, the button un-busies and nothing visible
happens. Because `app/(app)/layout.tsx` redirects to `/onboarding` until
`onboarded` is true, a failure there would strand a user permanently one step
past a successful login — indistinguishable from broken login. I found no
evidence it is currently failing (`HatchIn` matches the payload the client
sends), so **I did not change it**, per the instruction to make only necessary
changes. Worth fixing separately.

**4. `refresh()` does not clear a stale token.** In `frontend/lib/auth.tsx`, when
`api.me()` throws, `user` is set to null but the dead token stays in
localStorage, so every page load makes one failing request. Cosmetic, not a
blocker. Not changed.

**5. Setting `SECRET_KEY` invalidated all pre-existing sessions.** Tokens are
signed with it. Anyone logged in before that deployment was silently signed out.
Expected and one-time — but if the report of "login broken" began around then,
this is likely part of what you saw.

---

## 9. Deployment Notes

**No environment variable, Vercel, Supabase or database changes are required.**
This is a frontend-only, logic-only fix.

One thing to be aware of: `NEXT_PUBLIC_*` values are inlined at build time, but
this change is not one of them — it is ordinary application code, so a normal
deployment picks it up.

Suggested sequence:

1. Review the diff: `git diff frontend/lib/api.ts`
2. Commit and push — Vercel deploys `main` automatically.
3. After the deployment reports Ready, sign in with a deliberately wrong
   password and confirm it now reads **"Incorrect email or password"**.
4. Then sign in with the correct password and confirm you reach the dashboard.

Local helper scripts used during this investigation (`_git-*.bat`, `_git-*.txt`,
`_git-serve.bat`) are covered by the existing `.gitignore` rules and will not be
committed.
