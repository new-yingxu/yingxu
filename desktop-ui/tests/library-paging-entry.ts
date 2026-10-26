// SSR bundle entry for the ui-slice-2 pure-frontend tests (no Tauri / Rust required).
export {
  LIBRARY_MAX_LIMIT,
  LIBRARY_MAX_OFFSET,
  LIBRARY_PAGE_SIZE,
  applyLibraryPage,
  canLoadNextPage,
  canLoadPreviousPage,
  createLibraryPage,
  describeLibraryPage,
  failLibraryLoad,
  libraryPageCount,
  libraryPageIndex,
  nextLibraryOffset,
  previousLibraryOffset,
  resetLibraryPage,
  startLibraryLoad,
} from "../src/api/library-paging";
export type { LibraryPageState } from "../src/api/library-paging";
