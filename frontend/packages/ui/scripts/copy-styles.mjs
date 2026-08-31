import { copyFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const source = fileURLToPath(new URL("../src/styles.css", import.meta.url));
const destination = fileURLToPath(new URL("../dist/styles.css", import.meta.url));

mkdirSync(fileURLToPath(new URL("../dist/", import.meta.url)), { recursive: true });
copyFileSync(source, destination);
