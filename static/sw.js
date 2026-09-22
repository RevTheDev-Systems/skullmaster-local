/*
 * SkullMaster iQ service worker — the offline app shell, nothing more.
 *
 * This is a private notebook: a stale cached answer would be worse than no
 * answer, so the worker NEVER caches or intercepts API, auth, health, or media
 * traffic. It only:
 *   - pre-caches the static shell so the app opens instantly (and can show the
 *     sign-in screen offline);
 *   - serves navigations network-first, falling back to the cached shell;
 *   - serves /static/* stale-while-revalidate.
 *
 * Registered only in a secure context (HTTPS or localhost) — see app.js /
 * login.js. Bump CACHE_VERSION when the shell changes materially.
 */
const CACHE_VERSION = 'skullmaster-shell-v1';

const SHELL = [
  '/',
  '/login',
  '/static/style.css',
  '/static/login.css',
  '/static/app.js',
  '/static/login.js',
  '/static/favicon.png',
  '/static/logo.png',
  '/static/apple-touch-icon.png',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/icon-maskable-512.png',
  '/static/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key)),
      ))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') { return; }

  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  if (url.origin !== self.location.origin) { return; }

  // Private/dynamic traffic always goes straight to the network, untouched:
  // API + auth + health, plus anything that is not the app shell.
  if (url.pathname.startsWith('/api/') || url.pathname === '/healthz'
      || url.pathname === '/health' || url.pathname === '/favicon.ico') {
    return;
  }

  // Navigations: network-first so updates always land; cached shell offline.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match(request).then((cached) => cached || caches.match('/login'))),
    );
    return;
  }

  // Static assets: stale-while-revalidate.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then((cached) => {
        const fromNetwork = fetch(request).then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(request, copy));
          }
          return response;
        }).catch(() => cached);
        return cached || fromNetwork;
      }),
    );
  }
});
