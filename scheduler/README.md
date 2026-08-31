# External update scheduler

Cloudflare Cron Triggers own the weekday schedule. GitHub Actions only executes
the explicitly dispatched update workflow.

The Worker runs at minute 17 of each hour from 10:00 through 14:00 UTC on
weekdays. At each invocation it reads the current day's workflow runs:

- A successful run stops further dispatches for that UTC day.
- A queued or running job younger than 90 minutes suppresses duplicates.
- A failed run, a stale active run, or no run causes another dispatch attempt.

GitHub workflow concurrency permits one updater at a time. The hourly checks are
recovery opportunities, not five unconditional updates.

## Authentication

The Worker requires a fine-grained GitHub token stored as the encrypted
Cloudflare secret `GITHUB_TOKEN`. Limit the token to
`zyhe16/top-us-stock-tickers` and grant only `Actions: read and write`.

Do not put the token in `wrangler.jsonc`, an environment file, shell history, or
Git. Run the setup wizard from the repository root:

```bash
./scripts/setup_cloudflare_scheduler.sh
```

## Local checks

The unit tests mock every GitHub request and need no credentials:

```bash
cd scheduler
npm ci
npm test
```

Wrangler can invoke the scheduled handler locally through its test endpoint,
but that path requires a local `GITHUB_TOKEN` and calls the real GitHub API. Do
not use a production token for routine unit tests.

## Hosted verification

After deployment, verify the secret name, Worker deployment, and cron trigger:

```bash
cd scheduler
npx wrangler secret list
npx wrangler deployments list
```

The deploy output must list `17 10-14 * * mon-fri`. Cloudflare may take up to
15 minutes to propagate a Cron Trigger change. The dashboard shows the trigger
under **Workers & Pages > top-us-stock-tickers-scheduler > Settings > Triggers**.
Worker logs contain only the action, reason, and GitHub run URL. They never
contain the token.
