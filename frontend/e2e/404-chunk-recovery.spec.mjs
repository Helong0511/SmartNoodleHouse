import { test, expect } from '@playwright/test'

test('unknown URL renders a useful not-found page with safe navigation links', async ({ page }) => {
  await page.goto('/this-route-does-not-exist')
  await expect(page.getByRole('heading', { name: '页面不存在' })).toBeVisible()
  await expect(page.getByRole('link', { name: '返回管理首页' })).toHaveAttribute('href', '/dashboard')
  await expect(page.getByRole('link', { name: '前往顾客点餐' })).toHaveAttribute('href', '/scan')
})

test('shows a manual recovery prompt after the one-time chunk retry has failed', async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem('snh_chunk_load_failed_after_retry', '1')
  })
  await page.goto('/login')
  await expect(page.getByRole('alert')).toContainText('页面资源加载失败')
  await expect(page.getByRole('button', { name: '刷新页面' })).toBeVisible()
})
