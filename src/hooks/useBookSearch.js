import { useState, useCallback, useRef } from 'react'

const GOOGLE_BOOKS_API = 'https://www.googleapis.com/books/v1/volumes'
const PAGE_SIZE = 20

export function useBookSearch() {
  const [books, setBooks] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [totalItems, setTotalItems] = useState(0)
  const [startIndex, setStartIndex] = useState(0)
  const lastQueryRef = useRef({ query: '', language: 'en' })

  const search = useCallback(async (query, language, index = 0) => {
    if (!query.trim()) return

    setLoading(true)
    setError(null)
    lastQueryRef.current = { query, language }

    const params = new URLSearchParams({
      q: query,
      langRestrict: language,
      maxResults: PAGE_SIZE,
      startIndex: index,
      printType: 'books',
      orderBy: 'relevance',
    })

    try {
      const res = await fetch(`${GOOGLE_BOOKS_API}?${params}`)
      if (!res.ok) throw new Error(`API error: ${res.status}`)
      const data = await res.json()

      const items = (data.items || []).map((item) => {
        const info = item.volumeInfo || {}
        return {
          id: item.id,
          title: info.title || 'Unknown title',
          authors: info.authors || [],
          description: info.description || '',
          thumbnail: info.imageLinks?.thumbnail?.replace('http:', 'https:') || null,
          publisher: info.publisher || '',
          publishedDate: info.publishedDate || '',
          pageCount: info.pageCount || null,
          categories: info.categories || [],
          averageRating: info.averageRating || null,
          ratingsCount: info.ratingsCount || 0,
          language: info.language || language,
          previewLink: info.previewLink || null,
        }
      })

      setTotalItems(data.totalItems || 0)
      setStartIndex(index)

      if (index === 0) {
        setBooks(items)
      } else {
        setBooks((prev) => [...prev, ...items])
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const loadMore = useCallback(() => {
    const { query, language } = lastQueryRef.current
    search(query, language, startIndex + PAGE_SIZE)
  }, [search, startIndex])

  return { books, loading, error, totalItems, startIndex, search, loadMore }
}
