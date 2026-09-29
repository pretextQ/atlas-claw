/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

import { buildApiUrl, buildAppUrl, rewriteManagedAppUrl } from './config.js'
import { clearSession } from './session-manager.js'

const AUTH_STORAGE_KEY = 'atlasclaw_auth_token'
const AUTH_HEADER_NAME = 'AtlasClaw-Authenticate'

export function redirectToLogin() {
  const current = `${window.location.pathname}${window.location.search}`
  const target = encodeURIComponent(current || '/')
  window.location.href = buildAppUrl(`/login.html?redirect=${target}`)
}

export function getAuthToken() {
  try {
    return sessionStorage.getItem(AUTH_STORAGE_KEY) || ''
  } catch (_error) {
    return ''
  }
}

export function setAuthToken(token) {
  try {
    if (token) {
      sessionStorage.setItem(AUTH_STORAGE_KEY, token)
    }
  } catch (_error) {
    // ignore
  }
}

export function clearAuthToken() {
  try {
    sessionStorage.removeItem(AUTH_STORAGE_KEY)
  } catch (_error) {
    // ignore
  }
}

export function installAuthFetchInterceptor() {
  if (window.__atlasclawFetchWrapped) {
    return
  }

  const rawFetch = window.fetch.bind(window)
  window.fetch = (input, init = {}) => {
    const nextInit = { ...init }
    const headers = new Headers(nextInit.headers || (input && typeof input === 'object' && input.headers) || {})
    const token = getAuthToken()

    let url = ''
    if (typeof input === 'string') {
      url = rewriteManagedAppUrl(input)
      input = url
    } else if (input && typeof input.url === 'string') {
      // Request objects take the same managed-URL rewriting as string inputs,
      // otherwise rewrite-dependent API detection silently diverges.
      url = rewriteManagedAppUrl(input.url)
    }

    // Only same-origin API requests may carry the bearer token and
    // credentials; a URL that merely contains "/api/" (e.g. a cross-origin
    // "https://evil.com/api/collect") must never receive them.
    let isSameOriginApi = false
    try {
      const resolved = new URL(url, window.location.origin)
      isSameOriginApi = resolved.origin === window.location.origin
        && resolved.pathname.includes('/api/')
    } catch (error) {
      isSameOriginApi = false
    }
    const isLocalLogin = isSameOriginApi && url.includes('/api/auth/local/login')
    if (isSameOriginApi && !isLocalLogin && token && !headers.has(AUTH_HEADER_NAME)) {
      headers.set(AUTH_HEADER_NAME, token)
    }

    nextInit.headers = headers
    if (isSameOriginApi) {
      nextInit.credentials = nextInit.credentials || 'include'
    }
    return rawFetch(input, nextInit)
  }

  window.__atlasclawFetchWrapped = true
}

export async function checkAuth({ redirect = true } = {}) {
  try {
    const token = getAuthToken()
    const headers = token ? { [AUTH_HEADER_NAME]: token } : {}

    const response = await fetch(buildApiUrl('/api/auth/me'), {
      method: 'GET',
      headers,
      credentials: 'include'
    })

    if (!response.ok) {
      if (response.status === 401) {
        clearAuthToken()
      }
      if (redirect) {
        redirectToLogin()
      }
      return null
    }

    return await response.json()
  } catch (_error) {
    if (redirect) {
      redirectToLogin()
    }
    return null
  }
}

export async function login(username, password) {
  const response = await fetch(buildApiUrl('/api/auth/local/login'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    credentials: 'include',
    body: JSON.stringify({ username, password })
  })

  const data = await response.json().catch(() => ({}))
  if (!response.ok || data.success === false) {
    throw new Error(data.detail || data.error || 'Login failed, please check your username and password')

  }

  const token = data.token || data.access_token || ''
  if (token) {
    setAuthToken(token)
  }
  clearSession()

  return data
}

export async function logout({ redirect = true } = {}) {
  try {
    await fetch(buildApiUrl('/api/auth/logout?redirect=false'), {
      method: 'GET',
      credentials: 'include'
    })
  } finally {
    clearAuthToken()
    clearSession()
    if (redirect) {
      window.location.href = buildAppUrl('/login.html')
    }
  }
}
