import BookCard from './BookCard'

export default function BookList({ books, loading, error, totalItems, startIndex, onLoadMore }) {
  if (error) {
    return (
      <div className="status-message error">
        <p>Something went wrong: {error}</p>
      </div>
    )
  }

  if (!loading && books.length === 0) {
    return null
  }

  const hasMore = startIndex + 20 < totalItems

  return (
    <section className="results-section">
      {totalItems > 0 && (
        <p className="results-count">
          Found <strong>{totalItems.toLocaleString()}</strong> books
          {books.length < totalItems && ` — showing ${books.length}`}
        </p>
      )}

      <div className="book-grid">
        {books.map((book) => (
          <BookCard key={book.id} book={book} />
        ))}
      </div>

      {loading && (
        <div className="status-message loading">
          <div className="spinner" />
          <p>Searching books...</p>
        </div>
      )}

      {!loading && hasMore && (
        <div className="load-more-wrapper">
          <button className="load-more-btn" onClick={onLoadMore}>
            Load more books
          </button>
        </div>
      )}
    </section>
  )
}
