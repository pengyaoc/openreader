// Article chat (docs/superpowers/specs/2026-11-07-article-chat-design.md).
// One thread per article per account; the server keeps it, this renders it.
// Desktop: a column docked right of the article. ≤760px: a bottom sheet
// that opens at half height, drags to full, and rides above the iOS
// keyboard (tracked through visualViewport).
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, chatQueryKey, type ChatMessage } from '../api'

const MOBILE_QUERY = '(max-width: 760px)'
const STARTERS = ["What's the main argument?", 'What evidence does it give?', "What's left unsaid?"]
const MAX_QUESTION = 2000
// Drag distance (px) on the sheet handle that counts as a deliberate move.
const DRAG_COMMIT_PX = 70

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

/** Height of the visible viewport and how far the keyboard covers the
 *  layout viewport's bottom — iOS shrinks visualViewport, not the page. */
function useVisualViewport(active: boolean): { height: number; keyboard: number } {
  const read = () => {
    const vv = window.visualViewport
    if (!vv) return { height: window.innerHeight, keyboard: 0 }
    return { height: vv.height, keyboard: Math.max(0, window.innerHeight - vv.height - vv.offsetTop) }
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
  onClose: () => void
}

export function ChatPanel({ articleId, onClose }: Props) {
  const qc = useQueryClient()
  const isMobile = useMediaQuery(MOBILE_QUERY)
  const coarse = useMediaQuery('(pointer: coarse)')
  const viewport = useVisualViewport(isMobile)

  const thread = useQuery({ queryKey: chatQueryKey(articleId), queryFn: () => api.getChat(articleId) })
  const messages = thread.data?.messages ?? []

  const [draft, setDraft] = useState('')
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null)
  const [error, setError] = useState<{ message: string; question: string } | null>(null)
  const [full, setFull] = useState(false)
  const [dragY, setDragY] = useState(0)
  const [dragging, setDragging] = useState(false)
  const threadRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const appendMessages = (added: ChatMessage[]) =>
    qc.setQueryData<{ messages: ChatMessage[] }>(chatQueryKey(articleId), (old) => ({
      messages: [...(old?.messages ?? []), ...added],
    }))

  const send = useMutation({
    mutationFn: (question: string) => api.sendChat(articleId, question),
    onMutate: (question) => {
      setPendingQuestion(question)
      setError(null)
      setDraft('')
    },
    onSuccess: (data) => {
      appendMessages(data.messages)
      setPendingQuestion(null)
    },
    onError: async (err, question) => {
      // The server may have finished and saved the turn even though this
      // response never arrived (the phone slept mid-request). Look before
      // offering Retry, so the same question isn't asked twice.
      const fresh = await thread.refetch()
      const list = fresh.data?.messages ?? []
      const landed = list.length >= 2 && list[list.length - 2].role === 'user' && list[list.length - 2].body === question
      setPendingQuestion(null)
      if (landed) return
      setError({ message: err instanceof Error ? err.message : 'Something went wrong', question })
      setDraft((d) => d || question)
    },
  })

  const clear = useMutation({
    mutationFn: () => api.clearChat(articleId),
    onSuccess: () => {
      qc.setQueryData(chatQueryKey(articleId), { messages: [] })
      setError(null)
    },
  })

  const ask = (text: string) => {
    const question = text.trim()
    if (!question || question.length > MAX_QUESTION || send.isPending) return
    send.mutate(question)
  }

  // Keep the newest message in view.
  useLayoutEffect(() => {
    const el = threadRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length, pendingQuestion, error])

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Desktop: Enter sends, Shift+Enter breaks the line. Touch keyboards
    // keep Enter as a newline; the send button sends.
    if (e.key === 'Enter' && !e.shiftKey && !coarse && !e.nativeEvent.isComposing) {
      e.preventDefault()
      ask(draft)
    }
  }

  // Sheet handle drag (mobile only). Move/up are read on window, not the
  // handle: the sheet moves under the finger mid-drag, and a release the
  // handle never sees would leave the sheet stuck half-dragged.
  const onHandleDown = (e: PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    const startY = e.clientY
    let dy = 0
    setDragging(true)
    const move = (ev: globalThis.PointerEvent) => {
      dy = ev.clientY - startY
      setDragY(dy)
    }
    const end = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', end)
      window.removeEventListener('pointercancel', end)
      setDragging(false)
      setDragY(0)
      if (dy < -DRAG_COMMIT_PX) setFull(true)
      else if (dy > DRAG_COMMIT_PX) {
        if (full) setFull(false)
        else onClose()
      }
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', end)
    window.addEventListener('pointercancel', end)
  }

  const sheetStyle = isMobile
    ? {
        height: `${Math.round((full ? viewport.height - 44 : viewport.height * 0.5) - Math.min(0, dragY))}px`,
        bottom: `${viewport.keyboard}px`,
        // The home-bar inset only matters while the keyboard is down.
        paddingBottom: viewport.keyboard > 0 ? 0 : undefined,
        transform: dragY > 0 ? `translateY(${dragY}px)` : undefined,
        transition: dragging ? 'none' : 'height 0.2s ease, transform 0.2s ease',
      }
    : undefined

  const empty = messages.length === 0 && !pendingQuestion

  return (
    <aside className={`chat-panel ${isMobile ? 'chat-panel--sheet' : ''}`} style={sheetStyle} aria-label="Ask about this article">
      {isMobile && (
        <div
          className="chat-panel__handle"
          onPointerDown={onHandleDown}
          aria-hidden="true"
        >
          <span />
        </div>
      )}
      <div className="chat-panel__head">
        <span>Ask about this article</span>
        <div className="chat-panel__head-actions">
          {messages.length > 0 && (
            <button
              className="chat-panel__link"
              disabled={clear.isPending || send.isPending}
              onClick={() => {
                if (window.confirm('Start a new chat? This clears the conversation for this article.')) clear.mutate()
              }}
            >
              New chat
            </button>
          )}
          <button className="icon-btn" onClick={onClose} title="Close chat (c)" aria-label="Close chat">
            ✕
          </button>
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
        {pendingQuestion && (
          <>
            <div className="chat-msg chat-msg--user">{pendingQuestion}</div>
            <div className="chat-panel__status">
              <span className="spinner" /> Thinking…
            </div>
          </>
        )}
        {error && (
          <div className="chat-panel__error" role="alert">
            {error.message}{' '}
            <button className="chat-panel__link" onClick={() => ask(error.question)} disabled={send.isPending}>
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
          onFocus={() => isMobile && setFull(true)}
        />
        <button
          type="submit"
          className="chat-panel__send"
          disabled={send.isPending || !draft.trim()}
          aria-label="Send"
          title="Send"
        >
          {send.isPending ? <span className="spinner" /> : '↑'}
        </button>
      </form>
    </aside>
  )
}
