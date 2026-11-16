const CHUNK_LOAD_ERROR_PATTERNS = [
  /failed to fetch dynamically imported module/i,
  /loading chunk [\w-]+ failed/i,
  /error loading dynamically imported module/i,
]

export function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? '')
  return CHUNK_LOAD_ERROR_PATTERNS.some((pattern) => pattern.test(message))
}
