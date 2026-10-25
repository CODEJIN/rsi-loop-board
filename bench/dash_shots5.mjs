import { chromium } from 'playwright';
import crypto from 'node:crypto';
const B = 'http://127.0.0.1:35014';
const b32 = (s) => { const a = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; let bits = ''; for (const ch of s.replace(/=+$/, '')) bits += a.indexOf(ch).toString(2).padStart(5, '0'); const out = []; for (let i = 0; i + 8 <= bits.length; i += 8) out.push(parseInt(bits.slice(i, i + 8), 2)); return Buffer.from(out); };
const totp = (secret) => { const c = Buffer.alloc(8); c.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000))); const h = crypto.createHmac('sha1', b32(secret)).update(c).digest(); const o = h[19] & 15; return String(((h.readUInt32BE(o) & 0x7fffffff) % 1e6)).padStart(6, '0'); };
const b = await chromium.launch();
const c = await b.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' });
const p = await c.newPage(); const errs = []; p.on('pageerror', e => errs.push(String(e))); p.on('console', m => m.type() === 'error' && errs.push(m.text()));
await p.request.post(B + '/auth/login', { data: { password: 'rsi-temp-test' } });
const setup = await (await p.request.get(B + '/auth/totp/setup')).json();
await p.request.post(B + '/auth/totp/verify', { data: { code: totp(setup.secret) } });
const sr = { status: () => 'skip', text: async () => '' };
console.log('schedule', sr.status(), await sr.text());
const shot = async (ctx, hash, name, wait, full = true) => { const pg = ctx === c ? p : await ctx.newPage(); await pg.goto(`${B}/${hash}`); await pg.waitForSelector(wait); await pg.waitForTimeout(900); await pg.screenshot({ path: `out/${name}.png`, fullPage: full }); };
await shot(c, '#/', 'd5_overview', 'text=승인 대기');
await shot(c, '#/runs/20261025-142825/iter/1', 'd5_run', 'text=점수 추이');
await shot(c, '#/usage', 'd5_usage', 'text=런별 사용량');
await shot(c, '#/schedules', 'd5_sched', 'text=야간 Astro 개선');
await shot(c, '#/approvals', 'd5_appr', 'text=승인함', false);
const m = await b.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, deviceScaleFactor: 2, colorScheme: 'dark', storageState: await c.storageState() });
const mp = await m.newPage(); mp.on('pageerror', e => errs.push('M ' + String(e)));
for (const [h, n, w] of [['#/', 'd5_m_overview', 'text=승인 대기'], ['#/runs/20261025-142825/iter/1', 'd5_m_run', 'text=점수 추이'], ['#/usage', 'd5_m_usage', 'text=런별 사용량']]) {
  await mp.goto(`${B}/${h}`); await mp.waitForSelector(w); await mp.waitForTimeout(900); await mp.screenshot({ path: `out/${n}.png`, fullPage: true });
}
console.log('errors', errs); await b.close();
