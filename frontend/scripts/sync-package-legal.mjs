import { copyFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const packageNames = process.argv[2]
  ? [process.argv[2]]
  : ["core", "react", "ui"];
const legalFiles = ["LICENSE", "NOTICE", "TRADEMARKS.md"];

for (const packageName of packageNames) {
  if (!["core", "react", "ui"].includes(packageName)) {
    throw new Error(`Unknown package: ${packageName}`);
  }
  for (const fileName of legalFiles) {
    const source = fileURLToPath(new URL(`../../${fileName}`, import.meta.url));
    const destination = fileURLToPath(
      new URL(`../packages/${packageName}/${fileName}`, import.meta.url),
    );
    copyFileSync(source, destination);
  }
}
