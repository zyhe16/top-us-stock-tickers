# Deploy v2 to Railway

The repository includes a Dockerfile for the API. It needs no database, volume, secret, or scheduled Railway job. GitHub remains the data source of record and continues to run the weekday updater.

## Create the service

1. Create a Railway service from `zyhe16/top-us-stock-tickers`. Railway can [build a service directly from a GitHub repository](https://docs.railway.com/services#deploying-from-a-github-repo).
2. Select the `main` branch and leave the repository root as the service root.
3. Railway will find the Dockerfile and build the API image.
4. Set the [health-check path](https://docs.railway.com/deployments/healthchecks) to `/health` in the service settings.
5. Keep ["Wait for CI"](https://docs.railway.com/deployments/github-autodeploys#wait-for-ci) disabled for the initial setup. The scheduled updater pushes with GitHub's built-in `GITHUB_TOKEN`, and GitHub does not start another workflow for that push. The updater runs the test suite and validates the legacy v1 and v2 snapshots before it pushes. Normal code changes should still go through a pull request with CI.
6. [Generate a public domain](https://docs.railway.com/networking/public-networking) under Networking.

Railway injects `PORT`. The container reads it and binds to `0.0.0.0`. Do not set a fixed production port.

## HTTPS

The public URL is HTTPS. Railway [provisions and renews the certificate](https://docs.railway.com/networking/public-networking) for its generated domain. It does the same for a custom domain after you add the DNS records shown in the Railway dashboard.

The Python process listens over HTTP inside the container. Railway terminates TLS at its public edge and forwards the request to the container. Do not add certificates or private keys to this repository.

## Verify the deployment

Replace the sample host with the generated Railway domain:

```bash
curl --fail https://YOUR-SERVICE.up.railway.app/health
curl --fail 'https://YOUR-SERVICE.up.railway.app/api/v2/tickers/AAPL'
curl --fail 'https://YOUR-SERVICE.up.railway.app/api/v2/tickers?collection=sp500&limit=5'
```

Then open:

```text
https://YOUR-SERVICE.up.railway.app/docs
```

The health response should report API version `2.0.0`, dataset contract `v2`, and the same manifest hash returned in the `X-Manifest-SHA256` API header.

## Deployment behavior

Railway rebuilds the service when a commit reaches the connected branch. The weekday data update therefore refreshes the API without a separate scheduler or mutable volume.

If the updater later uses a GitHub App token or personal access token that does trigger CI, enable Railway's "Wait for CI" option then. With the current `GITHUB_TOKEN` workflow, enabling it can leave an updater commit without a CI result for Railway to wait on. See [GitHub's `GITHUB_TOKEN` event behavior](https://docs.github.com/en/actions/concepts/security/github_token#when-github_token-triggers-workflow-runs).

The application validates `data/v2/manifest.json`, its row count, schema, and file checksum before it starts serving. Railway should keep the previous healthy deployment when a new image cannot pass `/health`.

Railway uses this health check during deployment, not as continuous uptime monitoring. Add a separate uptime monitor later if you need alerts after a deployment is already live.

[Serverless sleeping](https://docs.railway.com/deployments/serverless) is optional. It can cut idle usage, but the first request after sleep can be slow and may fail once while the service wakes. Leave it disabled if the API needs predictable availability.

This file prepares the deployment. It does not prove that a Railway service or public domain has been created.
