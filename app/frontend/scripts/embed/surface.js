/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * Parse the provider-agnostic Embed surface contract from the AtlasClaw URL.
 * Host page state is deliberately excluded: v1 receives only normalized paths
 * through the validated postMessage bridge.
 *
 * The floating-surface ``host_origin`` query parameter is accepted only when
 * it matches a trusted runtime source (the real iframe parent origin, the
 * loading referrer, or an explicit deployment allowlist) — a well-formed but
 * attacker-chosen origin is rejected (null), which disables the bridge.
 *
 * @param {Location|URL|string} locationLike - Browser location or test URL.
 * @param {Window|object} [runtime] - Window providing trusted origin sources
 *   (defaults to the global window; tests may pass a stub).
 * @returns {{embedded: boolean, surface: string|null, hostOrigin: string|null, nonce: string|null, integrationMode: boolean}}
 */
export function parseEmbedSurface(locationLike = window.location, runtime = window) {
  const url = toUrl(locationLike)
  const params = url.searchParams
  const embedded = parseBooleanParam(
    params.get('embedded') || params.get('embed') || params.get('iframe')
  )
  const requestedSurface = String(params.get('surface') || '').trim().toLowerCase()
  const surface = ['floating', 'menu'].includes(requestedSurface) ? requestedSurface : null
  const integrationMode = embedded && !!surface

  return Object.freeze({
    embedded,
    surface: integrationMode ? surface : null,
    hostOrigin: integrationMode && surface === 'floating'
      ? resolveTrustedHostOrigin(normalizeOrigin(params.get('host_origin')), runtime)
      : null,
    nonce: integrationMode && surface === 'floating'
      ? normalizeNonce(params.get('nonce'))
      : null,
    integrationMode
  })
}

/**
 * Apply surface classes without changing the legacy embedded menu classes.
 *
 * @param {ReturnType<typeof parseEmbedSurface>} surface - Parsed surface.
 * @param {HTMLElement} root - Document root or body.
 */
export function applySurfaceClasses(surface, root) {
  if (!root?.classList) return
  root.classList.toggle('atlas-surface-floating', surface?.surface === 'floating')
  root.classList.toggle('atlas-surface-menu', surface?.surface === 'menu')
}

function toUrl(locationLike) {
  if (locationLike instanceof URL) return locationLike
  if (typeof locationLike === 'string') return new URL(locationLike, 'http://localhost')
  return new URL(locationLike?.href || 'http://localhost/')
}

function parseBooleanParam(value) {
  return ['1', 'true', 'yes'].includes(String(value || '').trim().toLowerCase())
}

function normalizeOrigin(value) {
  if (!value) return null
  try {
    const origin = new URL(String(value)).origin
    return origin === 'null' ? null : origin
  } catch (_) {
    return null
  }
}

/**
 * Accept the claimed host origin only when a trusted runtime source
 * confirms it. Trusted sources are:
 * - ``window.location.ancestorOrigins`` — the real iframe ancestor chain
 *   (Chromium/Safari; absent elsewhere),
 * - ``document.referrer`` — the parent document that loaded this iframe
 *   (empty under a strict referrer policy),
 * - ``window.__ATLASCLAW_EMBED_ALLOWED_HOST_ORIGINS__`` — explicit
 *   deployment allowlist for hosts that hide both of the above.
 * With no confirmation the claim is rejected (null) and the bridge stays
 * disabled — failing closed instead of trusting the URL parameter.
 */
function resolveTrustedHostOrigin(claimedOrigin, runtime) {
  if (!claimedOrigin) return null
  const trusted = collectTrustedHostOrigins(runtime)
  return trusted.includes(claimedOrigin) ? claimedOrigin : null
}

function collectTrustedHostOrigins(runtime) {
  const origins = new Set()

  const ancestorOrigins = runtime?.location?.ancestorOrigins
  if (ancestorOrigins) {
    for (const entry of Array.from(ancestorOrigins)) {
      const normalized = normalizeOrigin(entry)
      if (normalized) origins.add(normalized)
    }
  }

  const referrerOrigin = normalizeOrigin(runtime?.document?.referrer || '')
  if (referrerOrigin) origins.add(referrerOrigin)

  const allowlist = runtime?.__ATLASCLAW_EMBED_ALLOWED_HOST_ORIGINS__
  if (Array.isArray(allowlist)) {
    for (const entry of allowlist) {
      const normalized = normalizeOrigin(entry)
      if (normalized) origins.add(normalized)
    }
  }

  return Array.from(origins)
}

function normalizeNonce(value) {
  const normalized = String(value || '').trim()
  return /^[A-Za-z0-9_-]{22,256}$/.test(normalized) ? normalized : null
}
