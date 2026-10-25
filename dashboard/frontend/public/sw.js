// RSI Loop 서비스 워커 — 홈 화면 앱(PWA) 설치와 오프라인 안내용.
// 항상 네트워크를 먼저 쓰고(배포 직후에도 최신 화면), 네트워크가 안 될 때만 저장해 둔 앱 화면을 보여줍니다.
// API·인증·실시간 스트림은 절대 캐시하지 않습니다.
const CACHE = 'rsi-shell-v1';

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(['/', '/manifest.webmanifest', '/icon-192.png'])).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/auth/')) return;   // 데이터는 항상 서버에서
  e.respondWith(
    fetch(e.request).then((res) => {
      if (res.ok && (e.request.mode === 'navigate' || url.pathname.startsWith('/assets/'))) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request.mode === 'navigate' ? '/' : e.request, copy));
      }
      return res;
    }).catch(() => caches.match(e.request.mode === 'navigate' ? '/' : e.request))
  );
});
