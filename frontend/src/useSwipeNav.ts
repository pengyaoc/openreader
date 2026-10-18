import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react'

// Article-to-article navigation by gesture, modelled on Summra's page turns
// (summra/frontend/static/js/pagination.js setupTouchGestures):
//
// Touch: once a drag moves 10px it locks to an axis; vertical is left to the
// browser's own scroll. A horizontal drag moves the whole track (current
// article plus a peek card for the neighbor on each side, see
// ArticleReader). On release it turns on a drag past 25% of the width or a
// quick flick, otherwise it springs back. Where there's no neighbor the drag
// meets resistance (a third of the finger's travel). Finger right-to-left =
// next, like turning a page.
//
// Trackpad: a sideways two-finger swipe (deltaX dominant) turns once its
// travel passes WHEEL_THRESHOLD, then ignores the rest of that gesture's
// momentum until the wheel has been quiet for WHEEL_QUIET_MS.

const AXIS_LOCK_PX = 10
const TURN_FRACTION = 0.25
const FLICK_MIN_PX = 30
const FLICK_MIN_SPEED = 0.5 // px/ms
const SNAP_BACK_MS = 200
const EDGE_GUARD_PX = 20 // leave the screen's left edge to iOS's back gesture
const WHEEL_THRESHOLD = 80
const WHEEL_QUIET_MS = 300
// If the turn didn't produce a new article (e.g. the next page of the list
// is still loading and then turns out empty), put the track back anyway.
const STALE_TURN_MS = 3000

interface Options {
  /** Element whose transform slides: the reader track. */
  trackRef: RefObject<HTMLElement | null>
  /** Element the gestures are read from. */
  surfaceRef: RefObject<HTMLElement | null>
  canPrev: boolean
  canNext: boolean
  onPrev: () => void
  onNext: () => void
  /** Changes when the shown article changes; ends a pending turn. */
  articleKey: number
}

function reducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

// A touch or wheel that starts inside something that scrolls sideways on its
// own (a code block, a wide table) belongs to that element, not to us.
function inHorizontalScroller(target: EventTarget | null, root: HTMLElement): boolean {
  let el = target instanceof Element ? target : null
  while (el && el !== root) {
    if (el.scrollWidth > el.clientWidth + 1) {
      const overflowX = getComputedStyle(el).overflowX
      if (overflowX === 'auto' || overflowX === 'scroll') return true
    }
    el = el.parentElement
  }
  return false
}

export function useSwipeNav({ trackRef, surfaceRef, canPrev, canNext, onPrev, onNext, articleKey }: Options) {
  // Latest values for the listeners, which are attached once.
  const live = useRef({ canPrev, canNext, onPrev, onNext })
  live.current = { canPrev, canNext, onPrev, onNext }
  // Direction of a turn that has been committed but whose article hasn't
  // rendered yet: 1 next, -1 previous.
  const pendingTurn = useRef<0 | 1 | -1>(0)
  const staleTimer = useRef<number | undefined>(undefined)

  // The new article has rendered (layout effect, so before paint): bring the
  // track back from off-screen and let the article settle in from the side
  // it arrived from.
  useLayoutEffect(() => {
    const side = pendingTurn.current
    const track = trackRef.current
    if (!side || !track) return
    pendingTurn.current = 0
    window.clearTimeout(staleTimer.current)
    if (reducedMotion()) {
      track.style.transition = 'none'
      track.style.transform = ''
      return
    }
    track.style.transition = 'none'
    track.style.transform = `translateX(${side * 32}px)`
    track.style.opacity = '0'
    void track.offsetWidth // commit the start position before transitioning
    track.style.transition = 'transform 180ms ease-out, opacity 180ms ease-out'
    track.style.transform = ''
    track.style.opacity = ''
  }, [articleKey, trackRef])

  useEffect(() => {
    const surface = surfaceRef.current
    const track = trackRef.current
    if (!surface || !track) return

    const slide = (x: number, ms: number) => {
      track.style.transition = ms ? `transform ${ms}ms ease-out` : 'none'
      track.style.transform = x ? `translateX(${x}px)` : ''
    }

    const turn = (forward: boolean) => {
      pendingTurn.current = forward ? 1 : -1
      window.clearTimeout(staleTimer.current)
      staleTimer.current = window.setTimeout(() => {
        pendingTurn.current = 0
        slide(0, 0)
      }, STALE_TURN_MS)
      if (forward) live.current.onNext()
      else live.current.onPrev()
    }

    let drag: { x: number; y: number; t: number; axis: 'x' | 'y' | null } | null = null
    let settleTimer: number | undefined

    const onTouchStart = (e: TouchEvent) => {
      const selection = window.getSelection()
      const touch = e.touches[0]
      if (
        e.touches.length !== 1 ||
        pendingTurn.current ||
        (selection && !selection.isCollapsed) ||
        touch.clientX < EDGE_GUARD_PX ||
        inHorizontalScroller(e.target, surface)
      ) {
        drag = null
        return
      }
      window.clearTimeout(settleTimer)
      drag = { x: touch.clientX, y: touch.clientY, t: e.timeStamp, axis: null }
    }

    const onTouchMove = (e: TouchEvent) => {
      if (!drag || e.touches.length !== 1) return
      const dx = e.touches[0].clientX - drag.x
      const dy = e.touches[0].clientY - drag.y
      if (!drag.axis && (Math.abs(dx) > AXIS_LOCK_PX || Math.abs(dy) > AXIS_LOCK_PX)) {
        drag.axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y'
        if (drag.axis === 'x') track.classList.add('is-swiping')
      }
      if (drag.axis !== 'x') return
      const blocked = dx > 0 ? !live.current.canPrev : !live.current.canNext
      slide(blocked ? dx / 3 : dx, 0)
    }

    const onRelease = (e: TouchEvent) => {
      const current = drag
      drag = null
      if (!current || current.axis !== 'x') return
      const dx = e.changedTouches[0].clientX - current.x
      const elapsed = Math.max(1, e.timeStamp - current.t)
      const width = surface.clientWidth
      const flick = Math.abs(dx) > FLICK_MIN_PX && Math.abs(dx) / elapsed > FLICK_MIN_SPEED
      const forward = dx < 0
      const canTurn = forward ? live.current.canNext : live.current.canPrev
      if (e.type === 'touchend' && canTurn && (Math.abs(dx) > width * TURN_FRACTION || flick)) {
        const ms = reducedMotion() ? 0 : Math.max(120, Math.round(300 * (1 - Math.abs(dx) / width)))
        slide(forward ? -width : width, ms)
        settleTimer = window.setTimeout(() => {
          track.classList.remove('is-swiping')
          turn(forward)
        }, ms)
      } else {
        slide(0, SNAP_BACK_MS)
        settleTimer = window.setTimeout(() => track.classList.remove('is-swiping'), SNAP_BACK_MS)
      }
    }

    let wheelTravel = 0
    let wheelSpent = false
    let wheelQuietTimer: number | undefined
    const onWheel = (e: WheelEvent) => {
      if (Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return
      if (inHorizontalScroller(e.target, surface)) return
      // Keep the sideways swipe from also becoming browser back/forward.
      e.preventDefault()
      window.clearTimeout(wheelQuietTimer)
      wheelQuietTimer = window.setTimeout(() => {
        wheelTravel = 0
        wheelSpent = false
      }, WHEEL_QUIET_MS)
      if (wheelSpent || pendingTurn.current) return
      wheelTravel += e.deltaX
      if (Math.abs(wheelTravel) < WHEEL_THRESHOLD) return
      const forward = wheelTravel > 0
      wheelSpent = true
      if (forward ? live.current.canNext : live.current.canPrev) {
        if (forward) live.current.onNext()
        else live.current.onPrev()
      }
    }

    surface.addEventListener('touchstart', onTouchStart, { passive: true })
    surface.addEventListener('touchmove', onTouchMove, { passive: true })
    surface.addEventListener('touchend', onRelease, { passive: true })
    surface.addEventListener('touchcancel', onRelease, { passive: true })
    surface.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      surface.removeEventListener('touchstart', onTouchStart)
      surface.removeEventListener('touchmove', onTouchMove)
      surface.removeEventListener('touchend', onRelease)
      surface.removeEventListener('touchcancel', onRelease)
      surface.removeEventListener('wheel', onWheel)
      window.clearTimeout(settleTimer)
      window.clearTimeout(wheelQuietTimer)
      window.clearTimeout(staleTimer.current)
    }
  }, [surfaceRef, trackRef])
}
