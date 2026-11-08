// Shared by ArticleReader (which opens the chat) and ChatPanel (which lays
// it out). At this width and below, chat is a full-screen overlay.
export const CHAT_FULLSCREEN_QUERY = '(max-width: 760px)'

/** iOS raises the keyboard only for a focus() made inside the tap itself,
 *  and the chat's input doesn't exist yet at that moment. So the tap
 *  focuses this throwaway input, and ChatPanel moves focus to the real one
 *  once it mounts: iOS keeps the keyboard up through that handoff. */
export function primeKeyboard(): void {
  const el = document.createElement('input')
  el.setAttribute('aria-hidden', 'true')
  el.tabIndex = -1
  // 16px, or iOS zooms the page on focus.
  el.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;font-size:16px;border:0;padding:0'
  document.body.appendChild(el)
  el.focus({ preventScroll: true })
  // Long enough for the panel to mount and take focus.
  window.setTimeout(() => el.remove(), 1000)
}
