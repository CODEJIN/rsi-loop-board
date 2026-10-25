// Tonight's Setup 필터 갱신 속도 벤치마크 (RSI Loop Phase 0, 시나리오 S1 "집 베란다").
//
// 한 라운드 = 새 페이지에서 사용자가 실제로 하는 순서를 그대로 재현합니다.
//   1) 장비 조합 선택 (필터 없는 전체 목록)
//   2) W-az: 전체 목록에서 방위 N 끄기/켜기 (가장 무거운 경우)
//   3) S1 설정 과정: 카테고리 → 슬라이더 → 방위 5개 끄기 (필터는 새로고침 시 초기화되므로 매번 반복하는 과정)
//   4) S1 상태에서의 조작: 방위/고도/화각/카테고리 토글
// 각 조작은 "클릭 → 메인 스레드가 다시 조용해질 때까지(settle)" 시간을 잽니다.
// 라운드 0은 워밍업이며, 이때 조작마다 목록 텍스트를 스냅샷으로 저장합니다(정확성 gate용).
//
// 사용: node tonight.mjs --base http://127.0.0.1:35103 --rounds 5 [--cpu 4] --out result.json [--snapshot snap.json]
import { chromium } from 'playwright';
import fs from 'node:fs';

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, arr) => {
  if (a.startsWith('--')) acc.push([a.slice(2), arr[i + 1]]);
  return acc;
}, []));
const BASE = args.base || 'http://127.0.0.1:35103';
const ROUNDS = Number(args.rounds || 5);
const CPU_THROTTLE = Number(args.cpu || 1);
const PASSWORD = process.env.BENCH_PASSWORD || 'rsi-bench';
// 측정 재현성을 위해 브라우저의 현재 시각(Date)을 고정합니다(타이머·performance는 실제 값).
const FIXED_TIME = new Date(args.time || '2026-11-15T21:00:00+09:00');

const S1 = {
  telescope: 'Seestar S30 Pro Telephoto Lens',
  camera: 'Seestar S30 Pro Telephoto Camera',
};
// 슬라이더 인덱스: 고급 필터의 DualRangeSlider 순서 (고도, 화각 채움, 난이도, 촬영 시간대) × (min, max)
const R = { altMin: 0, altMax: 1, fitMin: 2, fitMax: 3, diffMin: 4, diffMax: 5 };
const compass = (dir) => `window.__bench.compass('${dir}').dispatchEvent(new MouseEvent('click', { bubbles: true }))`;
const button = (text) => `window.__bench.button(${JSON.stringify(text)}).click()`;
const range = (idx, v) => `window.__bench.setRange(${idx}, ${v})`;

// [그룹, 라벨, 실행 코드]
const STEPS = [
  ['W-az', 'N-off', compass('N')],
  ['W-az', 'N-on', compass('N')],
  ['S1-setup', 'cat-galaxy-off', button('은하')],
  ['S1-setup', 'cat-other-off', button('기타')],
  ['S1-setup', 'alt-max-55', range(R.altMax, 55)],
  ['S1-setup', 'fit-min-30', range(R.fitMin, 30)],   // 화각 채움 상한은 슬라이더 끝(제한 없음) 유지
  ['S1-setup', 'diff-max-50', range(R.diffMax, 50)],
  ['S1-setup', 'az-N-off', compass('N')],
  ['S1-setup', 'az-NE-off', compass('NE')],
  ['S1-setup', 'az-E-off', compass('E')],
  ['S1-setup', 'az-W-off', compass('W')],
  ['S1-setup', 'az-NW-off', compass('NW')],
  ['S1-az', 'SE-off', compass('SE')],
  ['S1-az', 'SE-on', compass('SE')],
  ['S1-alt', 'alt-60', range(R.altMax, 60)],
  ['S1-alt', 'alt-55', range(R.altMax, 55)],
  ['S1-fit', 'fit-50', range(R.fitMin, 50)],
  ['S1-fit', 'fit-30', range(R.fitMin, 30)],
  ['S1-cat', 'cluster-off', button('성단')],
  ['S1-cat', 'cluster-on', button('성단')],
];

// ---- 페이지 안에서 실행되는 헬퍼 ----
const PAGE_HELPERS = `
window.__bench = {
  nextFrame: () => new Promise(r => requestAnimationFrame(() => setTimeout(r, 0))),
  rows: () => Array.from(document.querySelectorAll('img'))
    .filter(i => i.src.includes('sky-thumbnail'))
    // textContent: 화면에 그려졌는지와 무관한 DOM 내용 — innerText는 content-visibility로 그리기를 미룬 행에서
    // 빈 문자열이 되어, 보이는 결과는 같은데도 스냅샷이 달라지는 문제가 있었습니다(2026-10-25 런 135918).
    .map(i => i.parentElement.textContent.replace(/\\s+/g, ' ').trim()),
  ranges: () => Array.from(document.querySelectorAll('.dual-range input[type=range]')),
  setRange: (idx, v) => {
    const input = window.__bench.ranges()[idx];
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, String(v));
    input.dispatchEvent(new Event('input', { bubbles: true }));
  },
  compass: (dir) => Array.from(document.querySelectorAll('svg g'))
    .find(g => g.querySelector(':scope > text')?.textContent.trim() === dir),
  button: (text) => Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === text),
  // 조작 → 메인 스레드가 다시 조용해질 때까지(settle). Long Task(50ms 초과 작업)를 관찰해서, 마지막
  // long task가 끝난 뒤 quietMs 동안 새 작업이 없으면 끝난 것으로 봅니다. 첫 프레임만 재거나 짧은 rAF
  // 창만 보면, 클릭 후 수백 ms 뒤에 시작되는 차트 재렌더(Recharts)를 놓칩니다(Phase 0에서 실제 확인).
  now: () => performance.now(),
  async measure(action, { quietMs = 1200, timeoutMs = 30000 } = {}) {
    const B = window.__bench;
    const lts = [];
    const obs = new PerformanceObserver(l => { for (const e of l.getEntries()) lts.push(e); });
    obs.observe({ type: 'longtask' });
    const t0 = B.now();
    action();
    await B.nextFrame();
    const firstFrame = B.now() - t0;
    const mine = () => lts.filter(e => e.startTime + e.duration >= t0);
    const lastEnd = () => { const xs = mine(); return xs.length ? Math.max(...xs.map(e => e.startTime + e.duration)) : t0 + firstFrame; };
    let timedOut = false;
    while (true) {
      await new Promise(r => setTimeout(r, 100));
      if (B.now() - lastEnd() >= quietMs) break;
      if (B.now() - t0 > timeoutMs) { timedOut = true; break; }
    }
    obs.disconnect();
    return {
      ms: lastEnd() - t0,
      firstFrame,
      tbt: mine().reduce((sum, e) => sum + Math.max(0, e.duration - 50), 0),
      rows: B.rows().length,
      timedOut,
    };
  },
};
`;

const median = (xs) => { const s = [...xs].sort((a, b) => a - b); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

async function runRound(context, snapshot) {
  const page = await context.newPage();
  // 시각 고정은 Date만 덮어씁니다. Playwright의 page.clock은 performance까지 가짜로 바꿔서
  // Long Task 기록과 타임라인이 어긋나는 것을 Phase 0에서 확인했습니다.
  await page.addInitScript(fixed => {
    const RealDate = Date;
    class FixedDate extends RealDate {
      constructor(...a) { if (a.length === 0) super(fixed); else super(...a); }
      static now() { return fixed; }
    }
    window.Date = FixedDate;
  }, FIXED_TIME.getTime());
  if (CPU_THROTTLE > 1) {
    const cdp = await context.newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: CPU_THROTTLE });
  }
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.addScriptTag({ content: PAGE_HELPERS });
  await page.getByRole('button', { name: "🔭 Tonight's Setup" }).click();
  await page.getByRole('button', { name: S1.telescope, exact: true }).click();
  await page.getByRole('button', { name: S1.camera, exact: true }).click();
  await page.waitForFunction(() => window.__bench.rows().length > 0 && window.__bench.ranges().length >= 6);
  // 장비 선택 직후의 초기 계산이 끝날 때까지 대기
  await page.evaluate(() => window.__bench.measure(() => {}));

  const results = [];
  for (const [group, label, code] of STEPS) {
    const r = await page.evaluate(c => window.__bench.measure(() => (0, eval)(c)), code);
    results.push({ group, label, ...r });
    if (snapshot) snapshot[`${group}:${label}`] = await page.evaluate(() => window.__bench.rows());
  }
  await page.close();
  return { results, errors };
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const baseHost = new URL(BASE).host;
  // 외부 요청(Aladin 등)은 차단하고, 썸네일은 백엔드 프록시(/api/sky-thumbnail)가 다시 외부로
  // 요청하므로 이것도 차단합니다 — 측정을 네트워크와 무관하게 만듭니다.
  await context.route('**/*', route => {
    const u = new URL(route.request().url());
    return (u.host === baseHost && !u.pathname.startsWith('/api/sky-thumbnail')) ? route.continue() : route.abort();
  });
  const login = await context.request.post(`${BASE}/auth/login`, { data: { password: PASSWORD } });
  if (!login.ok()) throw new Error(`login failed: ${login.status()}`);

  const snapshot = { scenario: 'S1', fixedTime: FIXED_TIME.toISOString() };
  const perStep = {};      // "그룹:라벨" → [ms...]
  const perStepTbt = {};
  const errors = [], timeouts = [];
  for (let round = 0; round <= ROUNDS; round++) {
    const { results, errors: errs } = await runRound(context, round === 0 ? snapshot : null);
    errors.push(...errs);
    if (round === 0) continue;
    for (const r of results) {
      const key = `${r.group}:${r.label}`;
      (perStep[key] ||= []).push(r.ms);
      (perStepTbt[key] ||= []).push(r.tbt);
      if (r.timedOut) timeouts.push(key);
    }
  }
  await browser.close();

  const steps = {};
  const groups = {};
  for (const [key, xs] of Object.entries(perStep)) {
    const m = median(xs);
    steps[key] = { median_ms: +m.toFixed(1), min_ms: +Math.min(...xs).toFixed(1), max_ms: +Math.max(...xs).toFixed(1), median_tbt_ms: +median(perStepTbt[key]).toFixed(1) };
    const g = key.split(':')[0];
    groups[g] = +((groups[g] || 0) + m).toFixed(1);
  }
  // objective: S1 세션 하나에서 사용자가 기다리는 총 시간(모든 조작의 settle 중앙값 합).
  const objective = +Object.values(groups).reduce((a, b) => a + b, 0).toFixed(1);
  const rowsAt = Object.fromEntries(Object.entries(snapshot).filter(([, v]) => Array.isArray(v)).map(([k, v]) => [k, v.length]));
  const result = { base: BASE, rounds: ROUNDS, cpuThrottle: CPU_THROTTLE, fixedTime: FIXED_TIME.toISOString(),
    objective_total_wait_ms: objective, groups_ms: groups, steps, rowsAt, errors, timeouts };
  if (args.out) fs.writeFileSync(args.out, JSON.stringify(result, null, 2));
  if (args.snapshot) fs.writeFileSync(args.snapshot, JSON.stringify(snapshot, null, 2));
  console.log(JSON.stringify({ objective_total_wait_ms: objective, groups_ms: groups, rowsAt, errors, timeouts }, null, 2));
}

main().catch(e => { console.error(e); process.exit(1); });
