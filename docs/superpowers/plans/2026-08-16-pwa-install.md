# PWA Installability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the reader installable as a home-screen app (iOS/Android/desktop "Add to Home Screen"), launching full-screen with its own icon — no offline support, no service worker.

**Architecture:** Static additions only — a web manifest, three PNG icons rendered once from the existing `favicon.svg` mark, a handful of `<link>`/`<meta>` tags in `index.html`, and one small addition to the existing theme-toggle `useEffect` in `App.tsx` to keep the browser-chrome tint in sync with the in-app dark/light toggle.

**Tech Stack:** Vite + React 19 + TypeScript, no new dependencies. No JS test framework exists in this repo (`frontend/package.json` has no test script) — verification is `tsc -b` (part of `npm run build`), inspecting built output, and manual/DevTools checks, matching the project's existing frontend conventions.

## Global Constraints

- Reuse the existing `frontend/public/favicon.svg` mark — no new icon artwork (spec §1, Non-goals).
- No service worker, no offline caching (spec, Non-goals) — this is installability only.
- `manifest.webmanifest` must use relative `start_url`/`scope`/icon `src` values (`"."`, `"icons/icon-192.png"`) — production serves under `VITE_BASE=/reader/`, dev serves under `/`, and relative manifest values resolve correctly against either without build-time templating (spec, "Deployment constraint").
- `background_color`/`theme_color` values are the app's existing `--bg` tokens: `#16140f` dark, `#faf6ee` light (`frontend/src/index.css` lines 8, 47).
- Do not add new npm dependencies (`sharp`, `canvas`, image CLIs, etc.) to `frontend/package.json` — icons are rendered once via ad hoc tooling, not a repo-committed build step (spec §1).

---

### Task 1: Generate PWA icon PNGs from the existing favicon mark

**Files:**
- Create: `frontend/public/icons/icon-192.png`
- Create: `frontend/public/icons/icon-512.png`
- Create: `frontend/public/icons/apple-touch-icon.png`

**Interfaces:**
- Consumes: `frontend/public/favicon.svg` (existing file, contents below — do not modify it)
- Produces: three PNG files at the paths above, referenced by file name only (`icon-192.png`, `icon-512.png`, `apple-touch-icon.png`) by Tasks 2 and 3.

The source SVG (`frontend/public/favicon.svg`), for reference:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="7" fill="#e08a3e"/>
  <circle cx="9" cy="23" r="3.2" fill="#faf6ee"/>
  <path d="M9 15 A8 8 0 0 1 17 23" fill="none" stroke="#faf6ee" stroke-width="3.1" stroke-linecap="round"/>
  <path d="M9 8 A15 15 0 0 1 24 23" fill="none" stroke="#faf6ee" stroke-width="3.1" stroke-linecap="round"/>
</svg>
```

- [ ] **Step 1: Create a scratch HTML file that rasterizes the SVG onto canvases at each target size**

Write this to `/tmp/pwa-icon-render.html` (scratch file, not part of the repo):

```html
<!doctype html>
<canvas id="c192" width="192" height="192"></canvas>
<canvas id="c512" width="512" height="512"></canvas>
<canvas id="c180" width="180" height="180"></canvas>
<script>
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="7" fill="#e08a3e"/>
  <circle cx="9" cy="23" r="3.2" fill="#faf6ee"/>
  <path d="M9 15 A8 8 0 0 1 17 23" fill="none" stroke="#faf6ee" stroke-width="3.1" stroke-linecap="round"/>
  <path d="M9 8 A15 15 0 0 1 24 23" fill="none" stroke="#faf6ee" stroke-width="3.1" stroke-linecap="round"/>
</svg>`
  const img = new Image()
  const blob = new Blob([svg], { type: 'image/svg+xml' })
  const url = URL.createObjectURL(blob)
  window.renderDone = false
  img.onload = () => {
    for (const [id, size] of [['c192', 192], ['c512', 512], ['c180', 180]]) {
      const c = document.getElementById(id)
      const ctx = c.getContext('2d')
      ctx.drawImage(img, 0, 0, size, size)
    }
    window.renderDone = true
  }
  img.src = url
</script>
```

- [ ] **Step 2: Open the file in a browser tab and wait for rendering**

Using the Chrome/Playwright browser tool available in this session: navigate to `file:///tmp/pwa-icon-render.html`, then poll `window.renderDone === true` (evaluate JS) before continuing.

- [ ] **Step 3: Extract each canvas as a PNG data URL and decode to disk**

For each of the three canvases, evaluate in the page:

```javascript
document.getElementById('c192').toDataURL('image/png')
```

(repeat for `c512`, `c180`). Each call returns a string like
`data:image/png;base64,iVBORw0KG...`. Strip the `data:image/png;base64,`
prefix and write the remaining base64 to disk, e.g.:

```bash
mkdir -p frontend/public/icons
echo "<base64 payload from c192>" | base64 -d > frontend/public/icons/icon-192.png
echo "<base64 payload from c512>" | base64 -d > frontend/public/icons/icon-512.png
echo "<base64 payload from c180>" | base64 -d > frontend/public/icons/apple-touch-icon.png
```

- [ ] **Step 4: Verify the three files exist with the correct pixel dimensions**

Run:
```bash
file frontend/public/icons/icon-192.png frontend/public/icons/icon-512.png frontend/public/icons/apple-touch-icon.png
```
Expected output shows `PNG image data, 192 x 192` for `icon-192.png`, `512 x 512` for `icon-512.png`, and `180 x 180` for `apple-touch-icon.png`.

- [ ] **Step 5: Commit**

```bash
git add frontend/public/icons/icon-192.png frontend/public/icons/icon-512.png frontend/public/icons/apple-touch-icon.png
git commit -m "Add PWA icon PNGs rendered from favicon.svg"
```

---

### Task 2: Add the web app manifest

**Files:**
- Create: `frontend/public/manifest.webmanifest`

**Interfaces:**
- Consumes: icon files from Task 1 (`icons/icon-192.png`, `icons/icon-512.png`) by relative path.
- Produces: `manifest.webmanifest` served at the app root (dev `/manifest.webmanifest`, prod `/reader/manifest.webmanifest` via Vite's `base`-relative asset handling), referenced by `<link rel="manifest">` in Task 3.

- [ ] **Step 1: Write the manifest**

```json
{
  "name": "OpenReader",
  "short_name": "OpenReader",
  "start_url": ".",
  "scope": ".",
  "display": "standalone",
  "background_color": "#16140f",
  "theme_color": "#16140f",
  "icons": [
    { "src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

Save this exactly as `frontend/public/manifest.webmanifest`.

- [ ] **Step 2: Verify it's valid JSON**

Run:
```bash
node -e "JSON.parse(require('fs').readFileSync('frontend/public/manifest.webmanifest', 'utf8')); console.log('valid JSON')"
```
Expected: prints `valid JSON` with no error.

- [ ] **Step 3: Commit**

```bash
git add frontend/public/manifest.webmanifest
git commit -m "Add PWA web app manifest"
```

---

### Task 3: Wire the manifest and iOS meta tags into index.html

**Files:**
- Modify: `frontend/index.html`

**Interfaces:**
- Consumes: `manifest.webmanifest` and `icons/apple-touch-icon.png` from Tasks 1–2, by root-absolute path (`/manifest.webmanifest`, `/icons/apple-touch-icon.png`) — Vite rewrites these with the configured `base` at build time, the same mechanism already used by the existing `<link rel="icon" href="/favicon.svg">` tag.
- Produces: a built `dist/index.html` with correctly `base`-prefixed manifest/icon links, verified in this task's Step 3.

- [ ] **Step 1: Edit the `<head>`**

Current relevant block in `frontend/index.html`:

```html
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <meta
      name="viewport"
      content="width=device-width, initial-scale=1.0, maximum-scale=1.0, viewport-fit=cover"
    />
```

Replace it with:

```html
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <link rel="manifest" href="/manifest.webmanifest" />
    <link rel="apple-touch-icon" href="/icons/apple-touch-icon.png" />
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
    <meta name="apple-mobile-web-app-title" content="OpenReader" />
    <meta name="theme-color" content="#16140f" />
    <meta
      name="viewport"
      content="width=device-width, initial-scale=1.0, maximum-scale=1.0, viewport-fit=cover"
    />
```

- [ ] **Step 2: Build with the production base path and confirm it still compiles**

Run:
```bash
cd frontend && VITE_BASE=/reader/ npm run build
```
Expected: build succeeds (runs `tsc -b && vite build` per `frontend/package.json`), no errors.

- [ ] **Step 3: Inspect the built HTML for correctly prefixed paths**

Run:
```bash
grep -E 'manifest|apple-touch-icon|favicon' frontend/dist/index.html
```
Expected: every path shown is prefixed with `/reader/` (e.g.
`/reader/manifest.webmanifest`, `/reader/icons/apple-touch-icon.png`,
`/reader/favicon.svg`) — confirming Vite's `base` rewriting applies to the
new tags the same way it already does for the existing favicon link.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html
git commit -m "Wire PWA manifest and iOS home-screen meta tags into index.html"
```

---

### Task 4: Sync theme-color meta tag with the in-app theme toggle

**Files:**
- Modify: `frontend/src/App.tsx:150-153`

**Interfaces:**
- Consumes: existing `theme` state (`'dark' | 'light'`) and the existing `useEffect` at `App.tsx:150-153`; the `<meta name="theme-color">` tag added in Task 3.
- Produces: nothing consumed by later tasks — this is the last code change.

- [ ] **Step 1: Edit the existing theme effect**

Current code (`frontend/src/App.tsx:150-153`):

```typescript
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('reader-theme', theme)
  }, [theme])
```

Replace with:

```typescript
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('reader-theme', theme)
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute('content', theme === 'light' ? '#faf6ee' : '#16140f')
  }, [theme])
```

- [ ] **Step 2: Typecheck**

Run:
```bash
cd frontend && npx tsc -b
```
Expected: no errors.

- [ ] **Step 3: Verify the toggle updates the meta tag at runtime**

Start the dev server (`cd frontend && npm run dev`), open it in the
browser tool, and use the theme toggle button in the UI. After toggling,
evaluate in the page:

```javascript
document.querySelector('meta[name="theme-color"]').getAttribute('content')
```

Expected: `#faf6ee` when the UI is in light mode, `#16140f` when back in
dark mode — confirming the meta tag tracks the toggle rather than staying
fixed at its seeded value.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "Sync theme-color meta tag with in-app theme toggle"
```

---

### Task 5: End-to-end verification

**Files:** none (verification only)

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: nothing — this is the final gate confirming the feature works as a whole.

- [ ] **Step 1: Full production-shaped build**

Run:
```bash
cd frontend && VITE_BASE=/reader/ npm run build
```
Expected: succeeds with no errors (this re-runs the same build as Task 3
Step 2, now including the Task 4 code change).

- [ ] **Step 2: Confirm manifest and icons are present in the build output**

Run:
```bash
ls frontend/dist/manifest.webmanifest frontend/dist/icons/icon-192.png frontend/dist/icons/icon-512.png frontend/dist/icons/apple-touch-icon.png
```
Expected: all four files listed with no "No such file" errors — `public/`
contents are copied to `dist/` root as-is by Vite.

- [ ] **Step 3: Validate the manifest via Chrome DevTools' Application panel**

Serve the build (`cd frontend && npm run preview -- --base /reader/` or
equivalent local static server matching the built base path), open it in
the browser tool, open DevTools → Application → Manifest. Expected: the
panel shows `name: OpenReader`, `short_name: OpenReader`,
`start_url`/`scope` resolving under the served path, `display: standalone`,
both icons listed with no manifest errors or warnings.

- [ ] **Step 4: Confirm no stray dev artifacts were committed**

Run:
```bash
git status
```
Expected: clean (Tasks 1–4 already committed their changes); `frontend/dist/`
is untracked/build output and should not be part of the diff (confirm it's
covered by `.gitignore` — if not, do not `git add` it).
