// Thin wrapper around the local JSON API. Every request stays on this machine.

async function request(method, path, body) {
  const options = { method, headers: {} };
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(`/api${path}`, options);
  } catch {
    throw new Error("Tutanak is not running.");
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) {
      detail = detail.map((d) => `${(d.loc || []).slice(-1)[0] ?? ""}: ${d.msg}`).join("; ");
    }
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body ?? {}),
  put: (path, body) => request("PUT", path, body),
  patch: (path, body) => request("PATCH", path, body),
  del: (path) => request("DELETE", path),
  upload: (path, form) => request("POST", path, form),
};

// One server-sent-event stream for the whole app; views subscribe and unsubscribe.
const listeners = new Set();
let source = null;

export function onEvent(listener) {
  listeners.add(listener);
  if (!source) {
    source = new EventSource("/api/events");
    source.onmessage = (message) => {
      let event;
      try {
        event = JSON.parse(message.data);
      } catch {
        return;
      }
      for (const fn of [...listeners]) fn(event);
    };
  }
  return () => listeners.delete(listener);
}
