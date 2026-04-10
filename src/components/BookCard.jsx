export default function BookCard({ book }) {
  const stars = book.averageRating
    ? Array.from({ length: 5 }, (_, i) => (i < Math.round(book.averageRating) ? '★' : '☆')).join('')
    : null

  return (
    <article className="book-card">
      <div className="book-cover">
        {book.thumbnail ? (
          <img src={book.thumbnail} alt={`Cover of ${book.title}`} loading="lazy" />
        ) : (
          <div className="book-cover-placeholder">
            <span>No cover</span>
          </div>
        )}
      </div>

      <div className="book-info">
        <h3 className="book-title">{book.title}</h3>

        {book.authors.length > 0 && (
          <p className="book-authors">{book.authors.join(', ')}</p>
        )}

        {stars && (
          <div className="book-rating">
            <span className="stars">{stars}</span>
            <span className="rating-count">({book.ratingsCount})</span>
          </div>
        )}

        {book.categories.length > 0 && (
          <div className="book-categories">
            {book.categories.slice(0, 2).map((cat) => (
              <span key={cat} className="category-tag">{cat}</span>
            ))}
          </div>
        )}

        {book.description && (
          <p className="book-description">{book.description}</p>
        )}

        <div className="book-meta">
          {book.publishedDate && <span>{book.publishedDate.slice(0, 4)}</span>}
          {book.pageCount && <span>{book.pageCount} pages</span>}
          {book.publisher && <span>{book.publisher}</span>}
        </div>

        {book.previewLink && (
          <a
            href={book.previewLink}
            target="_blank"
            rel="noopener noreferrer"
            className="preview-link"
          >
            Preview on Google Books
          </a>
        )}
      </div>
    </article>
  )
}
