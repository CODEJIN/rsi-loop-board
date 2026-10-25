import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api.js';

// 데이터를 불러오고, deps(실시간 이벤트 등)가 바뀌면 짧게 모아서(debounce) 다시 불러옵니다 —
// 에이전트 도구 호출처럼 이벤트가 몰려 들어와도 요청이 폭주하지 않게 합니다.
export function useApi(path, { refreshKey, shouldRefresh = () => true, debounceMs = 600, pollMs = null } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const timer = useRef(null);

  const load = useCallback(() => {
    if (!path) return;
    api(path).then((d) => { setData(d); setError(null); }).catch(setError);
  }, [path]);

  useEffect(() => { setData(null); load(); }, [load]);

  // 이벤트가 없는 값(오케스트레이터 heartbeat 등)은 주기적으로 다시 불러옵니다.
  useEffect(() => {
    if (!pollMs) return undefined;
    const t = setInterval(load, pollMs);
    return () => clearInterval(t);
  }, [load, pollMs]);

  useEffect(() => {
    if (refreshKey == null || !shouldRefresh(refreshKey)) return undefined;
    clearTimeout(timer.current);
    timer.current = setTimeout(load, debounceMs);
    return () => clearTimeout(timer.current);
    // shouldRefresh는 매 렌더 새로 만들어지는 함수라 의존성에서 뺍니다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey, load, debounceMs]);

  return { data, error, reload: load };
}
