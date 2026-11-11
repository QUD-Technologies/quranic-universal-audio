// Restore vendored archives the Hub stored as Git-LFS pointers (the Space build context gets the
// ~130-byte pointer, not the bytes, for binaries over about 100 KB). Each pointer is replaced by the
// file at the deployed commit of the public GitHub repo (vendor/DEPLOY_COMMIT, written by
// scripts/deploy/upload_inspector.py) and checked against the pointer's own sha256 and size.
// A no-op wherever the archives are real files (local checkouts, CI).
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = "QUD-Technologies/quranic-universal-audio";
const VENDOR_IN_REPO = "inspector/frontend/vendor";
const POINTER = "version https://git-lfs.github.com/spec/";

const vendor = dirname(fileURLToPath(import.meta.url));
const pointers = readdirSync(vendor).filter((f) => {
  const head = readFileSync(join(vendor, f)).subarray(0, POINTER.length).toString("latin1");
  return head === POINTER;
});
if (pointers.length) {
  const commit = readFileSync(join(vendor, "DEPLOY_COMMIT"), "utf8").trim();
  for (const file of pointers) {
    const pointer = readFileSync(join(vendor, file), "utf8");
    const oid = /oid sha256:([0-9a-f]{64})/.exec(pointer)?.[1];
    const size = Number(/size (\d+)/.exec(pointer)?.[1]);
    if (!oid || !size) throw new Error(`${file}: not a readable LFS pointer`);
    const url = `https://raw.githubusercontent.com/${REPO}/${commit}/${VENDOR_IN_REPO}/${file}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${file}: ${url} answered ${res.status}`);
    const bytes = Buffer.from(await res.arrayBuffer());
    const got = createHash("sha256").update(bytes).digest("hex");
    if (bytes.length !== size || got !== oid) throw new Error(`${file}: got ${bytes.length} bytes sha256 ${got}, the pointer says ${size} ${oid}`);
    writeFileSync(join(vendor, file), bytes);
    console.log(`restored ${file} (${size} bytes) from ${commit.slice(0, 9)}`);
  }
}
