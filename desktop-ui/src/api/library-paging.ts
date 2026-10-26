import type { MediaFile, MediaPage } from './contracts'

/** Rust `library.searchPage` accepts limit 1..200 and offset <= 1_000_000. */
export const LIBRARY_MAX_LIMIT = 200
export const LIBRARY_MAX_OFFSET = 1_000_000
export const LIBRARY_PAGE_SIZE = 60

export type LibraryPageStatus = 'idle' | 'loading' | 'ready' | 'empty' | 'error'

export interface LibraryPageState {
  items: MediaFile[]
  total: number
  offset: number
  limit: number
  hasMore: boolean
  status: LibraryPageStatus
  error?: string
}

export function clampLibraryLimit(limit: number): number {
  if (!Number.isSafeInteger(limit) || limit < 1) return LIBRARY_PAGE_SIZE
  return Math.min(limit, LIBRARY_MAX_LIMIT)
}

/** A changed search condition always restarts at offset 0. */
export function resetLibraryPage(
  state: LibraryPageState,
  limit: number = state.limit,
): LibraryPageState {
  return {
    items: [],
    total: 0,
    offset: 0,
    limit: clampLibraryLimit(limit),
    hasMore: false,
    status: 'idle',
  }
}

export function createLibraryPage(limit: number = LIBRARY_PAGE_SIZE): LibraryPageState {
  return resetLibraryPage(
    { items: [], total: 0, offset: 0, limit, hasMore: false, status: 'idle' },
    limit,
  )
}

export function startLibraryLoad(state: LibraryPageState): LibraryPageState {
  return { ...state, status: 'loading', error: undefined }
}

/**
 * Applies one server page. offset 0 replaces the list, a later offset appends,
 * an earlier offset truncates back. A reply that contradicts the request is
 * rejected instead of shown with a wrong total.
 */
export function applyLibraryPage(
  state: LibraryPageState,
  page: MediaPage,
): LibraryPageState {
  if (
    !page ||
    !Array.isArray(page.items) ||
    !Number.isSafeInteger(page.total) ||
    page.total < 0 ||
    !Number.isSafeInteger(page.offset) ||
    page.offset < 0 ||
    page.offset > LIBRARY_MAX_OFFSET ||
    !Number.isSafeInteger(page.limit) ||
    page.limit < 1 ||
    page.limit > LIBRARY_MAX_LIMIT ||
    typeof page.hasMore !== 'boolean' ||
    page.items.length > page.limit ||
    page.offset + page.items.length > page.total ||
    page.hasMore !== (page.offset + page.items.length < page.total)
  )
    return failLibraryLoad(state, '资料库分页响应不完整或与请求不匹配')
  if (page.offset === 0) {
    const items = page.items.slice(0, page.limit)
    return {
      items,
      total: page.total,
      offset: 0,
      limit: page.limit,
      hasMore: page.hasMore,
      status: items.length ? 'ready' : 'empty',
      error: undefined,
    }
  }
  if (page.offset > state.offset) {
    const seen = new Set(state.items.map((item) => item.id))
    const items = [...state.items]
    for (const item of page.items) {
      if (!seen.has(item.id)) {
        seen.add(item.id)
        items.push(item)
      }
    }
    return {
      items,
      total: page.total,
      offset: page.offset,
      limit: page.limit,
      hasMore: page.hasMore,
      status: items.length ? 'ready' : 'empty',
      error: undefined,
    }
  }
  const items = state.items.slice(0, page.offset + page.items.length)
  return {
    items,
    total: page.total,
    offset: page.offset,
    limit: page.limit,
    hasMore: page.hasMore,
    status: items.length ? 'ready' : 'empty',
    error: undefined,
  }
}

export function failLibraryLoad(
  state: LibraryPageState,
  message: string,
): LibraryPageState {
  return { ...state, status: 'error', error: message }
}

export function canLoadNextPage(state: LibraryPageState): boolean {
  return state.status !== 'loading' && state.hasMore
}

export function canLoadPreviousPage(state: LibraryPageState): boolean {
  return state.status !== 'loading' && state.offset > 0
}

export function nextLibraryOffset(state: LibraryPageState): number {
  return Math.min(state.offset + state.limit, LIBRARY_MAX_OFFSET)
}

export function previousLibraryOffset(state: LibraryPageState): number {
  return Math.max(0, state.offset - state.limit)
}

export function libraryPageIndex(state: LibraryPageState): number {
  return state.limit ? Math.floor(state.offset / state.limit) + 1 : 1
}

export function libraryPageCount(state: LibraryPageState): number {
  return state.limit ? Math.max(1, Math.ceil(state.total / state.limit)) : 1
}

export function describeLibraryPage(state: LibraryPageState): string {
  if (state.status === 'idle') return '尚未检索资料库'
  if (state.status === 'error') return state.error || '资料库读取失败'
  if (!state.total) return '没有匹配素材'
  return `共 ${state.total} 项 · 第 ${libraryPageIndex(state)}/${libraryPageCount(state)} 页 · 已显示 ${state.items.length} 项`
}
