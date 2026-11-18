import { test, expect } from '@playwright/test'

async function startStaffSession(page, user, token = 'e2e-route-permission-token') {
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
      body: JSON.stringify({ code: 0, data: user }),
    })
  })
  await page.addInitScript(value => {
    sessionStorage.setItem('snh_staff_token', value)
  }, token)
}

test('store admin exception grants store-info without an explicit matching permission', async ({ page }) => {
  await startStaffSession(page, {
    id: 810, role: 'STORE_ADMIN', permissions: [],
  })

  await page.goto('/store-info')
  await expect(page).toHaveURL(url => new URL(url).pathname === '/store-info')
})

test('anyPermission accepts either permission and denies users with neither', async ({ page }) => {
  await startStaffSession(page, {
    id: 811, role: 'STAFF', permissions: ['order:read'],
  })

  await page.goto('/orders')
  await expect(page).toHaveURL(url => new URL(url).pathname === '/orders')

  await page.goto('/payment-reconcile')
  await expect(page).toHaveURL(url => new URL(url).pathname === '/dashboard')
})

test('super-admin-only route rejects a regular staff member even with system:manage', async ({ page }) => {
  await startStaffSession(page, {
    id: 812, role: 'STAFF', permissions: ['system:manage', 'order:read'],
  })

  await page.goto('/outbox-dead-letters')
  await expect(page).toHaveURL(url => new URL(url).pathname === '/dashboard')
})

test('super admin can access the super-admin-only route', async ({ page }) => {
  await startStaffSession(page, {
    id: 813, role: 'SUPER_ADMIN', permissions: [],
  })

  await page.goto('/outbox-dead-letters')
  await expect(page).toHaveURL(url => new URL(url).pathname === '/outbox-dead-letters')
})

test('settlement permission is granted to store admins and payment-refund holders only', async ({ page }) => {
  await startStaffSession(page, {
    id: 814, role: 'STAFF', permissions: ['payment:refund'],
  })

  await page.goto('/pos-settlement')
  await expect(page).toHaveURL(url => new URL(url).pathname === '/pos-settlement')

  await page.goto('/pos-refunds')
  await expect(page).toHaveURL(url => new URL(url).pathname === '/pos-refunds')
})

test('staff without settlement permission is redirected away from settlement routes', async ({ page }) => {
  await startStaffSession(page, {
    id: 815, role: 'STAFF', permissions: ['order:read'],
  })

  await page.goto('/pos-settlement')
  await expect(page).toHaveURL(url => new URL(url).pathname === '/dashboard')
})

test('cashier may open personal stats but not the general POS route', async ({ page }) => {
  await startStaffSession(page, {
    id: 816, role: 'CASHIER', permissions: [],
  })

  await page.goto('/pos/my-stats')
  await expect(page).toHaveURL(url => new URL(url).pathname === '/pos/my-stats')

  await page.goto('/pos')
  await expect(page).toHaveURL(url => new URL(url).pathname === '/pos-terminal')
})
