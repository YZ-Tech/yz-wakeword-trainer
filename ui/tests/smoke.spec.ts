import { test, expect } from '@playwright/test'

/** Standalone-mode smoke test for the wakeword-trainer satellite UI.
 *
 *  Loads the satellite's SPA (served from `yz_wakeword_trainer/static/`
 *  via FastAPI StaticFiles), waits for the standalone bundle to render,
 *  and asserts the basic shell + an api round-trip.
 *
 *  Run from satellites/yz-wakeword-trainer/ui/:  npx playwright test */
test('standalone SPA loads + header strip renders', async ({ page }) => {
  await page.goto('/')

  // Header strip rendered (from App.tsx StandaloneHeader). Selector by
  // src instead of role because the logo is decorative (alt="") so its
  // ARIA role flips to "presentation" — getByRole('img') wouldn't find
  // it. Asserting by src also proves the public/logo.svg is being
  // served by FastAPI StaticFiles.
  await expect(page.locator('header img[src="/logo.svg"]')).toBeVisible()
  await expect(page.getByText('Wake-word trainer')).toBeVisible()
  await expect(page.getByText(/satellite · standalone/i)).toBeVisible()
})

test('GET /health responds 200 ok', async ({ request }) => {
  const res = await request.get('/health')
  expect(res.ok()).toBeTruthy()
  const body = await res.json()
  expect(body.ok).toBe(true)
  expect(body.version).toMatch(/^\d+\.\d+\.\d+$/)
})
