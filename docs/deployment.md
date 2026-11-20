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
   | `TRUSTED_PROXY_HOPS` | `2` (Railway's edge rewrites `X-Forwarded-For` to `[connecting IP, edge IP]`) |
   | `CLIENT_IP_HEADER` | `x-vercel-forwarded-for` (Vercel's header with the browser IP; see step 4) |
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

## 4. Verify the client IP on the live chain

Per-IP rate limits need the browser's real IP. Verified on this deployment:
- Railway's edge **replaces** `X-Forwarded-For` with `[connecting IP, edge IP]`, so a forged value never reaches the API.
- Behind Vercel the connecting IP is Vercel's egress address, which changes between requests. The browser IP only arrives in `x-vercel-forwarded-for`, which Vercel sets itself.

So set `CLIENT_IP_HEADER=x-vercel-forwarded-for` and `TRUSTED_PROXY_HOPS=2`. The second covers requests that skip Vercel.

Test from an incognito window: 5 wrong passwords for one email, then the 6th attempt says "Too many attempts". From another network (e.g. a phone hotspot) the correct password still works.

Known limit: the Railway domain is public, so a client calling it directly can set `x-vercel-forwarded-for` itself and evade the per-IP + email limit. The per-email slowdown (counted across all IPs) still applies. A shared secret between Vercel and the API would close this (future work).

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
