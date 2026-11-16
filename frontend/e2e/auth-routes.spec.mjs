import { test, expect } from '@playwright/test'

test('login page renders without prefilled credentials', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: '肠旺面经营管理系统' })).toBeVisible()
  await expect(page.getByPlaceholder('请输入用户名')).toHaveValue('')
  await expect(page.getByPlaceholder('请输入密码')).toHaveValue('')
})

test('unauthenticated staff route redirects to login', async ({ page }) => {
  await page.goto('/dashboard')
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByRole('heading', { name: '肠旺面经营管理系统' })).toBeVisible()
})

for (const path of ['/scan', '/order']) {
  test(`customer public route ${path} does not redirect to staff login`, async ({ page }) => {
    await page.goto(path)
    await expect(page).toHaveURL(new RegExp(`${path.replaceAll('/', '\\/')}$`))
    await expect(page.locator('#app')).toBeVisible()
  })
}

test('cashier session is kept in sessionStorage and is confined to POS', async ({ page }) => {
  await page.route('**/api/auth/me', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: 731, role: 'CASHIER', permissions: [] }),
    })
  })
  await page.addInitScript(() => {
    sessionStorage.setItem('snh_staff_token', 'e2e-placeholder-token')
  })
  await page.goto('/dashboard')
  await expect(page).toHaveURL(/\/pos-terminal$/)
  const storage = await page.evaluate(() => ({
    sessionToken: sessionStorage.getItem('snh_staff_token'),
    localToken: localStorage.getItem('snh_staff_token'),
    legacyLocalToken: localStorage.getItem('token'),
  }))
  expect(storage).toEqual({
    sessionToken: 'e2e-placeholder-token',
    localToken: null,
    legacyLocalToken: null,
  })
})
