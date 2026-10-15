# Deploy a free public demo

Render (free web service) runs the API and Neon (free PostgreSQL) holds the data. Neither
should ask for a credit card; if either does, stop there rather than adding one. The demo
uses `SUGGESTER=heuristic`, so it never calls a paid API, and no secret is committed: two
values are typed into Render's dashboard.

## What the free tiers mean in practice

| | Limit |
|---|---|
| Render free web service | Sleeps after 15 minutes without traffic and takes about a minute to wake up. 750 free instance hours a month. No shell access. |
| Neon free plan | 1 GB storage and 100 compute-hours a month. The database suspends after 5 idle minutes and wakes on the next query (about a second). |
| Rate limiting | In memory, per process, so it resets whenever the service restarts or wakes. Fine for one instance. |

Render's own free Postgres is deleted after 30 days, which is why the database is on Neon.

## 1. Make a Neon database

1. Go to <https://neon.com> and sign up (GitHub, Google or email).
2. Create a project. Any name works, for example `client-onboarding`. Choose the region
   **AWS US West 2 (Oregon)** so it sits next to the Render service (`render.yaml` uses
   Oregon). If you pick another region, change `region:` in `render.yaml` to match.
3. On the project dashboard find the **Connect** button (a connection details panel).
   - Turn **Connection pooling OFF** (use the direct connection). A single small instance
     doesn't need a pooler.
   - Copy the connection string. It looks like
     `postgresql://USER:PASSWORD@ep-xxxx.us-west-2.aws.neon.tech/neondb?sslmode=require`.
4. Keep it private. It contains the database password. The app accepts it as it is
   (`postgresql://` is rewritten to the psycopg driver automatically).

## 2. Make a demo API key

The key is a secret you invent. Make a long random one on your own machine:

```bash
python -c "import secrets; print('ob_' + secrets.token_urlsafe(32))"
```

Keep it somewhere safe. You will paste it into Render in the next step, and send it in the
`X-API-Key` header. The app only ever stores its hash.

## 3. Deploy on Render

1. Make sure the latest code, including `render.yaml`, is pushed to GitHub.
2. Go to <https://render.com> and sign up with **GitHub** (so Render can see the repo).
3. In the dashboard click **New > Blueprint**.
4. Click **Connect** next to `client-onboarding-api`. If it isn't listed, use the link to
   grant Render access to that repository first.
5. Enter a name for the Blueprint, choose the branch `main`, then review what Render will
   create: one **free** web service named `client-onboarding-api`.
6. Render asks for the values of the two secret variables:
   - `DATABASE_URL`: the Neon connection string from step 1.
   - `BOOTSTRAP_API_KEY`: the key from step 2.
7. Click **Deploy Blueprint**, then open the service and watch its **Logs**. The build
   installs the requirements, then the start command applies the database migrations and
   starts the API. When you see `Uvicorn running`, it's live. The service URL is shown at the
   top of the page, like `https://client-onboarding-api-xxxx.onrender.com`.

If Render asks for a payment method on the Blueprint page, use **New > Web Service** instead
and enter the same settings by hand: connect the public repo, **Language** Python 3,
**Region** Oregon, **Instance Type** Free, **Build Command**
`pip install -r requirements.txt`, **Start Command**
`alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`, **Health Check
Path** `/health`, and the environment variables `PYTHON_VERSION=3.12.8`, `SUGGESTER=heuristic`,
`DATABASE_URL` and `BOOTSTRAP_API_KEY`.

## 4. Check it

```bash
python scripts/smoke_test.py https://YOUR-SERVICE.onrender.com
```

It asks for the key (input hidden), wakes the service if it is asleep, then checks that
`/health` and `/docs` are open, that other endpoints refuse a missing or wrong key, and
uploads `samples/messy_customers.csv`. It prints the upload status, errors and customers.

The interactive docs are at `https://YOUR-SERVICE.onrender.com/docs`. Click **Authorize**,
paste the key, and every endpoint can be tried from the page.

## Day-to-day

- **Rotate the key:** change `BOOTSTRAP_API_KEY` in Render (Environment tab). On the next
  start the old key is revoked and the new one created.
- **More keys:** there is no shell on the free tier, so run the CLI on your own machine
  against the Neon database: `DATABASE_URL="postgresql://..." python -m app.cli create-key --name alice`.
- **Wiping the demo data:** delete and recreate the Neon database (or the project).
- **Redeploys:** every push to `main` redeploys (`autoDeployTrigger: commit`).
- **Uploaded files** are stored in the database so they can be reprocessed. Don't put real
  customer data into a public demo.
- **Cost:** none. If the free hours or storage run out, Render suspends the service and
  Neon stops accepting writes until the next month; nothing is billed without a card.
