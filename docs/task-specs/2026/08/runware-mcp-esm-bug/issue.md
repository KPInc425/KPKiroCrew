---
title: "@runware/mcp fails to start — ESM extensionless import of './version' (ERR_MODULE_NOT_FOUND) on every published version"
labels: ["bug", "mcp"]
assignees: []
---

## Summary

The `@runware/mcp` npm package cannot boot under Node ESM. `dist/config.js`
imports a sibling module **without a `.js` extension**, which Node's ESM
resolver rejects with `ERR_MODULE_NOT_FOUND`. The package declares
`"type": "module"`, so all `.js` files are ESM and the bare specifier is not
allowed.

This affects **every published version** (verified: `1.3.1`, `1.3.3`, `1.3.4`).
Any MCP client that launches it via `npx -y @runware/mcp` (Claude Desktop,
Cursor, Codex stdio bridge, kiro-cli, etc.) gets a server that dies on startup
with no tools.

## Repro

```bash
npx -y @runware/mcp
```

Immediately exits 1:

```
ERR_MODULE_NOT_FOUND
url: 'file:///…/node_modules/@runware/mcp/dist/version'
```

(no `.js` extension on the resolved URL)

## Root cause

`dist/config.js` line 10:

```js
import { MCP_VERSION } from './version';   // ❌ must be './version.js'
```

The file `dist/version.js` **does exist** and exports `MCP_VERSION`; the import
is simply missing its extension. Node ESM requires explicit file extensions for
relative imports, and the package has no `exports`/`imports` map to remap the
bare specifier, so resolution fails.

`dist/index.js` also has other relative imports, but they are correctly
specified (`./config.js`, `./formatters.js`, `./schema-registry.js`); only
`./version` is extensionless.

## Expected behavior

The server starts and exposes its MCP tools when launched via
`npx -y @runware/mcp` (or `node dist/index.js`) with `RUNWARE_API_KEY` set.

## Suggested fix

Change line 10 of `dist/config.js` to:

```js
import { MCP_VERSION } from './version.js';
```

and re-publish, since every version from 1.3.1 through 1.3.4 carries the same
defect.

## Workaround (for affected users)

Until this is published, the server can be made to boot by patching the
installed copy in place:

```
sed -i "s|from './version'|from './version.js'|" node_modules/@runware/mcp/dist/config.js
```

(We run this exact patch at our own install today; happy to switch back to the
unpatched package once a fixed version is published.)

## Environment

- `@runware/mcp` versions tried: `1.3.1`, `1.3.3`, `1.3.4`
- Node `v24.19.0`
- Launched via `npx -y @runware/mcp` and `node dist/index.js`
