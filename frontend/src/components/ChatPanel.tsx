// Article chat (docs/superpowers/specs/2026-11-07-article-chat-design.md).
// One thread per article per account; the server keeps it, this renders it.
// Desktop: a column docked right of the article. ≤760px: a full-screen
// overlay sized to the space above the iOS keyboard (visualViewport), so
// the input always sits right on top of it.
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, chatQueryKey } from '../api'
import { CHAT_FULLSCREEN_QUERY } from '../chatLayout'

const STARTERS = ["What's the main argument?", 'What evidence does it give?', "What's left unsaid?"]
const MAX_QUESTION = 2000

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const onChange = () => setMatches(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [query])
  return matches
}

/** The visible viewport: iOS shrinks visualViewport for the keyboard, not
 *  the page, and may scroll it down (offsetTop) when an input takes focus. */
function useVisualViewport(active: boolean): { height: number; top: number; keyboard: number } {
  const read = () => {
    const vv = window.visualViewport
    if (!vv) return { height: window.innerHeight, top: 0, keyboard: 0 }
    return {
      height: vv.height,
      top: vv.offsetTop,
      keyboard: Math.max(0, window.innerHeight - vv.height - vv.offsetTop),
    }
  }
  const [state, setState] = useState(read)
  useEffect(() => {
    const vv = window.visualViewport
    if (!active || !vv) return
    const update = () => setState(read())
    vv.addEventListener('resize', update)
    vv.addEventListener('scroll', update)
    update()
    return () => {
      vv.removeEventListener('resize', update)
      vv.removeEventListener('scroll', update)
    }
  }, [active])
  return state
}

interface Props {
  articleId: number
  articleTitle: string
  /** Focus the input on open (ArticleReader decides; on iOS it has already
   *  primed the keyboard during the tap — see chatLayout.ts). */
  autoFocus: boolean
  onClose: () => void
}

export function ChatPanel({ articleId, articleTitle, autoFocus, onClose }: Props) {
  const qc = useQueryClient()
  const fullscreen = useMediaQuery(CHAT_FULLSCREEN_QUERY)
  const coarse = useMediaQuery('(pointer: coarse)')
  const viewport = useVisualViewport(fullscreen)

  const thread = useQuery({
    queryKey: chatQueryKey(articleId),
    queryFn: () => api.getChat(articleId),
    // While the server is still answering (this panel's request, or one
    // from before a remount or a dropped connection), poll until it lands.
    refetchInterval: (q) => (q.state.data?.pending ? 2500 : false),
    // The cached copy (from the 💬 dot's prefetch) can predate a question
    // sent before the panel was closed and reopened.
    refetchOnMount: 'always',
  })
  const messages = thread.data?.messages ?? []
  const serverPending = thread.data?.pending ?? null

  const [draft, setDraft] = useState('')
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null)
  const [error, setError] = useState<{ message: string; question: string } | null>(null)
  const panelRef = useRef<HTMLElement>(null)
  const threadRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const send = useMutation({
    mutationFn: (question: string) => api.sendChat(articleId, question),
    onMutate: (question) => {
      setPendingQuestion(question)
      setError(null)
      setDraft('')
      return { baseline: messages.length }
    },
    onSuccess: async () => {
      // Refetch rather than append: a poll may already have picked the
      // turn up, and appending would show it twice.
      await qc.invalidateQueries({ queryKey: chatQueryKey(articleId) })
      setPendingQuestion(null)
    },
    onError: async (err, question, ctx) => {
      // The response may be lost while the server keeps going (the phone
      // slept mid-request), or the turn may already be saved. Look before
      // offering Retry, so the same question isn't asked twice.
      const fresh = await thread.refetch()
      setPendingQuestion(null)
      if (fresh.data?.pending) return // still answering: the poll shows it
      if ((fresh.data?.messages.length ?? 0) > (ctx?.baseline ?? Infinity)) return // it landed
      setError({ message: err instanceof Error ? err.message : 'Something went wrong', question })
      setDraft((d) => d || question)
    },
  })
  const busy = send.isPending || serverPending !== null
  const shownPending = pendingQuestion ?? serverPending

  const clear = useMutation({
    mutationFn: () => api.clearChat(articleId),
    onSuccess: () => {
      qc.setQueryData(chatQueryKey(articleId), { messages: [], pending: null })
      setError(null)
    },
  })

  const ask = (text: string) => {
    const question = text.trim()
    if (!question || question.length > MAX_QUESTION || busy || clear.isPending) return
    send.mutate(question)
  }

  // Keep the newest message in view.
  useLayoutEffect(() => {
    const el = threadRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length, shownPending, error])

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Desktop: Enter sends, Shift+Enter breaks the line. Touch keyboards
    // keep Enter as a newline; the send button sends.
    if (e.key === 'Enter' && !e.shiftKey && !coarse && !e.nativeEvent.isComposing) {
      e.preventDefault()
      ask(draft)
    }
  }

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus({ preventScroll: true })
    // Once, on open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Full screen: a drag anywhere on the chat moves only the conversation.
  // Left alone, iOS hands a drag the thread can't take (it's at its top or
  // bottom, or the finger is on the header or composer) to the article
  // behind, or pans the whole visible area while the keyboard is up, and
  // the panel jumps to follow. The input keeps its own touches, so moving
  // the cursor and scrolling a long question still work.
  useEffect(() => {
    const panel = panelRef.current
    if (!fullscreen || !panel) return
    let lastY = 0
    const onStart = (e: TouchEvent) => {
      lastY = e.touches[0]?.clientY ?? 0
    }
    const onMove = (e: TouchEvent) => {
      const target = e.target as Node
      if (inputRef.current?.contains(target)) return
      const y = e.touches[0]?.clientY ?? lastY
      const dy = y - lastY
      lastY = y
      const thread = threadRef.current
      if (!thread || !thread.contains(target)) {
        e.preventDefault()
        return
      }
      const atTop = thread.scrollTop <= 0
      const atBottom = thread.scrollTop + thread.clientHeight >= thread.scrollHeight - 1
      if ((dy > 0 && atTop) || (dy < 0 && atBottom)) e.preventDefault()
    }
    panel.addEventListener('touchstart', onStart, { passive: true })
    panel.addEventListener('touchmove', onMove, { passive: false })
    return () => {
      panel.removeEventListener('touchstart', onStart)
      panel.removeEventListener('touchmove', onMove)
    }
  }, [fullscreen])

  const panelStyle = fullscreen
    ? { top: `${viewport.top}px`, height: `${Math.round(viewport.height)}px` }
    : undefined
  const keyboardUp = fullscreen && viewport.keyboard > 0

  const empty = messages.length === 0 && !shownPending

  return (
    <aside
      ref={panelRef}
      className={`chat-panel ${fullscreen ? 'chat-panel--full' : ''} ${keyboardUp ? 'chat-panel--keyboard' : ''}`}
      style={panelStyle}
      aria-label="Ask about this article"
    >
      <div className="chat-panel__head">
        {fullscreen && (
          <button className="chat-panel__done" onClick={onClose}>
            Done
          </button>
        )}
        <span className="chat-panel__title">{fullscreen ? articleTitle : 'Ask about this article'}</span>
        <div className="chat-panel__head-actions">
          {messages.length > 0 && (
            <button
              className="chat-panel__link"
              disabled={clear.isPending || busy}
              onClick={() => {
                if (window.confirm('Start a new chat? This clears the conversation for this article.')) clear.mutate()
              }}
            >
              New chat
            </button>
          )}
          {!fullscreen && (
            <button className="icon-btn" onClick={onClose} title="Close chat (c)" aria-label="Close chat">
              ✕
            </button>
          )}
        </div>
      </div>

      <div className="chat-panel__thread" ref={threadRef}>
        {thread.isLoading ? (
          <div className="chat-panel__status">Loading…</div>
        ) : thread.isError ? (
          <div className="chat-panel__error">Couldn't load this conversation.</div>
        ) : empty ? (
          <div className="chat-panel__starters">
            {STARTERS.map((s) => (
              <button key={s} className="chat-panel__starter" onClick={() => ask(s)}>
                {s}
              </button>
            ))}
          </div>
        ) : (
          messages.map((m) =>
            m.role === 'user' ? (
              <div key={m.id} className="chat-msg chat-msg--user">
                {m.body}
              </div>
            ) : (
              <div
                key={m.id}
                className="chat-msg chat-msg--answer reader-body"
                dangerouslySetInnerHTML={{ __html: m.body }}
              />
            ),
          )
        )}
        {shownPending && (
          <>
            <div className="chat-msg chat-msg--user">{shownPending}</div>
            <div className="chat-panel__status">
              <span className="spinner" /> Thinking…
            </div>
          </>
        )}
        {error && (
          <div className="chat-panel__error" role="alert">
            {error.message}{' '}
            <button className="chat-panel__link" onClick={() => ask(error.question)} disabled={busy}>
              Retry
            </button>
          </div>
        )}
      </div>

      <form
        className="chat-panel__composer"
        onSubmit={(e) => {
          e.preventDefault()
          ask(draft)
        }}
      >
        <textarea
          ref={inputRef}
          className="chat-panel__input"
          rows={1}
          value={draft}
          maxLength={MAX_QUESTION}
          placeholder={messages.length ? 'Ask a follow-up…' : 'Ask about this article…'}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button
          type="submit"
          className="chat-panel__send"
          disabled={busy || !draft.trim()}
          aria-label="Send"
          title="Send"
        >
          {busy ? <span className="spinner" /> : '↑'}
        </button>
      </form>
    </aside>
  )
}
