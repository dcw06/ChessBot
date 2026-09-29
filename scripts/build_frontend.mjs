import { build } from "esbuild";
import { rm } from "node:fs/promises";

await rm(new URL("../static/dist/chunks/", import.meta.url), {
  recursive: true,
  force: true,
});
await build({
  entryPoints: ["static/js/app.js"],
  bundle: true,
  minify: true,
  splitting: true,
  format: "esm",
  outdir: "static/dist",
  entryNames: "app.min",
  chunkNames: "chunks/[name]-[hash]",
});
await build({
  entryPoints: ["static/css/app.css"],
  minify: true,
  outfile: "static/dist/app.min.css",
});
