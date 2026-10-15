// Build the web app as static files for the desktop app (frontend/out).
//
// The same as `next build`, with AURA_DESKTOP_BUILD=1 — see next.config.mjs.
// A script rather than an inline env var so it works the same in cmd,
// PowerShell and sh.
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const next = require.resolve("next/dist/bin/next");
const result = spawnSync(process.execPath, [next, "build"], {
  stdio: "inherit",
  env: {
    ...process.env,
    AURA_DESKTOP_BUILD: "1",
    NEXT_TELEMETRY_DISABLED: "1",
    // The desktop app's backend serves these pages from its own address, on
    // whatever port was free. A URL from .env.local (localhost:8000 for `npm
    // run dev`) baked in here would point every installed copy at the wrong
    // place. Set but empty, it also wins over the .env files.
    NEXT_PUBLIC_API_URL: "",
  },
});
process.exit(result.status ?? 1);
