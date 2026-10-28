import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { chromium } from 'playwright'

const ui = fileURLToPath(new URL('..', import.meta.url))
const out = path.resolve(ui, '../outputs/desktop-qa')
await mkdir(out, { recursive: true })
const port = 5178
const server = spawn(process.execPath, ['node_modules/vite/bin/vite.js', 'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort', '--outDir', 'evidence/desktop-qa'], { cwd: ui, stdio: 'pipe' })
let serverOutput = ''
server.stdout.on('data', data => { serverOutput += data })
server.stderr.on('data', data => { serverOutput += data })
let browser
const results = []
const errors = []
try {
  const url = `http://127.0.0.1:${port}/tests/desktop-qa.html`
  let ready = false
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw Error(serverOutput)
    try { ready = (await fetch(url)).ok } catch {}
    if (ready) break
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  assert.ok(ready, 'QA server started')
  browser = await chromium.launch({ headless: true, ...(process.env.QA_BROWSER_CHANNEL ? { channel: process.env.QA_BROWSER_CHANNEL } : process.platform === 'win32' ? { channel: 'msedge' } : {}) })
  for (const size of [{ width: 1280, height: 840 }, { width: 1024, height: 640 }, { width: 760, height: 500 }, { width: 853, height: 560 }]) {
    const page = await browser.newPage({ viewport: size })
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(url)
    assert.equal(await page.locator('.login-tabs').count(), 0, 'native welcome hides preview authentication')
    const welcome = await page.getByRole('button', { name: '开始使用', exact: true }).boundingBox()
    assert.ok(welcome.x >= 0 && welcome.x + welcome.width <= size.width, 'welcome action fits viewport')
    await page.getByRole('button', { name: '开始使用', exact: true }).click()
    await page.getByRole('heading', { name: '工作台', exact: true }).waitFor()
    await page.locator('.inline-toast').evaluateAll(nodes => nodes.forEach(node => node.remove()))
    for (const theme of ['light', 'dark']) {
      if (theme === 'dark') await page.getByRole('button', { name: '浅色', exact: true }).click()
      for (const label of ['工作台', '复制新内容', '转移老内容', '影像资料库', '归档记录', '导入预设', '恢复与健康审计', '交付包', '偏好设置', 'Agent 助手']) {
        await page.getByRole('button', { name: label, exact: true }).click()
        await page.locator('.breadcrumbs b').filter({ hasText: label }).waitFor()
        await page.evaluate(() => document.fonts.ready)
        assert.equal(await page.locator('.page-scroll').evaluate(node => node.scrollTop), 0, 'new pages start at the top')
        const layout = await page.evaluate(() => {
          const scroll = document.querySelector('.page-scroll')
          return { rootWidth: document.documentElement.scrollWidth, viewport: innerWidth, contentWidth: scroll.clientWidth, contentScrollWidth: scroll.scrollWidth }
        })
        assert.ok(layout.rootWidth <= layout.viewport + 1, `${label} ${theme} ${size.width}: document overflow ${JSON.stringify(layout)}`)
        assert.ok(layout.contentScrollWidth <= layout.contentWidth + 1, `${label} ${theme} ${size.width}: page overflow ${JSON.stringify(layout)}`)
        const text = await page.locator('body').innerText()
        assert.doesNotMatch(text, /V2 草稿|本地联调|MockDesktopApi|Rust \/ SQLite|开发联调诊断 JSON|保存高级设置|只保存偏好|尚未接入/)
        assert.equal(await page.getByRole('button', { name: '通知', exact: true }).count(), 0)
        if (label === '偏好设置') {
          assert.equal(await page.getByText('登录桌面后启动', { exact: true }).count(), 0)
          assert.equal(await page.getByText('自动生成质量建议', { exact: true }).count(), 0)
          assert.equal(await page.locator('.settings-nav a[href="#advanced"]').count(), 0)
        }
        if (label === '归档记录') {
          const previousName = await page.locator('.task-name b').innerText()
          await page.getByRole('button', { name: '改名', exact: true }).click()
          await page.getByRole('dialog').getByLabel('记录名称').fill('QA 改名后的归档记录')
          await page.getByRole('button', { name: '下一步', exact: true }).click()
          await page.getByRole('dialog').getByRole('button', { name: '取消', exact: true }).click()
          assert.equal(await page.locator('.task-name b').innerText(), previousName, 'cancelled rename preserves the record')
          await page.getByRole('button', { name: '改名', exact: true }).click()
          await page.getByRole('dialog').getByLabel('记录名称').fill('QA 改名后的归档记录')
          await page.getByRole('button', { name: '下一步', exact: true }).click()
          await page.getByRole('dialog').getByRole('button', { name: '确认执行', exact: true }).click()
          await page.waitForFunction(() => document.querySelector('.task-name b')?.textContent === 'QA 改名后的归档记录')
          await page.getByRole('button', { name: '详情 / 报告', exact: true }).click()
          await page.getByRole('heading', { name: '任务详情与报告' }).waitFor()
          assert.match(await page.locator('.result-banner').innerText(), /部分/)
          await page.getByRole('button', { name: '查看报告', exact: true }).click()
          const dialog = page.getByRole('dialog')
          await dialog.waitFor()
          assert.equal(await dialog.locator('details').count(), 0, 'production report hides raw diagnostics')
          await dialog.getByRole('button', { name: '关闭', exact: true }).last().click()
        }
        if (['Agent 助手', '偏好设置', '工作台', '影像资料库'].includes(label)) {
          await page.screenshot({ path: path.join(out, `${size.width}-${size.height}-${theme}-${label}.png`) })
        }
        results.push({ ...size, theme, page: label, passed: true })
      }
    }
    await page.close()
  }
  assert.deepEqual(errors, [], 'no unhandled UI errors')
  await writeFile(path.join(out, 'results.json'), JSON.stringify({ runtime: 'Edge Chromium, synthetic native adapter, production Vue build', checks: results.length, errors, results }, null, 2))
  console.log(`${results.length} desktop page/viewport/theme checks passed; production diagnostics hidden; no unhandled UI errors.`)
} finally {
  await browser?.close()
  server.kill()
}
