"use strict";

// Test-only: validate the actual GLB/glTF files with Khronos' implementation.
// External references are limited to the export directory.
const fs = require("node:fs");
const path = require("node:path");
const validator = require(process.env.HOOKY_GLTF_VALIDATOR || "./gltf-validator/node_modules/gltf-validator");

async function validate(file) {
  const directory = path.dirname(path.resolve(file));
  const content = new Uint8Array(await fs.promises.readFile(file));
  const report = await validator.validateBytes(content, {
    uri: path.basename(file), maxIssues: 100,
    externalResourceFunction: async (uri) => {
      const resolved = path.resolve(directory, decodeURIComponent(uri));
      if (!resolved.startsWith(directory + path.sep)) throw new Error("Reference outside export directory");
      return new Uint8Array(await fs.promises.readFile(resolved));
    },
  });
  return { file, ...report.issues };
}

(async () => {
  if (process.argv.length < 3) throw new Error("Expected at least one file");
  const reports = await Promise.all(process.argv.slice(2).map(validate));
  process.stdout.write(JSON.stringify(reports) + "\n");
  if (reports.some((report) => report.numErrors)) process.exitCode = 1;
})().catch((error) => { console.error(error); process.exitCode = 1; });
