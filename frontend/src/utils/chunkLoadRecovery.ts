const CHUNK_LOAD_ERROR_PATTERNS = [
  /failed to fetch dynamically imported module/i,
  /loading chunk [\w-]+ failed/i,
  /error loading dynamically imported module/i,
  /importing a module script failed/i,
]

export function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? '')
  return CHUNK_LOAD_ERROR_PATTERNS.some((pattern) => pattern.test(message))
}

export function readSessionFlag(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key)
  } catch {
    return null
  }
}

export function writeSessionFlag(key: string, value: string): boolean {
  try {
    window.sessionStorage.setItem(key, value)
    return true
  } catch {
    return false
  }
}

export function removeSessionFlag(key: string): void {
  try {
    window.sessionStorage.removeItem(key)
  } catch {
    // Recovery must remain usable when browser storage is unavailable.
  }
}
