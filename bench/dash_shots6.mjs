import { chromium } from 'playwright';
import crypto from 'node:crypto';
const B = 'http://127.0.0.1:35014';
const b32 = (s) => { const a = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; let bits = ''; for (const ch of s.replace(/=+$/, '')) bits += a.indexOf(ch).toString(2).padStart(5, '0'); const out = []; for (let i = 0; i + 8 <= bits.length; i += 8) out.push(parseInt(bits.slice(i, i + 8), 2)); return Buffer.from(out); };
const totp = (secret) => { const c = Buffer.alloc(8); c.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000))); const h = crypto.createHmac('sha1', b32(secret)).update(c).digest(); const o = h[19] & 15; return String(((h.readUInt32BE(o) & 0x7fffffff) % 1e6)).padStart(6, '0'); };
const b = await chromium.launch();
const c = await b.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark', timezoneId: 'Asia/Seoul' });
const p = await c.newPage(); const errs = []; p.on('pageerror', e => errs.push(String(e)));
await p.request.post(B + '/auth/login', { data: { password: 'rsi-temp-test' } });
const setup = await (await p.request.get(B + '/auth/totp/setup')).json();
await p.request.post(B + '/auth/totp/verify', { data: { code: totp(setup.secret) } });
for (const [h, n, w] of [['#/', 'd6_overview', 'text=승인 대기'], ['#/runs/20261025-142825/iter/1', 'd6_run', 'text=점수 추이'], ['#/usage', 'd6_usage', 'text=런별 사용량']]) {
  await p.goto(`${B}/${h}`); await p.waitForSelector(w); await p.waitForTimeout(900); await p.screenshot({ path: `out/${n}.png` });
}
await p.click('text=더보기'); await p.waitForTimeout(300); await p.screenshot({ path: 'out/d6_more.png' });
console.log('errors', errs); await b.close();
