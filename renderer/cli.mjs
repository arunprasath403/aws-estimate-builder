// Local bridge: reads the renderer event as JSON on stdin, writes the result as JSON on stdout.
import { handler } from "./index.mjs";

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (input += c));
process.stdin.on("end", async () => {
  const out = await handler(JSON.parse(input));
  process.stdout.write(JSON.stringify(out));
});
