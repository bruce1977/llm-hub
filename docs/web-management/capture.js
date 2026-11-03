const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const htmlPath = 'file:///' + path.resolve(__dirname, 'dashboard-mockup.html').replace(/\\/g, '/');
const outDir = path.resolve(__dirname, 'images');
fs.mkdirSync(outDir, { recursive: true });

const views = [
  ['dashboard', 'dashboard'],
  ['upstreams', 'upstreams'],
  ['models-llm', 'models-llm'],
  ['models-rerank', 'models-rerank'],
  ['models-systemone', 'models-systemone'],
  ['tools-websearch', 'tools-websearch'],
  ['requests', 'requests'],
  ['settings', 'settings'],
];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1340, height: 1500 },
    deviceScaleFactor: 2,
  });
  await page.goto(htmlPath, { waitUntil: 'networkidle' });

  await page.waitForTimeout(200);
  const sidebar = await page.$('.sidebar');
  await sidebar.screenshot({ path: path.join(outDir, 'nav-overview.png') });
  console.log('saved nav-overview');

  for (const [view, name] of views) {
    await page.evaluate((v) => { window.navTo(v); }, view);
    await page.waitForTimeout(450);
    const el = await page.$('.app');
    await el.screenshot({ path: path.join(outDir, name + '.png') });
    console.log('saved', name);
  }

  await page.evaluate(() => { window.navTo('requests'); window.openDrawer(); });
  await page.waitForTimeout(450);
  const drawer = await page.$('.drawer');
  await drawer.screenshot({ path: path.join(outDir, 'requests-detail.png') });
  console.log('saved requests-detail');

  await browser.close();
  console.log('CAPTURE_DONE_OK');
})().catch((e) => { console.error(e); process.exit(1); });
