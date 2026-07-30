const inflight = new Map();

export class ApiError extends Error {
  constructor(message, status = 0, data = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

export async function api(path, options = {}) {
  const { key, timeout = 15000, retries = 0, ...fetchOptions } = options;
  if (key && inflight.has(key)) inflight.get(key).abort();
  const controller = new AbortController();
  if (key) inflight.set(key, controller);
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...fetchOptions,
      signal: controller.signal,
      headers: {
        ...(fetchOptions.body ? { "Content-Type": "application/json" } : {}),
        ...(fetchOptions.headers || {}),
      },
    });
    let data;
    try {
      data = await response.json();
    } catch {
      data = {};
    }
    if (!response.ok)
      throw new ApiError(
        data.error || `Request failed (${response.status})`,
        response.status,
        data,
      );
    return data;
  } catch (error) {
    if (retries > 0 && !(error instanceof ApiError && error.status < 500)) {
      await new Promise((resolve) =>
        setTimeout(resolve, 500 * 2 ** (options._attempt || 0)),
      );
      return api(path, {
        ...options,
        retries: retries - 1,
        _attempt: (options._attempt || 0) + 1,
      });
    }
    if (error.name === "AbortError")
      throw new ApiError("The request took too long. Please try again.");
    throw error instanceof ApiError
      ? error
      : new ApiError(
          "Unable to reach the server. Check your connection and retry.",
        );
  } finally {
    clearTimeout(timer);
    if (key && inflight.get(key) === controller) inflight.delete(key);
  }
}

export function post(path, data, options = {}) {
  return api(path, {
    method: "POST",
    body: JSON.stringify(data || {}),
    ...options,
  });
}

export function cancelRequest(key) {
  inflight.get(key)?.abort();
  inflight.delete(key);
}
