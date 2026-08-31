const GITHUB_API_VERSION = "2026-03-10";
const REPOSITORY = "zyhe16/top-us-stock-tickers";
const WORKFLOW = "daily_update.yml";
const WORKFLOW_REF = "main";

export const ACTIVE_RUN_MAX_AGE_MS = 90 * 60 * 1000;

const ACTIVE_RUN_STATUSES = new Set([
  "in_progress",
  "pending",
  "queued",
  "requested",
  "waiting",
]);

function githubHeaders(githubToken) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${githubToken}`,
    "Content-Type": "application/json",
    "User-Agent": "top-us-stock-tickers-scheduler",
    "X-GitHub-Api-Version": GITHUB_API_VERSION,
  };
}

function startOfUtcDay(now) {
  const start = new Date(now);
  start.setUTCHours(0, 0, 0, 0);
  return start.getTime();
}

function runsForUtcDay(runs, now) {
  const dayStart = startOfUtcDay(now);
  return runs.filter((run) => {
    const createdAt = Date.parse(run.created_at);
    return Number.isFinite(createdAt) && createdAt >= dayStart && createdAt <= now;
  });
}

function successfulRun(runs) {
  return runs.find(
    (run) => run.status === "completed" && run.conclusion === "success",
  );
}

function recentActiveRun(runs, now) {
  return runs.find((run) => {
    if (!ACTIVE_RUN_STATUSES.has(run.status)) {
      return false;
    }
    const createdAt = Date.parse(run.created_at);
    return now - createdAt <= ACTIVE_RUN_MAX_AGE_MS;
  });
}

async function responseBody(response) {
  const body = await response.text();
  return body ? JSON.parse(body) : {};
}

export async function runScheduler({
  fetch: fetchImpl = globalThis.fetch,
  githubToken,
  now = Date.now(),
}) {
  if (!githubToken) {
    throw new Error("GITHUB_TOKEN is required");
  }

  const headers = githubHeaders(githubToken);
  const workflowUrl =
    `https://api.github.com/repos/${REPOSITORY}/actions/workflows/${WORKFLOW}`;
  const runsResponse = await fetchImpl(`${workflowUrl}/runs?per_page=50`, {
    headers,
  });
  if (!runsResponse.ok) {
    throw new Error(
      `GitHub run history request failed with ${runsResponse.status}`,
    );
  }

  const runHistory = await runsResponse.json();
  const todaysRuns = runsForUtcDay(runHistory.workflow_runs ?? [], now);
  const success = successfulRun(todaysRuns);
  if (success) {
    return {
      action: "skipped",
      reason: "successful-run-today",
      runId: success.id,
      runUrl: success.html_url,
    };
  }

  const active = recentActiveRun(todaysRuns, now);
  if (active) {
    return {
      action: "skipped",
      reason: "active-run",
      runId: active.id,
      runUrl: active.html_url,
    };
  }

  const dispatchResponse = await fetchImpl(`${workflowUrl}/dispatches`, {
    method: "POST",
    headers,
    body: JSON.stringify({ ref: WORKFLOW_REF }),
  });
  if (!dispatchResponse.ok) {
    throw new Error(`GitHub workflow dispatch failed with ${dispatchResponse.status}`);
  }

  const dispatch = await responseBody(dispatchResponse);
  return {
    action: "dispatched",
    runId: dispatch.workflow_run_id,
    runUrl: dispatch.html_url,
  };
}

export default {
  async scheduled(controller, env, _context) {
    const result = await runScheduler({
      githubToken: env.GITHUB_TOKEN,
      now: controller.scheduledTime,
    });
    console.log(JSON.stringify(result));
  },
};
