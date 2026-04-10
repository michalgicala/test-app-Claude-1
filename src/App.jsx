import { useState, useRef, useEffect } from 'react'
import { useBookSearch } from './hooks/useBookSearch'
import BookList from './components/BookList'
import './App.css'

const SUGGESTED_QUERIES = {
  en: ['science fiction', 'mystery thriller', 'biography', 'history', 'fantasy', 'psychology', 'cooking', 'travel'],
  pl: ['kryminał', 'powieść historyczna', 'fantastyka', 'biografia', 'horror', 'romans', 'psychologia', 'podróże'],
}

const UI_TEXT = {
  en: {
    title: 'Book Search',
    subtitle: 'Discover your next favourite read',
    placeholder: 'Search by title, author, or keyword…',
    searchBtn: 'Search',
    langLabel: 'Language',
    suggestions: 'Popular searches:',
    clearBtn: 'Clear',
  },
  pl: {
    title: 'Wyszukiwarka Książek',
    subtitle: 'Odkryj swoją następną ulubioną lekturę',
    placeholder: 'Szukaj po tytule, autorze lub słowie kluczowym…',
    searchBtn: 'Szukaj',
    langLabel: 'Język',
    suggestions: 'Popularne wyszukiwania:',
    clearBtn: 'Wyczyść',
  },
}

export default function App() {
  const [query, setQuery] = useState('')
  const [language, setLanguage] = useState('pl')
  const [submittedQuery, setSubmittedQuery] = useState('')
  const inputRef = useRef(null)

  const { books, loading, error, totalItems, startIndex, search, loadMore } = useBookSearch()
  const ui = UI_TEXT[language]

  const handleSubmit = (e) => {
    e.preventDefault()
    if (!query.trim()) return
    setSubmittedQuery(query.trim())
    search(query.trim(), language)
  }

  const handleSuggestion = (suggestion) => {
    setQuery(suggestion)
    setSubmittedQuery(suggestion)
    search(suggestion, language)
    inputRef.current?.focus()
  }

  const handleClear = () => {
    setQuery('')
    setSubmittedQuery('')
    inputRef.current?.focus()
  }

  // Re-run current search when language changes (if a query is active)
  useEffect(() => {
    if (submittedQuery) {
      search(submittedQuery, language)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [language])

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-top">
          <div className="brand">
            <span className="brand-icon">📚</span>
            <div>
              <h1 className="app-title">{ui.title}</h1>
              <p className="app-subtitle">{ui.subtitle}</p>
            </div>
          </div>

          <div className="lang-switcher">
            <span className="lang-label">{ui.langLabel}:</span>
            <button
              className={`lang-btn ${language === 'pl' ? 'active' : ''}`}
              onClick={() => setLanguage('pl')}
            >
              PL
            </button>
            <span className="lang-divider">/</span>
            <button
              className={`lang-btn ${language === 'en' ? 'active' : ''}`}
              onClick={() => setLanguage('en')}
            >
              EN
            </button>
          </div>
        </div>

        <form className="search-form" onSubmit={handleSubmit}>
          <div className="search-input-wrapper">
            <span className="search-icon">🔍</span>
            <input
              ref={inputRef}
              type="text"
              className="search-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={ui.placeholder}
              aria-label="Search books"
            />
            {query && (
              <button type="button" className="clear-btn" onClick={handleClear} aria-label="Clear search">
                ×
              </button>
            )}
          </div>
          <button type="submit" className="search-btn" disabled={loading || !query.trim()}>
            {loading && submittedQuery ? '…' : ui.searchBtn}
          </button>
        </form>

        <div className="suggestions">
          <span className="suggestions-label">{ui.suggestions}</span>
          <div className="suggestion-tags">
            {SUGGESTED_QUERIES[language].map((s) => (
              <button key={s} className="suggestion-tag" onClick={() => handleSuggestion(s)}>
                {s}
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className="app-main">
        <BookList
          books={books}
          loading={loading}
          error={error}
          totalItems={totalItems}
          startIndex={startIndex}
          onLoadMore={loadMore}
        />
      </main>
    </div>
  )
}
