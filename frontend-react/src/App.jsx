import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import RetrievalLogs from './RetrievalLogs'
import './App.css'

const API_BASE = import.meta.env.VITE_API_BASE_URL
  || `http://${window.location.hostname || '127.0.0.1'}:8000`

function formatAnswer(value, sources = []) {
  const sourceIds = new Set(sources.map((source) => source.id))
  const html = marked.parse(String(value ?? ''), { gfm: true, breaks: true })
    .replace(/\[\s*(S\d+)\s*\]/g, (citation, sourceId) => (
      sourceIds.has(sourceId)
        ? `<button type="button" class="answer-citation" data-citation="${sourceId}" aria-label="Show evidence for ${sourceId}">${citation}</button>`
        : citation
    ))
  return DOMPurify.sanitize(html, { ADD_ATTR: ['data-citation'] })
}

function formatSourceExcerpt(value) {
  const blocks = String(value ?? '').trim().split(/\n\s*\n/)
  const html = blocks.map((block) => {
    const lines = block.split(/\r?\n/)
    const hasClauses = lines.some((line) => /^\s*(?:\(\d+\)|\([a-z]\)|[ivxlcdm]+\))\s+/i.test(line))
    if (!hasClauses) {
      return `<p>${lines.map((line) => escapeHtml(line)).join('<br>')}</p>`
    }

    const items = []
    let currentClause = null
    lines.forEach((line) => {
      const clause = line.match(/^\s*(\(\d+\)|\([a-z]\)|[ivxlcdm]+\))\s*(.*)$/i)
      if (clause) {
        currentClause = { marker: clause[1], lines: [clause[2]] }
        items.push(currentClause)
      } else if (currentClause) {
        currentClause.lines.push(line)
      } else {
        items.push({ lines: [line] })
      }
    })
    return `<div class="source-clause-group">${items.map((item) => (
      item.marker
        ? `<div class="source-clause"><strong class="source-clause-marker">${escapeHtml(item.marker)}</strong><span>${item.lines.map(escapeHtml).join('<br>')}</span></div>`
        : `<p>${item.lines.map(escapeHtml).join('<br>')}</p>`
    )).join('')}</div>`
  }).join('')
  return DOMPurify.sanitize(html)
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character])
}

function sourceCards(sources = []) {
  if (!sources.length) return null

  return (
    <details className="source-section">
      <summary className="source-summary">
        {sources.length} retrieved {sources.length === 1 ? 'passage' : 'passages'} · references and excerpts
      </summary>
      <p className="source-note">S1, S2, etc. are chat references for these retrieved passages, not RBI clause numbers. Check the excerpt and section details below.</p>
      {sources.map((source) => {
        const score = Number(source.relevance)
        const hasScore = source.relevance !== null
          && source.relevance !== undefined
          && Number.isFinite(score)
        const lowConfidence = hasScore && score < 0.5
        const section = source.section
          ? `${source.chapter ? `${source.chapter}, ` : ''}Section ${source.section}`
          : source.chapter || source.reference

        const metadata = [
          ['Source', source.document],
          ['Section', section],
          ['Clauses', source.clauses],
          ['Page', source.page || 'Not available in extracted source'],
        ].filter(([label, value]) => (
          label === 'Page'
            ? true
            : Array.isArray(value)
              ? value.length > 0
              : value !== undefined && value !== null && String(value).trim() !== ''
        ))

        return (
          <article key={source.id} className="source-card" data-source-id={source.id}>
            <div className="source-card-head">
              <span className="source-id">{source.id}</span>
              <span className="source-title">{source.title || source.document}</span>
              {hasScore && (
                <span className={`source-score${lowConfidence ? ' low-confidence' : ''}`}>
                  {lowConfidence ? 'Low confidence' : 'Similarity'} · {score.toFixed(4)}
                </span>
              )}
            </div>
            <div className="source-ref">
              {metadata.map(([label, value]) => (
                <span key={`${source.id}-${label}`} className="source-metadata-item">
                  <strong>{label}:</strong>{' '}
                  {Array.isArray(value) ? value.join(', ') : value}
                </span>
              ))}
            </div>
            <span className="source-evidence-label">Retrieved passage (as indexed)</span>
            <div className="source-excerpt" dangerouslySetInnerHTML={{ __html: formatSourceExcerpt(source.excerpt) }} />
          </article>
        )
      })}
    </details>
  )
}

async function api(path, options = {}) {
  const fullUrl = API_BASE + path
  const response = await fetch(fullUrl, { credentials: 'include', ...options })
  const contentType = response.headers.get('content-type') || ''
  const body = contentType.includes('application/json') ? await response.json() : await response.text()

  if (response.status === 401 && window.location.pathname !== '/login') {
    window.location.replace('/login')
  }
  if (!response.ok) {
    const message = typeof body === 'object'
      ? body.detail?.message || body.detail || body.message
      : body
    throw new Error(typeof message === 'string' ? message : JSON.stringify(message))
  }

  return body
}

function App() {
  const [messages, setMessages] = useState([])
  const [conversations, setConversations] = useState([])
  const [conversationId, setConversationId] = useState(null)
  const [input, setInput] = useState('')
  const inputRef = useRef(null)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState({ ready: false, state: 'Connecting...', error: null, point_count: 0 })
  const [user, setUser] = useState(null)
  const scrollRef = useRef(null)

  const addToast = useCallback((message, isError = false) => {
    const region = document.getElementById('toastRegion')
    if (!region) return
    const element = document.createElement('div')
    element.className = `toast${isError ? ' error' : ''}`
    element.textContent = message
    region.appendChild(element)
    window.setTimeout(() => element.remove(), 3800)
  }, [])

  const loadStatus = useCallback(async () => {
    try {
      const data = await api('/api/health')
      setStatus(data.retrieval || { ready: false, state: 'unavailable', point_count: 0 })
    } catch (error) {
      console.error('Status check failed:', error)
      setStatus({ ready: false, state: 'Index unavailable', error: String(error.message || error), point_count: 0 })
    }
  }, [])

  const loadConversations = useCallback(async () => {
    try {
      const { conversations: next } = await api('/api/conversations?limit=30')
      setConversations(next)
    } catch (error) {
      console.error('Failed to load conversations:', error)
    }
  }, [])

  const loadConversation = useCallback(async (id) => {
    try {
      const data = await api(`/api/conversations/${id}`)
      setConversationId(id)
      setMessages(data.messages || [])
      setInput('')
      const currentTitle = data.conversation?.title || 'Conversation'
      document.getElementById('topbarView').textContent = currentTitle
      setConversations((prev) => prev.some((item) => item.id === id) ? prev : [...prev, data.conversation])
    } catch (error) {
      addToast(error.message, true)
    }
  }, [addToast])

  const newConversation = useCallback(() => {
    setConversationId(null)
    setMessages([])
    setInput('')
    const title = document.getElementById('topbarView')
    if (title) title.textContent = 'New conversation'
    setConversations((prev) => prev.map((conversation) => ({ ...conversation, selected: false })))
  }, [])

  const deleteConversation = useCallback(async (id) => {
    if (!window.confirm('Delete this conversation?')) return
    try {
      await api(`/api/conversations/${id}`, { method: 'DELETE' })
      if (conversationId === id) {
        newConversation()
      }
      await loadConversations()
    } catch (error) {
      addToast(error.message, true)
    }
  }, [addToast, conversationId, loadConversations, newConversation])

  const updateMessageFeedback = useCallback(async (messageId, rating) => {
    try {
      await api(`/api/messages/${messageId}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating }),
      })
      addToast('Feedback recorded')
    } catch (error) {
      addToast(error.message, true)
    }
  }, [addToast])

  const sendMessage = useCallback(async (rawText, options = {}) => {
    const text = String(rawText || '').trim()
    if (!text || busy) return

    if (!options.retry) {
      setMessages((prev) => [...prev, { role: 'user', content: text }])
    }

    setInput('')
    setBusy(true)
    const loadingId = `loading-${Date.now()}`
    setMessages((prev) => [...prev, {
      id: loadingId,
      role: 'assistant',
      content: '',
      streaming: true,
      status: 'Searching RBI sources...',
    }])

    try {
      const response = await fetch(`${API_BASE}/api/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ conversation_id: conversationId, message: text }),
      })

      if (!response.ok) {
        if (response.status === 401) {
          window.location.replace('/login')
        }
        const contentType = response.headers.get('content-type') || ''
        const body = contentType.includes('application/json')
          ? await response.json()
          : await response.text()
        const detail = typeof body === 'object'
          ? body.detail?.message || body.detail || body.message
          : body
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
      }

      const reader = response.body?.getReader()
      if (!reader) throw new Error('The server did not provide a response stream.')
      const decoder = new TextDecoder()
      let buffer = ''
      let result = null

      const processEvent = (eventText) => {
        const data = eventText
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart())
          .join('\n')
        if (!data) return

        const event = JSON.parse(data)
        if (event.type === 'status') {
          setMessages((prev) => prev.map((message) => (
            message.id === loadingId ? { ...message, status: event.message } : message
          )))
        } else if (event.type === 'token') {
          setMessages((prev) => prev.map((message) => (
            message.id === loadingId
              ? { ...message, content: message.content + event.content, status: null }
              : message
          )))
        } else if (event.type === 'error') {
          throw new Error(event.message)
        } else if (event.type === 'done') {
          result = event
          setConversationId(event.conversation_id)
          const answer = event.report_setup
            ? 'This chat is focused on RBI regulatory questions. I can still retrieve and explain relevant requirements with source citations.'
            : event.answer
          setMessages((prev) => prev.map((message) => (
            message.id === loadingId
              ? {
                  ...message,
                  id: event.message_id,
                  content: answer,
                  sources: event.sources || [],
                  sufficient_evidence: event.sufficient_evidence,
                  streaming: false,
                  status: null,
                }
              : message
          )))
        }
      }

      while (true) {
        const { value, done } = await reader.read()
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
        let eventEnd = buffer.indexOf('\n\n')
        while (eventEnd !== -1) {
          processEvent(buffer.slice(0, eventEnd))
          buffer = buffer.slice(eventEnd + 2)
          eventEnd = buffer.indexOf('\n\n')
        }
        if (done) break
      }
      if (buffer.trim()) processEvent(buffer)
      if (!result) throw new Error('The response stream ended before the answer was complete.')

      const nextTitle = text.length > 44 ? `${text.slice(0, 44)}…` : text
      const titleEl = document.getElementById('topbarView')
      if (titleEl) titleEl.textContent = nextTitle
      await loadConversations()
    } catch (error) {
      setMessages((prev) => prev.map((message) => (
        message.id === loadingId
          ? { ...message, content: `I couldn't complete that request. ${error.message}`, streaming: false, status: null }
          : message
      )))
      addToast(error.message, true)
    } finally {
      setBusy(false)
      const inputEl = document.getElementById('messageInput')
      if (inputEl) inputEl.focus()
    }
  }, [addToast, busy, conversationId, loadConversations])

  useEffect(() => {
    let active = true
    api('/api/auth/me')
      .then(({ user: currentUser }) => {
        if (!active) return
        setUser(currentUser)
        loadStatus()
        loadConversations()
      })
      .catch(() => {
        if (active && window.location.pathname !== '/login') {
          window.location.replace('/login')
        }
      })
    return () => { active = false }
  }, [loadConversations, loadStatus])

  const signOut = useCallback(async () => {
    try {
      await api('/api/auth/logout', { method: 'POST' })
      window.location.assign('/login')
    } catch (error) {
      addToast(error.message, true)
    }
  }, [addToast])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

  const resizeInput = useCallback(() => {
    const textarea = inputRef.current
    if (!textarea) return

    textarea.style.height = 'auto'
    const styles = getComputedStyle(textarea)
    const minHeight = Number.parseFloat(styles.minHeight)
    const maxHeight = Number.parseFloat(styles.maxHeight)
    const height = textarea.value
      ? Math.min(Math.max(textarea.scrollHeight, minHeight), maxHeight)
      : minHeight
    textarea.style.height = `${height}px`
  }, [])

  useEffect(() => {
    resizeInput()
    window.addEventListener('resize', resizeInput)
    return () => window.removeEventListener('resize', resizeInput)
  }, [input, resizeInput])

  const handleMessageClick = async (event) => {
    const citationButton = event.target.closest('.answer-citation')
    if (citationButton) {
      const card = document.querySelector(`[data-source-id="${CSS.escape(citationButton.dataset.citation)}"]`)
      if (card) {
        const sourceSection = card.closest('.source-section')
        if (sourceSection) sourceSection.open = true
        window.requestAnimationFrame(() => {
          card.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
          card.style.animation = 'pulse 0.5s ease-in-out'
          window.setTimeout(() => { card.style.animation = '' }, 500)
        })
      }
      return
    }

    const actionButton = event.target.closest('[data-action]')
    if (!actionButton) return

    const { action, content, rating, id } = actionButton.dataset
    if (action === 'copy') {
      try {
        await navigator.clipboard.writeText(content)
        addToast('Copied to clipboard')
      } catch (error) {
        console.error('Failed to copy message:', error)
        addToast('Could not copy message to clipboard', true)
      }
    } else if (action === 'feedback') {
      updateMessageFeedback(Number(id), rating)
    } else if (action === 'retry') {
      const lastUser = [...messages].reverse().find((message) => message.role === 'user')
      if (lastUser) sendMessage(lastUser.content, { retry: true })
    }
  }

  const visibleMessages = useMemo(() => messages.filter((message) => !message.loading), [messages])

  return (
    <div className="app-shell">
      <aside className="sidebar" id="sidebar" aria-label="Main navigation">
        <div className="brand-row">
          <img className="brand-mark" src="/rbi-logo.avif" alt="RegulaMind AI" />
          <div className="brand-copy">
            <strong>RegulaMind AI</strong>
            <small>REGULATORY INTELLIGENCE</small>
          </div>
          <button className="icon-button sidebar-collapse" id="collapseSidebar" type="button" aria-label="Collapse sidebar" title="Collapse sidebar">
            <svg viewBox="0 0 24 24"><path d="M9 5 2 12l7 7M22 5v14" /></svg>
          </button>
        </div>

        <button className="new-chat-button" type="button" id="newChat" onClick={newConversation}>
          <svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg>
          <span>New chat</span>
          <kbd>⌘ K</kbd>
        </button>

        <div className="sidebar-section-heading">
          <span>Recent conversations</span>
          <button className="text-icon" id="refreshConversations" type="button" aria-label="Refresh conversations" onClick={loadConversations}>↻</button>
        </div>

        <div className="conversation-list" aria-live="polite">
          {conversations.length ? conversations.map((conversation) => (
            <div key={conversation.id} className="conversation-item">
              <button
                type="button"
                className={`conversation-row${conversation.id === conversationId ? ' selected' : ''}`}
                data-conversation-id={conversation.id}
                onClick={() => loadConversation(conversation.id)}
              >
                <svg viewBox="0 0 24 24"><path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l1.7-3.3A7.5 7.5 0 1 1 20 11.5Z" /></svg>
                <span>{conversation.title}</span>
              </button>
              <button className="conversation-delete" type="button" aria-label={`Delete conversation: ${conversation.title}`} title="Delete conversation" onClick={() => deleteConversation(conversation.id)}>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18M8 6V4h8v2m3 0-1 14H6L5 6m4 4v6m6-6v6" /></svg>
              </button>
            </div>
          )) : <div className="empty-sidebar">Your conversations will appear here.</div>}
        </div>

        <div className="sidebar-bottom">
          {user && (
            <div className="account-row">
              <span className="account-avatar" aria-hidden="true">{user.name.slice(0, 1).toUpperCase()}</span>
              <span className="account-copy"><strong>{user.name}</strong><small>{user.email}</small></span>
              <button className="account-signout" type="button" onClick={signOut}>Sign out</button>
            </div>
          )}
          <div className="workspace-status">
            <span className={`status-dot${status.ready ? ' ready' : status.state === 'Index unavailable' ? ' error' : ''}`}></span>
            <span id="workspaceStatus">{status.ready ? `RBI index: ${status.point_count} sources` : status.state}</span>
          </div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <button className="icon-button mobile-menu" id="mobileMenu" type="button" aria-label="Open navigation">
            <svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
          </button>
          <div className="topbar-context">
            <span className="context-kicker">REGULATORY ASSISTANT</span>
            <span className="topbar-divider">/</span>
            <span id="topbarView">New conversation</span>
          </div>
          <div className="topbar-actions">
            <span className="model-indicator"><i></i><span id="modelLabel">RBI sources</span></span>
            <RetrievalLogs />
            {user
              ? <button className="topbar-auth-link" type="button" onClick={signOut}>Log out</button>
              : <a className="topbar-auth-link" href="/login">Sign in</a>}
          </div>
        </header>

        <section className="view chat-view" id="chatView">
          <div className="conversation-scroll" ref={scrollRef}>
            {visibleMessages.length === 0 && (
              <div className="welcome-state" id="welcomeState">
                <div className="welcome-eyebrow"><span className="eyebrow-rule"></span> REGULAMIND AI</div>
                <h1>Understand the rule.<br /><span>See the evidence.</span></h1>
                <p className="welcome-copy">Ask about a direction, obligation, or control. Answers are grounded in the RBI source text and cited by document and page.</p>
                <div className="corpus-note"><span className="note-lock">◇</span><span>Answers cite the indexed corpus. Confirm applicability and effective dates against the official RBI publication.</span></div>
              </div>
            )}

            <div className="message-list" aria-live="polite">
              {visibleMessages.map((message, index) => {
                const isAssistant = message.role === 'assistant'
                const articleKey = message.id || `${message.role}-${index}`

                return (
                  <article key={articleKey} className={`message ${isAssistant ? 'assistant' : 'user'}`} onClick={handleMessageClick}>
                    {isAssistant && <div className="assistant-mark" aria-hidden="true">R</div>}
                    <div className="message-bubble">
                      {message.content
                        ? <span dangerouslySetInnerHTML={{ __html: isAssistant ? formatAnswer(message.content, message.sources || []) : formatAnswer(message.content) }} />
                        : message.streaming && <span className="stream-status">{message.status || 'Writing response...'}<span className="streaming-dots" aria-hidden="true">...</span></span>}
                      {message.streaming && message.content && <span className="stream-cursor" aria-hidden="true"></span>}
                    </div>
                    {!isAssistant && (
                      <div className="message-actions">
                        <button type="button" data-action="copy" data-content={message.content || ''} aria-label="Copy your message" title="Copy message">
                          <svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></svg>
                        </button>
                      </div>
                    )}
                    {isAssistant && sourceCards(message.sources || [])}
                    {isAssistant && !message.streaming && (
                      <div className="message-actions">
                        <button type="button" data-action="copy" data-content={message.content || ''} aria-label="Copy answer" title="Copy answer">
                          <svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></svg>
                        </button>
                        <button type="button" data-action="feedback" data-rating="helpful" data-id={message.message_id || message.id || ''} aria-label="Helpful" title="Helpful">
                          <svg viewBox="0 0 24 24"><path d="M7 10v11H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3ZM7 10l5-8 1.5 1a3 3 0 0 1 1 3.5L13 10h6a2 2 0 0 1 2 2l-1.5 7a2 2 0 0 1-2 1H7" /></svg>
                        </button>
                        <button type="button" data-action="feedback" data-rating="not_helpful" data-id={message.message_id || message.id || ''} aria-label="Not helpful" title="Not helpful">
                          <svg viewBox="0 0 24 24"><path d="M7 14V3H4a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3ZM7 14l5 8 1.5-1a3 3 0 0 0 1-3.5L13 14h6a2 2 0 0 0 2-2l-1.5-7a2 2 0 0 0-2-1H7" /></svg>
                        </button>
                        <button type="button" data-action="retry" aria-label="Retry response" title="Retry">
                          <svg viewBox="0 0 24 24"><path d="M20 7v5h-5M4 17v-5h5" /><path d="M5.6 9A7 7 0 0 1 18 6.5L20 12M4 12l2 5.5A7 7 0 0 0 18.4 15" /></svg>
                        </button>
                      </div>
                    )}
                  </article>
                )
              })}
            </div>
          </div>

          <div className="composer-dock">
            <div className="composer-shell">
              <textarea
                id="messageInput"
                ref={inputRef}
                rows="1"
                maxLength="8000"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault()
                    sendMessage(input)
                  }
                }}
                placeholder="Ask about an RBI direction or compliance obligation…"
                aria-label="Message RegulaMind AI"
              />
              <div className="composer-toolbar">
                <div className="composer-right">
                  <span className="composer-hint">Evidence-led · RBI sources</span>
                  <button className="send-button" type="button" id="sendButton" disabled={busy || !input.trim()} onClick={() => sendMessage(input)} aria-label="Send message" title="Send message">
                    <svg viewBox="0 0 24 24"><path d="m5 12 14-7-4 14-3-6-7-1Z" /><path d="m12 13 7-8" /></svg>
                  </button>
                </div>
              </div>
            </div>
            <p className="disclaimer">AI-generated regulatory assistance. Verify cited source text before making compliance decisions.</p>
          </div>
        </section>
      </main>

      <div className="toast-region" id="toastRegion" aria-live="polite" />
    </div>
  )
}

export default App
