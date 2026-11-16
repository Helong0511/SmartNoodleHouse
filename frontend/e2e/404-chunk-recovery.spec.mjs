import { test, expect } from '@playwright/test'

test('unknown URL renders a useful not-found page with safe navigation links', async ({ page }) => {
  await page.goto('/this-route-does-not-exist')
  await expect(page.getByRole('heading', { name: '页面不存在' })).toBeVisible()
  await expect(page.getByRole('link', { name: '返回管理首页' })).toHaveAttribute('href', '/dashboard')
  await expect(page.getByRole('link', { name: '前往顾客点餐' })).toHaveAttribute('href', '/scan')
})

test('retries a failed lazy chunk only once, then shows the manual recovery prompt', async ({ page }) => {
  let documentNavigations = 0
  let failedChunkRequests = 0
  page.on('request', (request) => {
    if (request.isNavigationRequest() && request.frame() === page.mainFrame()) {
      documentNavigations += 1
    }
  })

  // Load the app shell and login chunk successfully first. Only subsequent JS
  // requests are failed, so a document reload can boot the app and retry the
  // target route's lazy chunk.
  await page.goto('/login')
  const alreadyLoadedScripts = await page.evaluate(() => new Set(
    performance.getEntriesByType('resource')
      .map((entry) => entry.name)
      .filter((url) => /\\.js(?:\\?|$)/.test(url)),
  ))

  await page.route('**/*.js', async (route) => {
    if (alreadyLoadedScripts.has(route.request().url())) {
      await route.continue()
      return
    }
    failedChunkRequests += 1
    await route.abort('failed')
  })

  // /display is public and lazy-loaded. Trigger a real Vue Router navigation
  // so Chromium attempts to fetch its split JavaScript chunk.
  await page.evaluate(() => {
    window.history.pushState({}, '', '/display')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })

  await expect(page.getByRole('alert')).toContainText('页面资源加载失败')
  await expect(page.getByRole('button', { name: '刷新页面' })).toBeVisible()
  expect(failedChunkRequests).toBeGreaterThanOrEqual(2)
  // One initial document load plus exactly one automatic retry reload.
  expect(documentNavigations).toBe(2)
})

test('shows the recovery prompt when a failed-retry marker already exists', async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem('snh_chunk_load_failed_after_retry', '1')
  })
  await page.goto('/login')
  await expect(page.getByRole('alert')).toContainText('页面资源加载失败')
  await expect(page.getByRole('button', { name: '刷新页面' })).toBeVisible()
})
