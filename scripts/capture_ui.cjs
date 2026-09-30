// Capture real deployed pages. Requires an already-installed Playwright module.
// NEONFORGE_PLAYWRIGHT_MODULE and NEONFORGE_BROWSER may select local installations.
const { chromium } = require(process.env.NEONFORGE_PLAYWRIGHT_MODULE || 'playwright')
const path = require('node:path')

async function main() {
  const base = process.argv[2] || 'http://127.0.0.1:3000'
  const browser = await chromium.launch({ headless: true, executablePath: process.env.NEONFORGE_BROWSER, args: ['--no-sandbox'] })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  const pages = { voiceover: 'voiceover-studio', video: 'video-generation', character: 'character', lipsync: 'lip-sync', status: 'utilities-status' }
  for (const [route, filename] of Object.entries(pages)) {
    await page.setViewportSize({ width: 1440, height: route === 'video' && process.env.NEONFORGE_VIDEO_JOB ? 1500 : 1000 })
    await page.goto(`${base}/${route}`, { waitUntil: 'networkidle' })
    if (route === 'video' && process.env.NEONFORGE_VIDEO_JOB) {
      await page.evaluate((jobId) => localStorage.setItem('neonforge-media-jobs:/video', JSON.stringify([{ job_id: jobId, service: 'Video' }])), process.env.NEONFORGE_VIDEO_JOB)
      await page.reload({ waitUntil: 'networkidle' })
    }
    await page.waitForTimeout(2000)
    if (route === 'voiceover') {
      // Select an actual reference-free mode so no personal reference audio or transcript is visible.
      await page.getByText('Breeze TTS 2', { exact: true }).first().click()
      await page.waitForTimeout(500)
    }
    const labels = await page.locator('nav a').allTextContents()
    const expected = ['Voiceover', 'Video', 'Character', 'Avatar', 'Lip Sync', 'System Info']
    if (JSON.stringify(labels) !== JSON.stringify(expected)) throw new Error(`Unexpected navigation on ${route}`)
    // Mask operator identifiers and personal profile selectors, without replacing app content.
    const mask = route === 'voiceover' ? [page.locator('#voice-profile-select')] : []
    if (route === 'status') mask.push(page.locator('p').filter({ hasText: /[a-f0-9]{8}-[a-f0-9]{4}-/ }))
    await page.screenshot({ path: path.join(__dirname, '..', 'docs', 'images', `${filename}.png`), fullPage: false, mask, maskColor: '#11151e' })
    console.log(`Captured ${route}`)
  }
  for (const route of ['studio', 'voice', 'broll']) {
    const response = await page.goto(`${base}/${route}`)
    if (response.status() !== 404) throw new Error(`Legacy route still available: ${route}`)
  }
  await page.goto(`${base}/avatar`)
  if (!await page.getByText('Coming soon', { exact: true }).isVisible()) throw new Error('Avatar unavailable state missing')
  await page.goto(`${base}/character`)
  await page.getByRole('tab', { name: 'Animate', exact: true }).click()
  if (!await page.getByText('Animate is coming soon', { exact: true }).isVisible()) throw new Error('Animate unavailable state missing')
  if (await page.getByRole('button', { name: 'Generate character', exact: true }).count()) throw new Error('Animate exposes Replace generation')
  await page.setViewportSize({ width: 390, height: 844 })
  for (const route of ['video', 'character', 'lipsync']) {
    await page.goto(`${base}/${route}`)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
    if (overflow) throw new Error(`Horizontal page overflow on ${route}`)
  }
  await browser.close()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('Navigation, removed routes, unavailable state and mobile overflow checks passed.')
}
main().catch((error) => { console.error(error); process.exitCode = 1 })
