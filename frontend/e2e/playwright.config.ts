import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const port = Number(process.env.ZHIYUAN_E2E_PORT || '8787');
if (!Number.isInteger(port) || port < 1024 || port > 65535) {
  throw new Error('ZHIYUAN_E2E_PORT must be an unprivileged TCP port.');
}
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: '.',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  outputDir: path.join(root, 'output/playwright/results'),
  reporter: [
    ['list'],
    ['html', { outputFolder: path.join(root, 'output/playwright/report'), open: 'never' }],
  ],
  use: {
    ...devices['Desktop Chrome'],
    baseURL,
    headless: true,
    acceptDownloads: true,
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `.venv/bin/python scripts/browser_regression_server.py --port ${port}`,
    cwd: root,
    url: `${baseURL}/__e2e__/isolation`,
    // Never attach browser tests to the user's existing local service.
    reuseExistingServer: false,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 10_000 },
    timeout: 60_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
