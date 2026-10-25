// 백엔드 호출 공통 래퍼 — 401이면 로그인 화면으로 돌아가도록 onUnauthorized를 부릅니다.
let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

export async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* 본문 없는 응답 */ }
  if (res.status === 401 && !path.startsWith('/auth/')) onUnauthorized();
  if (!res.ok) {
    const d = data?.detail;
    const err = new Error((typeof d === 'string' ? d : d?.message) || `요청 실패 (${res.status})`);
    err.status = res.status;
    err.errors = Array.isArray(d?.errors) ? d.errors : null;   // Spec 검사 실패 목록 등
    throw err;
  }
  return data;
}

// 새 이벤트 실시간 구독(SSE). 연결이 끊기면 브라우저가 retry 값(3초)에 따라 자동 재연결합니다.
export function subscribeEvents(onEvent, onStatus) {
  const es = new EventSource('/api/stream');
  es.onopen = () => onStatus?.('live');
  es.onerror = () => onStatus?.('reconnecting');
  es.onmessage = (m) => {
    try { onEvent(JSON.parse(m.data)); } catch { /* heartbeat 등 */ }
  };
  return () => es.close();
}
