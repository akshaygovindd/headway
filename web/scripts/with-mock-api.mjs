// Run a command with HEADWAY_API_URL pointing at the mock API (random free port, no network).
import { spawn } from "node:child_process";
import { startMockApi } from "./mock-api.mjs";

const [cmd, ...args] = process.argv.slice(2);
const server = await startMockApi(0);
const url = `http://127.0.0.1:${server.address().port}`;
const child = spawn(cmd, args, {
  stdio: "inherit",
  env: { ...process.env, HEADWAY_API_URL: url },
  shell: process.platform === "win32",
});
child.on("exit", (code) => {
  server.close();
  process.exit(code ?? 1);
});
