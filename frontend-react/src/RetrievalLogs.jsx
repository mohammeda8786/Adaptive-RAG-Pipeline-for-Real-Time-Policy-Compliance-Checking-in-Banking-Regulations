import { useCallback, useState } from 'react'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')

function displayDate(value) {
  if (!value) return 'Timestamp unavailable'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function RetrievalLogs() {
  const [open, setOpen] = useState(false)
  const [requests, setRequests] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await fetch(`${API_BASE}/api/retrieval/logs?limit=50`, {
        credentials: 'include',
      })
      if (response.status === 401) {
        window.location.replace('/login')
        return
      }
      if (!response.ok) {
        throw new Error(`Could not load audit logs (HTTP ${response.status}).`)
      }
      const data = await response.json()
      const nextRequests = data.requests || []
      setRequests(nextRequests)
      setSelectedId((current) => (
        nextRequests.some((request) => request.request_id === current)
          ? current
          : nextRequests[0]?.request_id || null
      ))
    } catch (loadError) {
      setError(loadError.message || 'Could not load audit logs.')
    } finally {
      setLoading(false)
    }
  }, [])

  const selected = requests.find((request) => request.request_id === selectedId)
  const analyzerAttempts = selected?.events?.filter(
    (event) => event.event === 'query_analysis_attempt',
  ) || []

  return (
    <>
      <button
        className="query-log-toggle"
        type="button"
        aria-expanded={open}
        aria-controls="retrieval-logs"
        onClick={() => {
          if (!open) refresh()
          setOpen((value) => !value)
        }}
      >
        Retrieval Logs
        {requests.length > 0 && <span>{requests.length}</span>}
      </button>
      {open && (
        <div className="query-log-backdrop" onClick={() => setOpen(false)}>
          <aside
            className="query-log-panel"
            id="retrieval-logs"
            aria-label="Retrieval Logs"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="query-log-header">
              <div>
                <span className="context-kicker">DEBUGGING &amp; OBSERVABILITY</span>
                <h2>Retrieval Logs</h2>
              </div>
              <div className="query-log-header-actions">
                <button type="button" onClick={refresh} disabled={loading}>
                  {loading ? 'Refreshing…' : 'Refresh'}
                </button>
                <button
                  type="button"
                  className="query-log-close"
                  aria-label="Close Retrieval Logs"
                  onClick={() => setOpen(false)}
                >
                  ×
                </button>
              </div>
            </header>

            {error && <p className="query-log-state error">{error}</p>}
            {loading && requests.length === 0 && (
              <p className="query-log-state">Loading audit events…</p>
            )}
            {!loading && !error && requests.length === 0 && (
              <p className="query-log-state">
                No recorded chat requests yet. New requests will appear here after processing.
              </p>
            )}

            {requests.length > 0 && (
              <div className="query-log-content">
                <nav className="query-log-request-list" aria-label="Recent requests">
                  {requests.map((request) => (
                    <button
                      key={request.request_id}
                      type="button"
                      className={`query-log-request${request.request_id === selectedId ? ' selected' : ''}`}
                      onClick={() => setSelectedId(request.request_id)}
                    >
                      <strong>{request.original_query || 'Question unavailable'}</strong>
                      <span>{displayDate(request.timestamp)}</span>
                      <span className="query-log-request-meta">
                        {request.retrieval_mode}
                        {' · '}
                        {request.analyzer_status === 'not_used'
                          ? 'original question'
                          : `analyzer ${request.analyzer_status || 'pending'}`}
                        {' · '}
                        {request.status}
                      </span>
                    </button>
                  ))}
                </nav>

                {selected && (
                  <article className="query-log-detail">
                    <div className="query-log-kv">
                      <strong>Original question</strong>
                      <p>{selected.original_query || 'Not recorded'}</p>
                    </div>
                    <div className="query-log-summary-grid">
                      <span><strong>Timestamp</strong>{displayDate(selected.timestamp)}</span>
                      <span><strong>Request ID</strong><code>{selected.request_id}</code></span>
                      <span><strong>Status</strong>{selected.status}</span>
                      <span><strong>Response returned</strong>{selected.response_status}</span>
                      {selected.analyzer_status === 'not_used' ? (
                        <span><strong>Analyzer</strong>Not used for chat retrieval</span>
                      ) : (
                        <>
                          <span><strong>Analyzer decision</strong>{selected.analyzer_decision}</span>
                          <span><strong>Analyzer status</strong>{selected.analyzer_status}</span>
                        </>
                      )}
                      <span><strong>Retrieval mode</strong>{selected.retrieval_mode}</span>
                      <span><strong>Query source</strong>{selected.query_source}</span>
                      <span><strong>Planned</strong>{selected.planned_search_count}</span>
                      <span><strong>Attempted</strong>{selected.attempted_search_count}</span>
                      <span><strong>Executed</strong>{selected.executed_search_count}</span>
                      <span><strong>Succeeded</strong>{selected.successful_search_count}</span>
                      <span><strong>Failed</strong>{selected.failed_search_count}</span>
                      <span><strong>Passages</strong>{selected.total_passages}</span>
                    </div>
                    {selected.analyzer_status === 'failed' && (
                      <div className="query-log-error">
                        <strong>Analyzer failed</strong>
                        {selected.analyzer_error_type && ` · ${selected.analyzer_error_type}`}
                        {selected.analyzer_error && <p>{selected.analyzer_error}</p>}
                        {selected.fallback_strategy && (
                          <p>Fallback strategy: {selected.fallback_strategy}</p>
                        )}
                      </div>
                    )}
                    {analyzerAttempts.length > 0 && (
                      <details className="query-log-timeline">
                        <summary>Historical analyzer attempts ({analyzerAttempts.length})</summary>
                        <ol>
                          {analyzerAttempts.map((attempt, index) => (
                            <li key={`${attempt.timestamp}-${index}`}>
                              <strong>
                                Attempt {attempt.attempt || index + 1}/{attempt.max_attempts || analyzerAttempts.length}
                              </strong>
                              <span>
                                {attempt.model || 'Model unavailable'}
                                {attempt.response_model && attempt.response_model !== attempt.model
                                  ? ` · response model ${attempt.response_model}`
                                  : ''}
                              </span>
                              <span>
                                API {attempt.api_request_status || 'not recorded'}
                                {' · analyzer '}
                                {attempt.analyzer_status || 'not completed'}
                              </span>
                              <span>
                                Choices: {attempt.has_choices === null || attempt.has_choices === undefined
                                  ? 'not recorded'
                                  : attempt.has_choices ? 'present' : 'missing'}
                                {attempt.choices_count !== null && attempt.choices_count !== undefined
                                  ? ` (${attempt.choices_count})`
                                  : ''}
                                {' · message: '}
                                {attempt.first_message_present === null || attempt.first_message_present === undefined
                                  ? 'not recorded'
                                  : attempt.first_message_present ? 'present' : 'missing'}
                                {' · content: '}
                                {attempt.content_state || 'not received'}
                                {attempt.content_field_present === false
                                  ? ' (field missing)'
                                  : ''}
                                {attempt.content_length !== null && attempt.content_length !== undefined
                                  ? ` (${attempt.content_length} chars)`
                                  : ''}
                              </span>
                              <span>
                                Finish reason: {attempt.finish_reason || 'not provided'}
                                {' · response: '}
                                {attempt.response_empty
                                  ? 'empty'
                                  : attempt.response_truncated ? 'truncated' : 'complete'}
                                {' · refusal: '}
                                {attempt.refusal_present ? 'yes' : 'no'}
                                {' · incomplete: '}
                                {attempt.response_incomplete ? 'yes' : 'no'}
                                {' · reasoning field: '}
                                {attempt.reasoning_present ? 'present' : 'absent'}
                              </span>
                              {attempt.incomplete_details && (
                                <span>Incomplete details: {attempt.incomplete_details}</span>
                              )}
                              <span>
                                Output format: {attempt.response_format_mode || 'not recorded'}
                                {' · parse state: '}
                                {attempt.error_category || 'valid/unchecked'}
                              </span>
                              {attempt.parser_error_line !== null
                                && attempt.parser_error_line !== undefined && (
                                  <span>
                                    JSON parser: {attempt.parser_error_message || 'invalid JSON'}
                                    {' · line '}
                                    {attempt.parser_error_line}
                                    {', column '}
                                    {attempt.parser_error_column}
                                  </span>
                                )}
                              {(attempt.contains_markdown_fences
                                || attempt.contains_text_outside_json) && (
                                  <span>
                                    Non-JSON wrapping: {attempt.contains_markdown_fences
                                      ? 'Markdown fences'
                                      : ''}
                                    {attempt.contains_markdown_fences
                                      && attempt.contains_text_outside_json ? ' and ' : ''}
                                    {attempt.contains_text_outside_json
                                      ? 'text outside JSON' : ''}
                                  </span>
                                )}
                              {attempt.error_excerpt && (
                                <code className="query-log-error-excerpt">
                                  Near parser error: {attempt.error_excerpt}
                                </code>
                              )}
                              {(attempt.error || attempt.analyzer_error) && (
                                <p className="query-log-error">
                                  {attempt.exception_type && `${attempt.exception_type}: `}
                                  {attempt.error || attempt.analyzer_error}
                                </p>
                              )}
                            </li>
                          ))}
                        </ol>
                      </details>
                    )}
                    {selected.planned_search_count > 0 && (
                      <section className="query-log-searches">
                        <h3>Search plan ({selected.planned_search_count})</h3>
                        <ol className="query-log-planned-list">
                          {selected.searches.map((search) => (
                            <li key={search.operation}>
                              <code>{search.query || 'Query not recorded'}</code>
                              {search.purpose && <p>{search.purpose}</p>}
                              {search.evidence_requirement_ids?.length > 0 && (
                                <p>
                                  Covers: {search.evidence_requirement_ids.join(', ')}
                                </p>
                              )}
                            </li>
                          ))}
                        </ol>
                      </section>
                    )}
                    <div className="query-log-kv">
                      <strong>Retrieval note</strong>
                      <p>{selected.reason || 'No retrieval note recorded.'}</p>
                    </div>
                    {selected.plan_validation && (
                      <div className="query-log-kv">
                        <strong>Plan validation</strong>
                        <p>
                          {selected.plan_validation.valid
                            ? 'Valid'
                            : 'Invalid or not analyzer-validated'}
                          {selected.plan_validation.correction_attempted
                            ? ` · corrected on attempt ${selected.plan_validation.attempt_count}`
                            : ''}
                        </p>
                        {selected.plan_validation.issues?.length > 0 && (
                          <ul>
                            {selected.plan_validation.issues.map((issue, index) => (
                              <li key={`${index}-${issue}`}>{issue}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}
                    {selected.evidence_requirements?.length > 0 && (
                      <section className="query-log-searches">
                        <h3>Evidence requirements ({selected.evidence_requirements.length})</h3>
                        <ol className="query-log-planned-list">
                          {selected.evidence_requirements.map((requirement) => (
                            <li key={requirement.id}>
                              <strong>{requirement.topic}</strong>
                              <p>{requirement.description}</p>
                            </li>
                          ))}
                        </ol>
                      </section>
                    )}
                    <div className="query-log-answer-status">
                      <strong>Answer generation:</strong>{' '}
                      {selected.answer_generation_started ? 'started' : 'not started'}
                      {' · '}
                      {selected.answer_generation_status}
                      {selected.generation_method ? ` (${selected.generation_method})` : ''}
                      {' · '}
                      Context supplied: {selected.context_supplied === null
                        ? 'not recorded'
                        : selected.context_supplied ? 'yes' : 'no'}
                    </div>

                    <section className="query-log-searches">
                      <h3>Retrieval executions</h3>
                      {selected.searches.length === 0 && (
                        <p className="query-log-muted">
                          {selected.retrieval_skipped
                            ? 'Retrieval was explicitly skipped; zero vector searches were executed.'
                            : 'No retrieval executions recorded.'}
                        </p>
                      )}
                      {selected.searches.map((search) => (
                        <details className="query-log-search" key={search.operation}>
                          <summary>
                            <span>Search {search.operation}</span>
                            <span className={`query-log-badge ${search.status}`}>{search.status}</span>
                          </summary>
                          <div className="query-log-search-body">
                            <strong>Query source</strong>
                            <p>{search.query_source || selected.query_source}</p>
                            <strong>Exact query</strong>
                            <p>{search.query || 'Not recorded'}</p>
                            {search.purpose && (
                              <>
                                <strong>Purpose</strong>
                                <p>{search.purpose}</p>
                              </>
                            )}
                            <div className="query-log-search-meta">
                              {search.collection && <span>Collection: {search.collection}</span>}
                              {search.duration_ms !== undefined && <span>Duration: {search.duration_ms} ms</span>}
                              {search.passage_count !== undefined && <span>Passages: {search.passage_count}</span>}
                              {search.started_at && <span>Started: {displayDate(search.started_at)}</span>}
                              {search.ended_at && <span>Ended: {displayDate(search.ended_at)}</span>}
                            </div>
                            {search.error && <p className="query-log-error">{search.error}</p>}
                            {search.passages?.length > 0 && (
                              <div className="query-log-passages">
                                <strong>Returned passages</strong>
                                {search.passages.map((passage, index) => (
                                  <details key={`${search.operation}-${index}`}>
                                    <summary>
                                      Passage {passage.rank || index + 1}
                                      {passage.relevance !== undefined && ` · Qdrant similarity ${Number(passage.relevance).toFixed(4)}`}
                                    </summary>
                                    <p className="query-log-passage-text">{passage.excerpt || passage.text || 'No passage text recorded.'}</p>
                                    <div className="query-log-search-meta">
                                      {passage.title && <span>Title: {passage.title}</span>}
                                      {passage.document && <span>Document: {passage.document}</span>}
                                      {passage.page && <span>Page: {passage.page}</span>}
                                      {passage.section && <span>Section: {passage.section}</span>}
                                      {passage.chapter && <span>Chapter: {passage.chapter}</span>}
                                      {passage.reference && <span>Reference: {passage.reference}</span>}
                                    </div>
                                  </details>
                                ))}
                              </div>
                            )}
                          </div>
                        </details>
                      ))}
                    </section>

                    <details className="query-log-timeline">
                      <summary>Audit event timeline ({selected.events.length})</summary>
                      <ol>
                        {selected.events.map((event, index) => (
                          <li key={`${event.timestamp}-${index}`}>
                            <time>{displayDate(event.timestamp)}</time>
                            <code>{event.event}</code>
                          </li>
                        ))}
                      </ol>
                    </details>
                  </article>
                )}
              </div>
            )}
          </aside>
        </div>
      )}
    </>
  )
}

export default RetrievalLogs
