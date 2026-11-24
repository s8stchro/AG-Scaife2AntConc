/** Authenticated JSON/raw API client and local-bridge chooser. */
import { S } from './state.js';

export async function api(path, body, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin',
      headers: {
        ...(body === undefined ? {} : { 'Content-Type': 'application/json', 'X-AG-CSRF': S.csrf }),
        ...(options.headers || {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch (_) {
    throw new Error('Connection to the local application was interrupted. Relaunch the app; your saved choices remain in the results folder.');
  }
  if (!response.ok) {
    let info = {};
    try {
      info = await response.json();
    } catch (_) {}
    const error = new Error(info.error || 'The request could not be completed.');
    error.code = info.code;
    error.status = response.status;
    throw error;
  }
  return options.raw ? response : response.json();
}

export async function choose(kind) {
  const result = await api(`/api/local/choose/${kind}`, {});
  return result.value;
}
