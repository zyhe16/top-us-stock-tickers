import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  ACTIVE_RUN_MAX_AGE_MS,
  runScheduler,
} from "../src/index.js";

const NOW = Date.parse("2026-08-31T10:17:00Z");

function response(status, body = undefined) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function githubFetchWithRuns(runs, dispatchStatus = 200) {
  const requests = [];
  const fetch = async (url, options = {}) => {
    requests.push({ url, options });
    if ((options.method ?? "GET") === "GET") {
      return response(200, { workflow_runs: runs });
    }
    if (dispatchStatus === 204) {
      return response(204);
    }
    return response(dispatchStatus, {
      workflow_run_id: 123,
      html_url: "https://github.com/example/run/123",
    });
  };
  return { fetch, requests };
}

function run(overrides = {}) {
  return {
    id: 1,
    status: "completed",
    conclusion: "success",
    created_at: "2026-08-31T10:10:00Z",
    html_url: "https://github.com/example/run/1",
    ...overrides,
  };
}

test("dispatches when no run exists for the current UTC day", async () => {
  const github = githubFetchWithRuns([]);

  const result = await runScheduler({
    fetch: github.fetch,
    githubToken: "test-token",
    now: NOW,
  });

  assert.equal(result.action, "dispatched");
  assert.equal(github.requests.length, 2);
  assert.equal(github.requests[1].options.method, "POST");
  assert.deepEqual(JSON.parse(github.requests[1].options.body), { ref: "main" });
  assert.equal(
    github.requests[1].options.headers.Authorization,
    "Bearer test-token",
  );
});

test("does not dispatch after a successful run today", async () => {
  const github = githubFetchWithRuns([run()]);

  const result = await runScheduler({
    fetch: github.fetch,
    githubToken: "test-token",
    now: NOW,
  });

  assert.equal(result.action, "skipped");
  assert.equal(result.reason, "successful-run-today");
  assert.equal(github.requests.length, 1);
});

test("retries after a completed failed run", async () => {
  const github = githubFetchWithRuns([run({ conclusion: "failure" })]);

  const result = await runScheduler({
    fetch: github.fetch,
    githubToken: "test-token",
    now: NOW,
  });

  assert.equal(result.action, "dispatched");
  assert.equal(github.requests.length, 2);
});

test("does not duplicate a recent active run", async () => {
  const github = githubFetchWithRuns([
    run({ status: "in_progress", conclusion: null }),
  ]);

  const result = await runScheduler({
    fetch: github.fetch,
    githubToken: "test-token",
    now: NOW,
  });

  assert.equal(result.action, "skipped");
  assert.equal(result.reason, "active-run");
  assert.equal(github.requests.length, 1);
});

test("retries when an active run is stale", async () => {
  const github = githubFetchWithRuns([
    run({
      status: "queued",
      conclusion: null,
      created_at: new Date(NOW - ACTIVE_RUN_MAX_AGE_MS - 1).toISOString(),
    }),
  ]);

  const result = await runScheduler({
    fetch: github.fetch,
    githubToken: "test-token",
    now: NOW,
  });

  assert.equal(result.action, "dispatched");
  assert.equal(github.requests.length, 2);
});

test("ignores a successful run from the previous UTC day", async () => {
  const github = githubFetchWithRuns([
    run({ created_at: "2026-08-30T23:59:59Z" }),
  ]);

  const result = await runScheduler({
    fetch: github.fetch,
    githubToken: "test-token",
    now: NOW,
  });

  assert.equal(result.action, "dispatched");
  assert.equal(github.requests.length, 2);
});

test("fails closed when GitHub rejects the run-history request", async () => {
  const fetch = async () => response(503, { message: "unavailable" });

  await assert.rejects(
    runScheduler({ fetch, githubToken: "test-token", now: NOW }),
    /GitHub run history request failed with 503/,
  );
});

test("requires a GitHub token", async () => {
  await assert.rejects(
    runScheduler({ fetch: async () => response(200), now: NOW }),
    /GITHUB_TOKEN is required/,
  );
});

test("accepts an empty successful dispatch response", async () => {
  const github = githubFetchWithRuns([], 204);

  const result = await runScheduler({
    fetch: github.fetch,
    githubToken: "test-token",
    now: NOW,
  });

  assert.equal(result.action, "dispatched");
  assert.equal(result.runId, undefined);
});

test("fails when GitHub rejects the dispatch", async () => {
  const github = githubFetchWithRuns([], 403);

  await assert.rejects(
    runScheduler({
      fetch: github.fetch,
      githubToken: "test-token",
      now: NOW,
    }),
    /GitHub workflow dispatch failed with 403/,
  );
});

test("Cloudflare owns the schedule and GitHub is dispatch-only", () => {
  const wrangler = JSON.parse(
    readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8"),
  );
  const workflow = readFileSync(
    new URL("../../.github/workflows/daily_update.yml", import.meta.url),
    "utf8",
  );

  assert.deepEqual(wrangler.triggers.crons, ["17 10-14 * * mon-fri"]);
  assert.deepEqual(wrangler.secrets.required, ["GITHUB_TOKEN"]);
  assert.doesNotMatch(workflow, /^\s+schedule:/m);
  assert.match(workflow, /^\s+workflow_dispatch:/m);
  assert.match(workflow, /group: daily-stock-ticker-update/);
});
