// Usage: node shots.js <baseUrl> <outDir>   - visits landing + every nav page, screenshots, records inventory
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const [base, out] = process.argv.slice(2);
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.ASTRA_CHROMIUM || undefined, args: ['--use-gl=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${out}/00-landing.png` });
  const inventory = { landingButtons: await page.$$eval('button', bs => bs.map(b => b.getAttribute('aria-label') || b.innerText.trim()).filter(Boolean)) };
  await page.click('.landing-enter-dock');
  await page.waitForTimeout(1800);
  const navLabels = await page.$$eval('.astra-dock-host [aria-label], .astra-dock-host button', els => els.map(e => e.getAttribute('aria-label') || e.innerText.trim()).filter(Boolean));
  inventory.dock = [...new Set(navLabels)];
  const pages = ['Overview', 'Graph', 'Lead queue', 'Evidence', 'OSINT', 'Timeline', 'Hypotheses', 'Evaluation lab', 'Casebook', 'Settings'];
  inventory.pages = {};
  let i = 1;
  for (const p of pages) {
    const btn = page.locator(`.astra-dock-host [aria-label="${p}"]`).first();
    if (await btn.count()) { await btn.click(); } else { const b2 = page.getByRole('button', { name: p }).first(); await b2.click(); }
    await page.waitForTimeout(1500);
    const h1 = await page.$eval('.page-header h1', e => e.innerText).catch(() => null);
    const eyebrow = await page.$eval('.page-header .eyebrow', e => e.innerText).catch(() => null);
    const buttons = await page.$$eval('.page-wrap button', bs => bs.map(b => (b.getAttribute('aria-label') || b.innerText).trim()).filter(Boolean).slice(0, 60));
    const panels = await page.$$eval('.page-wrap h3', hs => hs.map(h => h.innerText.trim()));
    inventory.pages[p] = { eyebrow, h1, panels, buttonCount: buttons.length, buttons };
    await page.screenshot({ path: `${out}/${String(i).padStart(2, '0')}-${p.replace(/\W+/g, '_')}.png`, fullPage: true });
    i++;
  }
  inventory.errors = errors;
  fs.writeFileSync(`${out}/inventory.json`, JSON.stringify(inventory, null, 2));
  console.log(JSON.stringify({ pages: Object.keys(inventory.pages).length, errors: errors.slice(0, 10) }));
  await browser.close();
})();
