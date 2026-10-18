const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ executablePath: process.env.ASTRA_CHROMIUM || undefined });
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  await p.goto(process.argv[2], { waitUntil: 'networkidle' });
  await p.click('.landing-enter-dock'); await p.waitForTimeout(2500);
  const banner = await p.locator('.hypothesis-note').first().innerText();
  const footer = await p.locator('.app-footer').innerText();
  const status = await p.locator('.system-status').innerText();
  console.log(JSON.stringify({ banner: banner.slice(0, 200), footer, status }));
  await p.screenshot({ path: (process.argv[3] || '.') + '/backend-down.png' });
  await b.close();
})();
