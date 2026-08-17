# PWA installability — design

## Goal

Make the reader installable as a home-screen app on iOS/Android/desktop
("Add to Home Screen" / browser install), so it launches full-screen without
browser chrome and has its own app icon. This is installability only — no
offline reading, no service worker, no caching. The app still requires
network for everything, same as opening it in a tab today.

## Non-goals

- Offline article reading / caching. Explicitly deferred; would need its own
  design (cache strategy, invalidation/versioning, offline-state UI).
- Chrome's desktop/Android omnibox "install" button. That requires a
  registered service worker (even a no-op one) to satisfy Chrome's
  installability heuristic. Out of scope since the actual usage is iOS
  Safari/Chrome, where "Add to Home Screen" is a manual user action that only
  reads the manifest — no service worker involved. Desktop/Android Chrome
  continues to work as a normal tab, just without the install-button
  affordance.
- New icon artwork. Reuses the existing `frontend/public/favicon.svg` mark
  (amber rounded square, brand color already in use).

## Deployment constraint

Production serves the app under a path prefix: `VITE_BASE=/reader/` on
`pengyaochen.com/reader/`, proxied via Apache (`frontend/vite.config.ts`,
`docs/ERD.md` §7.1). Local dev serves at `/`. Anything path-dependent must
work at both without build-time templating.

Vite already rewrites root-absolute paths referenced by tags in
`index.html` (e.g. the existing `<link rel="icon" href="/favicon.svg">`) to
be prefixed with `base` at build time — this is how the current favicon
already works correctly at `/reader/`. New `<link>`/`<meta>` tags added to
`index.html` get the same treatment automatically.

The manifest itself needs no build-time awareness of the prefix: per the Web
App Manifest spec, `start_url`, `scope`, and icon `src` values are resolved
relative to the manifest's own URL. Using relative values (`"."`,
`"icons/icon-192.png"`) makes them correct under `/` or `/reader/`
unchanged.

## Components

### 1. Icons

Render `favicon.svg` to static PNGs, committed once to
`frontend/public/icons/`:

- `icon-192.png` (192×192) — manifest icon
- `icon-512.png` (512×512) — manifest icon
- `apple-touch-icon.png` (180×180) — iOS ignores manifest/SVG icons for the
  home-screen glyph and requires this specific `<link>` tag/size

Icons use `purpose: "any"` implicitly (manifest omits `purpose`, which
defaults to `"any"`) — no maskable/adaptive-icon safe-zone variant. The
source mark already has some internal padding around the glyph, so a plain
square/rounded-square treatment is acceptable; maskable icons are a
nice-to-have deferred as out of scope for this pass.

Generation: a one-off script that renders the SVG onto an HTML `<canvas>` at
each target size via headless browser (Playwright) and exports
`canvas.toDataURL()` to PNG bytes on disk. This is a one-time build step run
by hand now, not a new ongoing build/runtime dependency — the committed PNGs
don't need to regenerate unless the mark itself changes.

### 2. `frontend/public/manifest.webmanifest`

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

`background_color`/`theme_color` use the existing `--bg` dark-theme value
(`#16140f` from `frontend/src/index.css`), matching the app's dark-first
design intent (`color-scheme: dark` is the default).

### 3. `frontend/index.html` additions

Inside `<head>`, alongside the existing favicon/font links:

- `<link rel="manifest" href="/manifest.webmanifest">`
- `<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">`
- `<meta name="apple-mobile-web-app-capable" content="yes">`
- `<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">`
  (pairs with the existing `viewport-fit=cover`, so the status bar overlays
  content using the app's own dark background instead of a default bar)
- `<meta name="apple-mobile-web-app-title" content="OpenReader">`
- `<meta name="theme-color" content="#16140f">` (seeded to the dark value;
  see below for live sync)

### 4. Live theme-color sync (`frontend/src/App.tsx`)

The existing `useEffect` that applies `theme` to
`document.documentElement.dataset.theme` and `localStorage` (around line
150) also sets the `theme-color` meta tag's `content` to the active theme's
`--bg` value (`#16140f` dark / `#faf6ee` light), so the OS chrome/status-bar
tint follows the in-app dark/light toggle instead of staying fixed at the
install-time dark value.

## Verification

- Production-shaped build (`VITE_BASE=/reader/ npm run build`), inspect the
  built `index.html`/`manifest.webmanifest` output to confirm paths resolve
  correctly under the `/reader/` prefix.
- Chrome DevTools Application panel → Manifest, to confirm the manifest
  parses and icons load without errors (structural check; can't literally
  perform "Add to Home Screen" from this environment).
- Existing `npm run build` typecheck (`tsc -b`) must still pass after the
  `App.tsx` change.
