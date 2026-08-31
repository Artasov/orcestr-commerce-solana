import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

for (const packageDirectory of ["core", "react", "ui"]) {
  test(`${packageDirectory} package exports built ESM and legal files`, async () => {
    const root = resolve("packages", packageDirectory);
    const manifest = JSON.parse(await readFile(resolve(root, "package.json"), "utf8"));
    assert.equal(manifest.type, "module");
    await access(resolve(root, manifest.exports["."].import));
    await access(resolve(root, manifest.exports["."].types));
    for (const file of ["LICENSE", "NOTICE", "TRADEMARKS.md", "README.md", "README.ru.md"]) {
      await access(resolve(root, file));
      assert.ok(manifest.files.includes(file), `${file} is missing from npm files`);
    }
  });
}

test("UI package exports its copied stylesheet", async () => {
  await access(resolve("packages", "ui", "dist", "styles.css"));
});

test("UI package exposes i18n without loading checkout and QR modules", async () => {
  const root = resolve("packages", "ui");
  const manifest = JSON.parse(
    await readFile(resolve(root, "package.json"), "utf8"),
  );
  await access(resolve(root, manifest.exports["./i18n"].import));
  await access(resolve(root, manifest.exports["./i18n"].types));
});

test("React package exposes tree-shake-safe integration subpaths", async () => {
  const root = resolve("packages", "react");
  const manifest = JSON.parse(
    await readFile(resolve(root, "package.json"), "utf8"),
  );
  for (const subpath of [
    "./provider",
    "./query-keys",
    "./hooks",
    "./wallet",
    "./kit-inspector",
  ]) {
    await access(resolve(root, manifest.exports[subpath].import));
    await access(resolve(root, manifest.exports[subpath].types));
  }
});

for (const packageDirectory of ["react", "ui"]) {
  test(`${packageDirectory} package marks its browser entry as a client module`, async () => {
    const entry = await readFile(
      resolve("packages", packageDirectory, "dist", "index.js"),
      "utf8",
    );
    assert.match(entry.slice(0, 64), /["']use client["'];/u);
  });
}

test("UI-only consumers do not install the wallet or query runtime", async () => {
  const manifest = JSON.parse(
    await readFile(resolve("packages", "ui", "package.json"), "utf8"),
  );
  assert.equal(manifest.dependencies["@orcestr/commerce-solana-react"], undefined);
  assert.equal(manifest.peerDependencies["@tanstack/react-query"], undefined);
});
