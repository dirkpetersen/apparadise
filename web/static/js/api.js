// The ONLY way the frontend talks to /api. It always sends the CSRF header
// (`X-Requested-With: apparadise`, required on state-changing requests) and the
// session cookie, and turns failures into ApiError. A 401 on any call except
// login announces "apparadise:unauthorized" so app.js can show the login view.

export class ApiError extends Error {
  constructor(status, message, { retryAfter = null, body = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter; // seconds, from Retry-After on 429
    this.body = body; // the parsed JSON error body (extra fields such as requiresConfirmation)
  }
}

export async function api(path, { method = "GET", json, signal, announce401 = true } = {}) {
  const headers = { "X-Requested-With": "apparadise", Accept: "application/json" };
  const init = { method, headers, credentials: "same-origin", signal };
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  }
  let res;
  try {
    res = await fetch(`/api${path}`, init);
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError(0, "Cannot reach the server. Check your connection.");
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (res.ok) return body;
  if (res.status === 401 && announce401) {
    window.dispatchEvent(new CustomEvent("apparadise:unauthorized"));
  }
  const detail = typeof body?.detail === "string" ? body.detail : `Request failed (${res.status}).`;
  const ra = Number.parseInt(res.headers.get("Retry-After") ?? "", 10);
  throw new ApiError(res.status, detail, { retryAfter: Number.isFinite(ra) ? ra : null, body });
}

// {demo: bool}; true only when mock/demo.py wrapped the app (never in production).
export const getDemo = () => api("/demo", { announce401: false });
export const getMe = () => api("/me", { announce401: false });
export const login = (loginName, password) =>
  api("/login", { method: "POST", json: { login: loginName, password }, announce401: false });
export const logout = () => api("/logout", { method: "POST", announce401: false });

const enc = encodeURIComponent;

// The public catalog. list -> {packages: [{name, title, description, source, visibility, tags,
// votes, latest, targets: {vr|rtx|generic: status}, published, status}], targets}.
// Restricted packages are only included for sysadmins (the server decides).
export const listCatalog = (q = "") => api(`/catalog${q ? `?q=${enc(q)}` : ""}`, { announce401: false });
// One package: summary fields plus homepage, aliases, executables and versions:
// [{version, targets: {target: {status, stage, digest, test, built, commands?}}}].
// `commands` (published builds only) = {module, srunRegistry, srunSqsh, pull, sqsh}.
export const getPackage = (name) => api(`/catalog/${enc(name)}`, { announce401: false });


// Sysadmin (RCO-Admins) only; the server enforces the role.
// builds -> {builds: [{id, package, version, source, target, platform, status, stage,
//   restricted, requested_by, image_digest, failure_id, created_at, started_at, finished_at,
//   builder, hasLog}]}; log -> {log, size} (the raw tail, unscrubbed).
export const listBuilds = (status = "") => api(`/admin/builds${status ? `?status=${enc(status)}` : ""}`);
export const getBuildLog = (id) => api(`/admin/builds/${enc(id)}/log`);
// queue -> {source, rule, reason, cudaPort, queued: [{id, target, platform}], skipped: [{target, platform, reason}]}
export const queueBuild = (body) => api("/admin/builds", { method: "POST", json: body });
export const listBuilders = () => api("/admin/builders");
export const disableBuilder = (id) => api(`/admin/builders/${enc(id)}/disable`, { method: "POST" });
// -> {token, expiresAt}: shown once, a one-time secret; keep it in memory only.
export const createBuilderToken = () => api("/admin/builder-tokens", { method: "POST" });

// The registry (Harbor, read-only). overview -> {configured, host, projects: [{name,
// repositories: [{name, artifacts, pulls, updated, package}]}]}; `restricted` and `cache`
// only for sysadmins. repository -> {project, name, host, package, artifacts: [{digest, tags,
// size, pushed, platforms, index, signed, commands: {podman, srun, enroot}}]}.
export const getRegistry = () => api("/registry", { announce401: false });
export const getRepository = (project, name) =>
  api(`/registry/${enc(project)}/${name.split("/").map(enc).join("/")}`, { announce401: false });
