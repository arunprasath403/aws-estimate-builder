// Usage: $env:CHROME_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"; node local_test.mjs estimate.json [--save]
import { readFileSync } from "node:fs";
import { handler } from "./index.mjs";

const estimate = JSON.parse(readFileSync(process.argv[2], "utf8"));
const t = Date.now();
const out = await handler({ estimate, save: process.argv.includes("--save") });
console.log(JSON.stringify(out, null, 2));
console.log(`took ${((Date.now() - t) / 1000).toFixed(1)}s`);
