/** Keep one connection per browser profile, including while all its tabs are hidden. */
import { api } from './api.js';

const membership = 'ag-workbench-open-windows';
const changes = 'ag-workbench-window-change';
let group = crypto.randomUUID();
let presence;
let release;
let membershipLock;
let leaving = false;

export async function connectWindow() {
  if (navigator.locks) {
    await navigator.locks.request(changes, async () => {
      try {
        const storedGroup = localStorage.getItem('ag-window-group') || group;
        localStorage.setItem('ag-window-group', storedGroup);
        group = storedGroup;
      } catch {
        return;
      }
      const lifetime = new Promise(resolve => { release = resolve; });
      await new Promise(registered => {
        membershipLock = navigator.locks.request(membership, { mode: 'shared' }, () => {
          registered();
          return lifetime;
        });
      });
      // Browser locks pass to a remaining tab even when the owner crashes.
      // Sharing a stream leaves HTTP connections available for saves and exports.
      navigator.locks.request('ag-workbench-connection', async () => {
        if (leaving) return;
        presence = new EventSource(`/api/windows/${group}`);
        await lifetime;
        presence.close();
      });
    });
  }
  if (!membershipLock) presence = new EventSource(`/api/windows/${group}`);
}

export async function disconnectWindow() {
  leaving = true;
  presence?.close();
  release?.();
  await membershipLock;
}

export async function quitWindow() {
  const quit = () => api(`/api/windows/${group}/quit`, {});
  if (!membershipLock) {
    const result = await quit();
    await disconnectWindow();
    return result;
  }
  return navigator.locks.request(changes, async () => {
    const { held } = await navigator.locks.query();
    const otherWindows = held.filter(lock => lock.name === membership).length > 1;
    const result = otherWindows ? { status: 'other_windows' } : await quit();
    await disconnectWindow();
    return result;
  });
}
