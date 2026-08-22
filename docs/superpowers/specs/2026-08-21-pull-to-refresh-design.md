# Pull-to-refresh on the feed list — design

## Goal

Let touch users refresh the article list by pulling down from the top of the
list, instead of only via the header Refresh button / `r` key. Same result
as those existing triggers — same mutation, same toast, same cache
invalidation — just a second way to invoke it.

## Non-goals

- No new dependency. No gesture/animation library — this app has none today
  (safe-area handling, scroll-reset, and the horizontal-pan/scrollbar fix are
  all hand-rolled custom code; this follows the same pattern).
- No reliance on native browser pull-to-refresh. Not reliably available in
  iOS standalone-PWA mode (the primary target here), so it isn't usable even
  as a fallback.
- Not gated to the mobile CSS breakpoint (≤760px). Active on any
  touch-capable device regardless of viewport width — confirmed with user.
  Desktop/mouse users never fire `touchstart`, so nothing changes for them.

## Where it lives

All logic lives in `frontend/src/components/ArticleList.tsx` — the single
list component already shared and re-mounted across every view (saved
views, unread, source, folder — see its existing `listKey` prop). Fixing it
there covers every view uniformly; no per-view wiring.

`App.tsx` passes two new props through to `ArticleList`:
- `onRefresh: () => void` — wired to the existing `refreshMutation.mutate()`
  (`App.tsx:222`), the same mutation the header Refresh button
  (`Sidebar`'s `onRefresh`) and the `r` keyboard shortcut already call.
- `refreshing: boolean` — wired to `refreshMutation.isPending`, same value
  already passed to `Sidebar` as `refreshing`.

Reusing the same mutation means pull-to-refresh produces identical results
to the existing triggers: same `RefreshToast` summary, same
`qc.invalidateQueries(['sources'])` / `qc.invalidateQueries(['articles'])`.

## Gesture mechanics

Raw DOM listeners (`touchstart`, `touchmove`, `touchend`) attached to the
scroll container (`scrollRef`, the existing `.article-list` div) via
`useEffect`, not React's synthetic touch handlers — `touchmove` needs
`{ passive: false }` so `preventDefault()` can suppress the native
rubber-band bounce while a pull is in progress, so the custom indicator
doesn't visually fight the browser's own overscroll.

State machine, tracked in a ref (not React state, to avoid re-render churn
on every `touchmove`) with a separate `useState` only for the rendered pull
distance / phase:

1. **`touchstart`**: record the start only if `scrollRef.current.scrollTop
   === 0` and no refresh is currently pending. Otherwise this is a normal
   scroll or nested drag — do nothing, let the browser handle it natively.
2. **`touchmove`**: if a pull is active, compute `rawDelta = touch.clientY -
   startY`. Ignore (and let native scroll proceed) if `rawDelta <= 0` — this
   is only for pulling down past the top, not for measuring upward scrolls.
   Apply resistance so the visual distance grows slower than the finger
   moves: `pullDistance = min(rawDelta * 0.5, 96)` (matches common
   pull-to-refresh feel — halves finger movement, caps so it can't be
   dragged indefinitely). Call `preventDefault()` while active to suppress
   native bounce. Update the `useState` pull distance, which drives the
   indicator's height/rotation/text.
3. **`touchend`**: if `pullDistance >= 64` (the release threshold — comfortably
   above where the "release to refresh" text swaps in, so the visual and the
   behavior agree), call `onRefresh()` and hold the indicator in a spinning
   "Refreshing…" state. Otherwise animate back to 0 with no action. Once
   `refreshing` prop transitions from `true` to `false` (refresh finished),
   animate the indicator back to 0.

No-ops:
- A pull that starts mid-list (not `scrollTop === 0`) never begins — normal
  scroll behavior is untouched.
- A pull while `refreshing` is already `true` (e.g. triggered moments
  earlier via the header button) doesn't start a second one — `touchstart`
  checks the current `refreshing` prop.

## Visual indicator

A small row that grows in above the first article as the pull distance
increases, capped at ~96px tall so it never eclipses more than a fraction of
the visible list. Contents, keyed off pull phase:
- **Pulling, under threshold**: a chevron/arrow icon rotated proportionally
  to pull progress (0° → 180° as distance approaches 64px), text "Pull to
  refresh".
- **Pulling, past threshold**: icon fully rotated (180°), text "Release to
  refresh".
- **Refreshing**: icon replaced by a spinning indicator (CSS animation, no
  new asset — this app already has spinner treatment for the header refresh
  button's pending state to match against), text "Refreshing…".

Styled with the existing design tokens (`--amber` for the icon/active text,
`--ink-faint` for the idle "Pull to refresh" label, `--font-ui`) so it reads
as part of the app rather than a bolted-on library widget.

## Empty-state view

The `Nothing here yet` empty state (`ArticleList.tsx`'s early return when
`articles.length === 0`) currently isn't wrapped in the scrollable
`.article-list` container at all — it returns a different div. Since its own
hint text already says "Press `r` or hit Refresh," pull-to-refresh should
work there too for consistency. The empty state will be wrapped in a
scrollable container with the same touch listeners attached, so a pull
there triggers the same `onRefresh()`.

## Testing

No test framework exists for the frontend today (backend has pytest; frontend
has none) — consistent with the rest of this codebase, this ships without
automated tests. Verification is manual: exercise the gesture in a real
touch context (see plan's verification section) — pulling down at the top of
each view type (saved/unread/source/folder and the empty state), confirming
threshold behavior, confirming it no-ops mid-list and while already
refreshing, and confirming desktop mouse interaction is completely
unaffected.
