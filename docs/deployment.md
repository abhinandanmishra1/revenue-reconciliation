# Deployment

Three hosted pieces: **Neon** (Postgres), **Railway** (API container), **Vercel** (Next.js). The browser only talks to Vercel; Vercel forwards `/api/*` to Railway.

```
Browser ──► Vercel (Next.js, /api/* rewrite) ──► Railway (FastAPI) ──► Neon (Postgres)
```

Do the steps in order: each one needs a URL from the one before.

## 1. Neon: database

1. Create a project. Postgres 15 or newer is required (the explanation cache uses `UNIQUE NULLS NOT DISTINCT`); the default version is fine.
2. Open **Connect**, turn **Connection pooling off**, and copy the **direct** connection string (the host has no `-pooler`). Transaction-mode poolers break prepared statements.
3. Keep `?sslmode=require` on the end. The API accepts `postgres://` and `postgresql://` URLs and switches them to the psycopg driver.

## 2. Railway: API

1. **New project → Deploy from GitHub repo** and choose this repository.
2. Service **Settings → Root directory**: `backend`. Railway finds `backend/Dockerfile`.
3. **Variables:**

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | Neon direct connection string from step 1 |
   | `JWT_SECRET` | Output of `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `COOKIE_SECURE` | `true` |
   | `APP_ORIGINS` | Your Vercel URL, e.g. `https://your-app.vercel.app` (no trailing slash; set after step 3, then redeploy) |
   | `TRUSTED_PROXY_HOPS` | Start with `2`, then verify in step 4 |
   | `GEMINI_API_KEY` | Your Gemini API key |
   | `GEMINI_MODEL` | A current model id from the Gemini models page |

   Railway sets `PORT`; the container listens on it.
4. **Networking → Generate domain.** Note the URL, e.g. `https://your-api.up.railway.app`.
5. Migrations run automatically: the container runs `alembic upgrade head` before starting the server. The deploy log shows `Running upgrade -> 0001`. To run them by hand from your machine: `cd backend && DATABASE_URL=<neon url> uv run alembic upgrade head`.
6. Check: `curl https://your-api.up.railway.app/api/health` → `{"status":"ok"}`.

Run **one** replica: rate-limit counters are in memory (design.md section 15).

## 3. Vercel: web

1. **Add new project** and import this repository.
2. **Root directory**: `frontend` (framework preset: Next.js).
3. **Environment variable** `API_BASE_URL` = the Railway URL from step 2.4, without a trailing slash. Rewrites are fixed at build time, so **redeploy after changing it**.
4. Deploy, then copy the production URL into Railway's `APP_ORIGINS` and redeploy the API.

## 4. Verify `TRUSTED_PROXY_HOPS` on the live chain

The API reads the client IP from `X-Forwarded-For`, counting `TRUSTED_PROXY_HOPS` entries from the right. A wrong value either rate-limits everyone together or lets clients forge their IP.

1. Log in once through the Vercel URL.
2. In Railway **Deployments → Logs**, find `auth request forwarded_for_entries=N`. It records only the hop count, never addresses.
3. Expected: Vercel sets the browser's IP (replacing anything the browser sent) and Railway's edge appends Vercel's egress IP, so **N = 2 → `TRUSTED_PROXY_HOPS=2`**. The API takes the entry that many places from the right; it does not "skip" that many. With `1` it would read Vercel's rotating egress IP and per-IP limits would never trigger. Observed in production: N = 2 on every request.
4. Test it from an incognito window: 5 wrong passwords for one email → the 6th attempt returns "Too many attempts". From another network (e.g. a phone hotspot), the correct password still works.

If N is 1, Vercel is not forwarding the client IP. The per-IP limits would then group all users behind Vercel's egress IPs. Tell the team before going live.

Known limit: the Railway domain is public, so a client that bypasses Vercel can put a forged entry where Vercel's would be and evade the per-IP + email limit. The per-email slowdown (counted across all IPs) still applies. Closing this fully needs a shared secret header from Vercel or private networking (future work).

## 5. Smoke test (fresh incognito window)

1. Open the Vercel URL → redirected to `/login`.
2. **Sign up** → lands on Imports with "No imports yet".
3. Upload the orders and payments CSVs → "Imported" with the validation report.
4. Upload the same pair again → "Already imported", same import id.
5. **Open dashboard** → KPI row, chart and "Look at these first" table.
6. Click a KPI and a chart bar → the table filters; **Clear** resets it.
7. Open a row → the evidence drawer shows source row numbers.
8. **Explain with AI** → an "AI generated" badge appears. A "rule-based explanation" notice means the key or model id is wrong: check the Railway logs for `llm attempt`.
9. **AI summary** tab → **Summarise with AI**.
10. Log out → redirected to `/login`.

Also check that the cookie in DevTools → Application → Cookies is `HttpOnly`, `Secure` and `SameSite=Lax`.

## Local stack

```bash
cp .env.example .env   # set JWT_SECRET and POSTGRES_PASSWORD; Gemini values are optional
docker compose up --build
```

Open http://localhost:3000. The API is also reachable directly at http://localhost:8000/api/health.
