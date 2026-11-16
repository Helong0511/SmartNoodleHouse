import { test, expect } from '@playwright/test'

test('login page renders without prefilled credentials', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: '肠旺面经营管理系统' })).toBeVisible()
  await expect(page.getByPlaceholder('请输入用户名')).toHaveValue('')
  await expect(page.getByPlaceholder('请输入密码')).toHaveValue('')
})

test('unauthenticated staff route redirects to login', async ({ page }) => {
  await page.goto('/dashboard')
  await expect(page).toHaveURL(/\\/login$/)
  await expect(page.getByRole('heading', { name: '肠旺面经营管理系统' })).toBeVisible()
})

for (const path of ['/scan', '/order']) {
  test(`customer public route ${path} does not redirect to staff login`, async ({ page }) => {
    await page.goto(path)
    await expect(page).toHaveURL(new RegExp(`${path.replaceAll('/', '\\\\/')}$`))
    await expect(page.locator('#app')).toBeVisible()
  })
}

test('cashier session is kept in sessionStorage and is confined to POS', async ({ page }) => {
  await page.route('**/api/**', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 0, data: [] }),
    })
  })
  await page.route('**/api/auth/me', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 0, data: { id: 731, role: 'CASHIER', permissions: [] } }),
    })
  })
  await page.addInitScript(() => {
    sessionStorage.setItem('snh_staff_token', 'e2e-placeholder-token')
  })
  await page.goto('/dashboard')
  await expect(page).toHaveURL(/\\/pos-terminal$/)
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
  await page.goto('/kds')
  await expect(page).toHaveURL(/\\/pos-terminal$/)
})

test('failed staff login shows an error and returns to the idle state', async ({ page }) => {
  await page.route('**/api/auth/login', async route => {
    await route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ detail: '用户名或密码错误' }),
    })
  })
  await page.goto('/login')
  await page.getByPlaceholder('请输入用户名').fill('e2e-invalid-user')
  await page.getByPlaceholder('请输入密码').fill('e2e-invalid-password')
  await page.getByRole('button', { name: '登 录' }).click()
  await expect(page.getByRole('button', { name: '登 录 失 败' })).toBeVisible()
  await expect(page.getByRole('button', { name: '登 录' })).toBeEnabled({ timeout: 3000 })
})

test('expired staff session clears the token and redirects to login', async ({ page }) => {
  await page.route('**/api/auth/me', async route => {
    await route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ detail: '登录已过期' }),
    })
  })
  await page.addInitScript(() => {
    sessionStorage.setItem('snh_staff_token', 'e2e-expired-placeholder-token')
  })
  await page.goto('/dashboard')
  await expect(page).toHaveURL(/\\/login$/)
  const token = await page.evaluate(() => sessionStorage.getItem('snh_staff_token'))
  expect(token).toBeNull()
})

test('kitchen role can access KDS but is redirected away from POS', async ({ page }) => {
  await page.route('**/api/**', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 0, data: [] }),
    })
  })
  await page.route('**/api/auth/me', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 0,
        data: { id: 732, role: 'KITCHEN', permissions: ['order:read'] },
      }),
    })
  })
  await page.addInitScript(() => {
    sessionStorage.setItem('snh_staff_token', 'e2e-kitchen-placeholder-token')
  })
  await page.goto('/kds')
  await expect(page).toHaveURL(/\\/kds$/)
  await page.goto('/pos-terminal')
  await expect(page).toHaveURL(/\\/dashboard$/)
})
