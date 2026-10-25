import { chromium } from 'playwright';
const B = 'http://127.0.0.1:35004', CODE = process.env.CODE;
const RUN = '20261025-110804';
const b = await chromium.launch();
for (const [name, vp, mobile] of [['desk', { width: 1440, height: 1000 }, false], ['mobile', { width: 390, height: 844 }, true]]) {
  const c = await b.newContext({ viewport: vp, isMobile: mobile, deviceScaleFactor: mobile ? 2 : 1, colorScheme: 'dark' });
  const p = await c.newPage(); const errs = []; p.on('pageerror', e => errs.push(String(e))); p.on('console', m => m.type() === 'error' && errs.push(m.text()));
  await p.goto(B); await p.waitForSelector('input[type=password]');
  if (name === 'desk') await p.screenshot({ path: `out/dash_login.png` });
  await p.fill('input[type=password]', 'rsi-temp-test');
  await p.fill('input[inputmode=numeric]', CODE);
  await p.click('button.primary'); await p.waitForSelector('text=구독 사용량', { timeout: 10000 });
  await p.waitForTimeout(800); await p.screenshot({ path: `out/dash_${name}_overview.png`, fullPage: true });
  await p.goto(`${B}/#/runs/${RUN}/iter/1`); await p.waitForSelector('text=점수 추이'); await p.waitForTimeout(800);
  await p.click('text=diff 보기'); await p.waitForSelector('pre.diff'); await p.waitForTimeout(300);
  await p.screenshot({ path: `out/dash_${name}_run.png`, fullPage: true });
  console.log(name, 'errors', errs);
  await c.close();
}
await b.close();
