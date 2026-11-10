// Thin client for the worker's own HTTP API (docs/API.md).

export async function api(path, { method = "GET", body, headers = {} } = {}) {
  const init = { method, headers: { ...headers } };
  if (body instanceof Blob) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, init);
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    // Non-JSON error pages fall through to the status text below.
  }
  if (!response.ok) throw new Error(payload.error || `${response.status} ${response.statusText}`);
  return payload;
}

export const get = (path) => api(path);
export const post = (path, body = {}) => api(path, { method: "POST", body });
