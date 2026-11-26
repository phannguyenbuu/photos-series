const { chromium } = require('@playwright/test');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, permissions: ['local-network-access'] });
  const cdp = await browser.newBrowserCDPSession();
  const contexts = await cdp.send('Target.getBrowserContexts');
  await cdp.send('Browser.setPermission', { permission: { name: 'loopback-network' }, setting: 'granted', origin: 'https://dir.toolxprint.com', browserContextId: contexts.browserContextIds[0] });
  const page = await context.newPage();
  const errors = [];
  page.on('console', message => { if (message.type() === 'error') console.log('console', message.text().slice(0, 600)); });
  page.on('requestfailed', request => console.log('requestfailed', request.url().slice(0, 160), request.failure()?.errorText));
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('photo-series-job', 'downloads-remote-smoke'));
  await page.goto('https://dir.toolxprint.com/?view=photo-series&photo_job=downloads-remote-smoke', { waitUntil: 'domcontentloaded' });
  const section = page.getByRole('heading', { name: 'Chọn ảnh theo series & phân tích ngữ cảnh' });
  await section.waitFor({ state: 'visible', timeout: 20000 });
  try {
    await page.getByText('3/3 series đã phân tích', { exact: false }).waitFor({ state: 'visible', timeout: 20000 });
  } catch (error) {
    console.log((await section.locator('..').innerText()).slice(0, 1800));
    await page.screenshot({ path: path.resolve('output/photo-series-downloads-test/controller-ui.png'), fullPage: true });
    await browser.close();
    throw error;
  }
  const images = page.locator('img[src*="/api/photo-series/"]');
  const count = await images.count();
  if (count !== 9) throw new Error(`Expected 9 selected thumbnails, found ${count}`);
  await page.waitForFunction(() => [...document.querySelectorAll('img[src*="/api/photo-series/"]')].every(image => image.complete && image.naturalWidth > 0));
  await page.screenshot({ path: path.resolve('output/photo-series-downloads-test/controller-ui.png'), fullPage: true });
  let submitted = false;
  if (process.argv.includes('--submit')) {
    const input = path.resolve('output/photo-series-ui-input');
    fs.mkdirSync(input, { recursive: true });
    fs.copyFileSync(path.join(os.homedir(), 'Downloads/1/1/DSC04205.JPG'), path.join(input, 'DSC04205.JPG'));
    await page.getByLabel('Thư mục trên máy chạy PrintAgent').fill(input);
    await page.getByLabel('N ảnh / series', { exact: true }).fill('1');
    await page.getByRole('button', { name: 'Nhóm & phân tích', exact: true }).click();
    await page.getByText('1/1 series đã phân tích', { exact: false }).waitFor({ timeout: 120000 });
    const jobId = await page.evaluate(() => localStorage.getItem('photo-series-job'));
    const result = await page.evaluate(async id => (await fetch(`http://127.0.0.1:9173/api/photo-series/jobs/${id}`)).json(), jobId);
    if (result.status !== 'completed' || !result.series[0]?.result) throw new Error('Submitted job did not return valid structured context');
    fs.writeFileSync(path.resolve('output/photo-series-downloads-test/ui-submitted-job.json'), JSON.stringify(result, null, 2));
    submitted = true;
  }
  if (errors.length) throw new Error(errors.join('\n'));
  const genai = await context.newPage();
  await genai.goto('https://genai.toolxprint.com/retouch', { waitUntil: 'networkidle' });
  if (await genai.getByRole('heading', { name: 'Chọn ảnh theo series & phân tích ngữ cảnh' }).count()) throw new Error('Controller is still mounted in GenAI');
  console.log(JSON.stringify({ ui: 'passed', selectedThumbnails: count, javascriptErrors: errors.length, submitted }));
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
