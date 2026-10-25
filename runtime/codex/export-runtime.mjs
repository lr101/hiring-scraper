// Export the pinned native runtime for the Python release image; Node is build-only.
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { cpSync, mkdirSync, readdirSync } from 'node:fs'

if (process.platform !== 'linux' || !['x64', 'arm64'].includes(process.arch)) throw new Error('The Radius release supports Linux x64/arm64.')
const require = createRequire(import.meta.url)
const codexRequire = createRequire(require.resolve('@openai/codex/package.json'))
const native = dirname(codexRequire.resolve(`@openai/codex-linux-${process.arch}/package.json`))
const vendor = join(native, 'vendor')
const targets = readdirSync(vendor)
if (targets.length !== 1) throw new Error('Unexpected Codex native package layout.')
const source = join(vendor, targets[0])
const destination = resolve(process.argv[2] ?? '/codex-runtime')
mkdirSync(destination, { recursive: true })
for (const item of ['bin/codex', 'codex-resources/bwrap', 'codex-path/rg', 'codex-package.json']) {
  mkdirSync(dirname(join(destination, item)), { recursive: true })
  cpSync(join(source, item), join(destination, item))
}
