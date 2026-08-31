import { rmSync } from "node:fs";
import { resolve } from "node:path";

for (const packageName of ["core", "react", "ui"]) {
  rmSync(resolve("packages", packageName, "dist"), {
    recursive: true,
    force: true,
  });
  rmSync(resolve("packages", packageName, "tsconfig.tsbuildinfo"), {
    force: true,
  });
}
