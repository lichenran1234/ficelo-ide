/* Every call to the server. Errors arrive as the message the server chose,
   which is written for a person, so callers can show it directly. */

async function call(route, params, options) {
  const url = new URL(route, window.location.origin);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null) {
      if (Array.isArray(value)) value.forEach((item) => url.searchParams.append(key, item));
      else url.searchParams.set(key, value);
    }
  });
  let response;
  try {
    response = await fetch(url, options);
  } catch (err) {
    /* fetch rejects only on network failure, which here means the server
       stopped. Worth saying plainly rather than showing "Failed to fetch". */
    throw new Error("the ficelo server is not responding");
  }
  const body = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok) {
    const error = new Error(body.error || "request failed");
    error.status = response.status;
    throw error;
  }
  return body;
}

function post(route, payload) {
  return call(route, null, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export const api = {
  config: () => call("/api/config"),
  tree: (path, hidden) => call("/api/tree", { path, hidden: hidden === undefined ? undefined : Number(hidden) }),
  info: (path) => call("/api/info", { path }),
  watch: (paths) => post("/api/watch", { paths }),
  read: (path) => call("/api/file", { path }),
  write: (path, text, stamp) =>
    call("/api/file", null, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, text, stamp }),
    }),
  create: (path, kind) => post("/api/create", { path, kind }),
  rename: (from, to) => post("/api/rename", { from, to }),
  remove: (path) => post("/api/delete", { path }),
  search: (query, limit, hidden, scope) =>
    call("/api/search", {
      q: query,
      limit,
      hidden: hidden === undefined ? undefined : Number(hidden),
      scope: scope && scope !== "/" ? scope : undefined,
    }),
  gitRepositories: (scopes, query) => call("/api/git/repos", { scope: scopes, q: query }),
  gitStatus: (base, repo) => call("/api/git/status", { base, repo }),
  gitDiff: (path, rev, base, repo) => call("/api/git/diff", { path, rev, base, repo }),
  gitCommits: (limit, repo, base) => call("/api/git/commits", { limit, repo, base }),
  gitCommit: (sha, repo, full = false) => call("/api/git/commit", { sha, repo, full: full ? 1 : undefined }),
};
