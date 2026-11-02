#!/usr/bin/env node
/* Real-browser QA sweep for LifeOS (run manually; not part of pytest).
 *
 * Signs in, visits pages at desktop and phone sizes in light and dark themes, saves screenshots, and fails (exit 1) on:
 *   - horizontal overflow (page wider than the screen)       - JavaScript errors / failed requests
 *   - tap targets smaller than 24x24 CSS px (WCAG 2.5.8)      - HTTP error responses
 * Server-side tests cannot catch these; every layout bug found so far came from this sweep.
 *
 * Usage:  see qa/README.md
 * Env:    BASE_URL (default http://127.0.0.1:8000)  QA_EMAIL  QA_PASSWORD  QA_PAGES (JSON [[name, path], ...])
 *         CHROME_PATH (use an installed Chrome/Chromium)  QA_OUT (default qa/out)  QA_MODES (JSON, default all four)
 */
const fs = require('fs');
const path = require('path');
const BASE = process.env.BASE_URL || 'http://127.0.0.1:8000';
const EMAIL = process.env.QA_EMAIL, PASSWORD = process.env.QA_PASSWORD;
const OUT = process.env.QA_OUT || path.join(__dirname, 'out');
const MODES = JSON.parse(process.env.QA_MODES || '["desktop-light","mobile-light","desktop-dark","mobile-dark"]');
const DEFAULT_PAGES = [['dashboard', '/'], ['reminders', '/reminders/'], ['goals', '/goals/'], ['goal-new', '/goals/new/'], ['planner-today', '/planner/'],
  ['planner-week', '/planner/week/'], ['tasks', '/planner/tasks/'], ['habits', '/habits/'], ['finance', '/finance/'], ['finance-reports', '/finance/reports/'],
  ['accounts', '/finance/accounts/'], ['transactions', '/finance/transactions/'], ['transaction-new', '/finance/transactions/new/'], ['budgets', '/finance/budgets/'],
  ['debts', '/finance/debts/'], ['debt-planner', '/finance/debts/planner/?currency=GBP&monthly_budget=300'], ['savings', '/finance/savings/'],
  ['plans', '/future-plans/'], ['affordability', '/future-plans/affordability/?cost=5000&saved=500&monthly_available=400'], ['interests', '/interests/'],
  ['interest-new', '/interests/new/'], ['reviews', '/reviews/'], ['journal', '/journal/'], ['insights', '/insights/'], ['settings', '/accounts/settings/']];
const PAGES = process.env.QA_PAGES ? JSON.parse(process.env.QA_PAGES) : DEFAULT_PAGES;
const VIEW = { desktop: { width: 1366, height: 900, deviceScaleFactor: 1 }, mobile: { width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true } };

async function launch() {
  const opts = { headless: 'shell' };
  if (process.env.CHROME_PATH) return require('puppeteer-core').launch({ ...opts, executablePath: process.env.CHROME_PATH, args: ['--no-sandbox'] });
  try { return await require('puppeteer').launch({ ...opts, args: ['--no-sandbox'] }); } catch (e) { /* fall through */ }
  const chromium = require('@sparticuz/chromium').default || require('@sparticuz/chromium');
  return require('puppeteer-core').launch({ ...opts, args: [...chromium.args, '--no-sandbox'], executablePath: await chromium.executablePath() });
}

(async () => {
  if (!EMAIL || !PASSWORD) { console.error('Set QA_EMAIL and QA_PASSWORD (use the demo account: python manage.py seed_demo).'); process.exit(2); }
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launch(); const page = await browser.newPage();
  const problems = []; let checked = 0;
  for (const mode of MODES) {
    const [device, scheme] = mode.split('-');
    const cks = await page.cookies(); if (cks.length) await page.deleteCookie(...cks);
    await page.setViewport(VIEW[device]);
    await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: scheme }]);
    let errors = [];
    page.removeAllListeners('pageerror'); page.removeAllListeners('requestfailed'); page.removeAllListeners('console');
    page.on('pageerror', e => errors.push('js error: ' + e.message));
    page.on('requestfailed', r => errors.push('request failed: ' + r.url()));
    page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
    await page.goto(BASE + '/accounts/login/', { waitUntil: 'networkidle0' });
    await page.type('#id_username', EMAIL); await page.type('#id_password', PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle0' }), page.click('button[type=submit]')]);
    for (const [name, p] of PAGES) {
      errors = [];
      const resp = await page.goto(BASE + p, { waitUntil: 'networkidle0' });
      await new Promise(r => setTimeout(r, 500));                                  // let charts settle
      const m = await page.evaluate(() => {
        const vw = document.documentElement.clientWidth;
        const small = [...document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, summary')].filter(e => {
          const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
          if (!r.width || !r.height || cs.visibility === 'hidden' || e.closest('.sr-only')) return false;
          const inline = e.tagName === 'A' && cs.display === 'inline';                // WCAG exempts links inside sentences
          const isCheck = e.type === 'checkbox' || e.type === 'radio';
          return !inline && !isCheck && (r.width < 24 || r.height < 24);
        }).slice(0, 5).map(e => (e.getAttribute('aria-label') || e.textContent || e.tagName).trim().slice(0, 30));
        return { overflow: document.documentElement.scrollWidth > vw + 1, sw: document.documentElement.scrollWidth, vw, small };
      });
      await page.screenshot({ path: path.join(OUT, `${name}-${mode}.png`) });
      checked++;
      const tag = `${mode} ${name}`;
      if (resp.status() >= 400) problems.push(`${tag}: HTTP ${resp.status()}`);
      if (m.overflow) problems.push(`${tag}: horizontal overflow (${m.sw}px > ${m.vw}px)`);
      if (m.small.length) problems.push(`${tag}: tap targets under 24px: ${m.small.join(', ')}`);
      errors.slice(0, 3).forEach(e => problems.push(`${tag}: ${e}`));
    }
  }
  await browser.close();
  console.log(`${checked} page views checked, ${problems.length} problem(s). Screenshots: ${OUT}`);
  problems.forEach(p => console.log(' - ' + p));
  process.exit(problems.length ? 1 : 0);
})().catch(e => { console.error('FAILED', e.message); process.exit(2); });
