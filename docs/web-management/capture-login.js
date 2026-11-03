const { chromium } = require('playwright');
const path = require('path');

const toFileUrl = (p) => 'file:///' + path.resolve(__dirname, p).replace(/\\/g, '/');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 }, deviceScaleFactor: 2 });
  await page.goto(toFileUrl('dashboard-login-mockup.html'), { waitUntil: 'networkidle' });
  await page.waitForTimeout(300);
  const dir = path.resolve(__dirname, 'images');

  // 1) login (dark default)
  await page.screenshot({ path: `${dir}/login.png` });

  // 2) login error state
  await page.fill('#pw', 'wrong-pw');
  await page.click('.btn-primary');
  await page.waitForTimeout(150);
  await page.screenshot({ path: `${dir}/login-error.png` });

  // 3) locked (503) state
  await page.click('#loginCard .alt a'); // 预览：未配置状态
  await page.waitForTimeout(150);
  await page.screenshot({ path: `${dir}/login-locked.png` });

  // 4) light theme login
  await page.click('#lockedCard .alt a'); // 返回登录
  await page.waitForTimeout(100);
  await page.click('#themeBtn'); // 切浅色
  await page.waitForTimeout(150);
  await page.screenshot({ path: `${dir}/login-light.png` });

  await browser.close();
  console.log('login screens captured');
})();
