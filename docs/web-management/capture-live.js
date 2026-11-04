/**
 * Capture the *live* console (built bundle served by the gateway) for the READMEs.
 *
 *   cd web && npm run build                 # the screenshots come from app/web
 *   cd ..   && python -m uvicorn main:app --app-dir app --port 8000
 *
 *   LLMHUB_BASE=http://127.0.0.1:8000 ADMIN_PASSWORD=<console password> \
 *     NODE_PATH=<dir with playwright> node docs/web-management/capture-live.js
 *
 * Writes one set per locale: ../screenshots/<en|zh>/<page>.jpg
 * (the console picks its language from localStorage `llmhub-console-locale`).
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = (process.env.LLMHUB_BASE || 'http://127.0.0.1:8000').replace(/\/$/, '');
const PASSWORD = process.env.ADMIN_PASSWORD || '';
const OUT_ROOT = path.resolve(__dirname, '..', 'screenshots');

const VIEWPORT = { width: 1440, height: 900 };
/** [output folder, vue-i18n locale code] — the codes must match SUPPORTED_LOCALES. */
const LOCALES = [
  ['en', 'en'],
  ['zh', 'zh-CN'],
];
const LOCALE_KEY = 'llmhub-console-locale';

/**
 * The console reads its language from localStorage on boot, so set it on the real
 * origin and reload once. (An init script would run on about:blank first, where the
 * write throws and the later navigation is free to pick navigator.language instead.)
 */
async function useLocale(page, locale) {
  await page.evaluate(([k, l]) => localStorage.setItem(k, l), [LOCALE_KEY, locale]);
  await page.reload({ waitUntil: 'networkidle' });
}

/** [file name, route, extra wait] — order is the order they appear in the READMEs. */
const PAGES = [
  ['dashboard', '/admin/', 2200],
  ['upstreams', '/admin/upstreams', 2200],
  ['models-llm', '/admin/models/llm', 2400],
  ['models-rerank', '/admin/models/rerank', 1800],
  ['models-systemone', '/admin/models/systemone', 1800],
  ['requests', '/admin/requests', 2000],
  ['tools-websearch', '/admin/tools/websearch', 1600],
  ['settings', '/admin/settings', 1600],
];

async function shot(page, file) {
  await page.screenshot({ path: file, type: 'jpeg', quality: 88 });
  const kb = Math.round(fs.statSync(file).size / 1024);
  console.log('  saved', path.basename(path.dirname(file)) + '/' + path.basename(file), kb + 'KB');
}

(async () => {
  if (!PASSWORD) throw new Error('set ADMIN_PASSWORD (the console login password)');
  const browser = await chromium.launch();

  for (const [folder, locale] of LOCALES) {
    const dir = path.join(OUT_ROOT, folder);
    fs.mkdirSync(dir, { recursive: true });
    console.log('==', folder, '(' + locale + ')', '==');

    // --- login gate: a clean context, no session cookie -----------------------
    const anon = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 2 });
    const login = await anon.newPage();
    await login.goto(BASE + '/admin/login', { waitUntil: 'networkidle' });
    await useLocale(login, locale);
    await login.waitForTimeout(700);
    await shot(login, path.join(dir, 'login.jpg'));
    await anon.close();

    // --- signed-in views -----------------------------------------------------
    const ctx = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 2 });

    // Log in through the API: the session cookie lands in the shared cookie jar.
    const res = await ctx.request.post(BASE + '/v1/management/auth/login', { data: { password: PASSWORD } });
    if (!res.ok()) throw new Error('login failed: HTTP ' + res.status());

    const page = await ctx.newPage();
    await page.goto(BASE + '/admin/', { waitUntil: 'networkidle' });
    await useLocale(page, locale);

    for (const [name, route, wait] of PAGES) {
      await page.goto(BASE + route, { waitUntil: 'networkidle' });
      await page.waitForTimeout(wait);
      await shot(page, path.join(dir, name + '.jpg'));
    }

    // --- request detail drawer ----------------------------------------------
    await page.goto(BASE + '/admin/requests', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1800);
    const detail = page.locator('tbody .link').first();
    if (await detail.count()) {
      await detail.click();
      await page.waitForTimeout(1600);
      await shot(page, path.join(dir, 'requests-detail.jpg'));
    } else {
      console.log('  skipped requests-detail (no rows in the request log)');
    }

    await ctx.close();
  }

  await browser.close();
  console.log('CAPTURE_DONE_OK');
})().catch((e) => { console.error(e); process.exit(1); });
