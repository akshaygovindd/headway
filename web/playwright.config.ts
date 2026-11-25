import { defineConfig } from "@playwright/test";

// Browser checks run against the production build, served with the mock API (no network).
// Build first: `npm run build:mock` (the ci script does).
const PORT = 3123;
export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${PORT}` },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: {
    command: `node scripts/with-mock-api.mjs next start -p ${PORT} -H 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}/about`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
