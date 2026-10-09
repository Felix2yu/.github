/*
 * PWA Service Worker 标准模板（契约见 ../PWA规范.md）
 *
 * 构建时把 __BUILD_VERSION__ 替换为内容/构建版本号（如 git short SHA 或 HTML mtime）。
 * 版本变化的唯一目的：让 CACHE_VERSION 变，从而在 activate 阶段淘汰旧缓存。
 *
 * 行为契约：
 *   1) 缓存名版本化（CACHE_VERSION），activate 按前缀批量删旧；
 *   2) app shell 预缓存（SHELL_ASSETS）；
 *   3) 导航请求 network-first，失败回落缓存，再回落 SHELL_ASSETS[0]；
 *   4) API GET：network-first，失败回落缓存，且缓存有条目上限（MAX_API_ENTRIES）；
 *   5) 静态资源：cache-first；
 *   6) 有离线兜底；
 *   7) 提供 SKIP_WAITING 消息以即时接管。
 */
const BUILD_VERSION = '__BUILD_VERSION__';
const CACHE_PREFIX = 'app';
const CACHE_VERSION = `${CACHE_PREFIX}-${BUILD_VERSION}`;
const MAX_API_ENTRIES = 300;

const SHELL_ASSETS = ['/', '/offline.html'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k.startsWith(`${CACHE_PREFIX}-`) && k !== CACHE_VERSION)
          .map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});

/** 写缓存并裁剪到上限（FIFO）。 */
async function putWithLimit(cacheName, request, response) {
  const cache = await caches.open(cacheName);
  await cache.put(request, response);
  const keys = await cache.keys();
  if (keys.length > MAX_API_ENTRIES) {
    for (const k of keys.slice(0, keys.length - MAX_API_ENTRIES)) {
      await cache.delete(k);
    }
  }
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // 3) 导航请求：network-first，失败回落缓存/壳页
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((res) => {
          if (res.ok) {
            const clone = res.clone();
            caches.open(CACHE_VERSION).then((c) => c.put(request, clone));
          }
          return res;
        })
        .catch(async () => (await caches.match(request)) || caches.match(SHELL_ASSETS[0]))
    );
    return;
  }

  // 4) API GET：network-first + 缓存回落 + 条目上限
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(request)
        .then((res) => {
          if (res.ok) putWithLimit(CACHE_VERSION, request, res.clone());
          return res;
        })
        .catch(async () => (await caches.match(request)) || Response.error())
    );
    return;
  }

  // 5) 静态资源：cache-first
  event.respondWith(
    caches.match(request).then(
      (cached) =>
        cached ||
        fetch(request).then((res) => {
          if (res.ok) putWithLimit(CACHE_VERSION, request, res.clone());
          return res;
        })
    )
  );
});
