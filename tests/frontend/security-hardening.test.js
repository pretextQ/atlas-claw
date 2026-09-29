/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * WP-03 frontend security hardening regression tests.
 *
 * Covers: bearer-token same-origin scoping (F-0006), embed host_origin trust
 * validation (F-0067), sidebar content as text (F-0064), router fail-closed
 * navigation guard (F-0078), open-redirect guards (F-0005), and source-level
 * tripwires for the escaping fixes (F-0007/F-0072/F-0074).
 */

let installAuthFetchInterceptor
let setAuthToken
let clearAuthToken
let parseEmbedSurface
let setSidebarContent

beforeAll(async () => {
  ;({ installAuthFetchInterceptor, setAuthToken, clearAuthToken } =
    await import('../../app/frontend/scripts/auth.js'))
  ;({ parseEmbedSurface } = await import('../../app/frontend/scripts/embed/surface.js'))
  ;({ setSidebarContent } = await import('../../app/frontend/scripts/components/sidebar.js'))
})

beforeEach(() => {
  jest.resetModules()
  sessionStorage.clear()
  clearAuthToken()
  window.location = new URL('http://agent.example/')
  delete window.__atlasclawFetchWrapped
})

function useRawFetch(rawFetch) {
  window.fetch = rawFetch
}

describe('auth fetch interceptor (F-0006)', () => {
  test('attaches the bearer token to same-origin API requests', async () => {
    let seenInit = null
    useRawFetch(async (input, init) => {
      seenInit = { input, init }
      return { ok: true, status: 200, json: async () => ({}) }
    })
    installAuthFetchInterceptor()
    setAuthToken('secret-token')

    await fetch('/api/sessions', { method: 'GET' })

    expect(seenInit.init.headers.get('AtlasClaw-Authenticate')).toBe('secret-token')
    expect(seenInit.init.credentials).toBe('include')
  })

  test('never attaches the token or credentials to cross-origin URLs', async () => {
    let seenInit = null
    useRawFetch(async (input, init) => {
      seenInit = { input, init }
      return { ok: true, status: 200, json: async () => ({}) }
    })
    installAuthFetchInterceptor()
    setAuthToken('secret-token')

    // The URL merely contains "/api/" — it must still not receive the token.
    await fetch('https://evil.example/api/collect', { method: 'GET' })

    expect(seenInit.init.headers.get('AtlasClaw-Authenticate')).toBeNull()
    expect(seenInit.init.credentials).toBeUndefined()
  })

  test('same-origin URL with /api/ only in the query does not receive the token', async () => {
    let seenInit = null
    useRawFetch(async (input, init) => {
      seenInit = { input, init }
      return { ok: true, status: 200, json: async () => ({}) }
    })
    installAuthFetchInterceptor()
    setAuthToken('secret-token')

    await fetch('/page?next=/api/whatever', { method: 'GET' })

    expect(seenInit.init.headers.get('AtlasClaw-Authenticate')).toBeNull()
  })
})

describe('embed surface host_origin trust (F-0067)', () => {
  const floatingUrl = 'https://agent.example/?embedded=1&surface=floating&host_origin=https%3A%2F%2Fhost.example&nonce=abcdefghijklmnopqrstuvwxyz123456'

  test('rejects a well-formed host_origin with no trusted confirmation', () => {
    const surface = parseEmbedSurface(floatingUrl, {})
    expect(surface.integrationMode).toBe(true)
    expect(surface.hostOrigin).toBeNull()
  })

  test('accepts host_origin confirmed by document.referrer', () => {
    const surface = parseEmbedSurface(floatingUrl, {
      document: { referrer: 'https://host.example/page' },
    })
    expect(surface.hostOrigin).toBe('https://host.example')
  })

  test('accepts host_origin present in window.location.ancestorOrigins', () => {
    const surface = parseEmbedSurface(floatingUrl, {
      location: { ancestorOrigins: ['https://other.example', 'https://host.example'] },
    })
    expect(surface.hostOrigin).toBe('https://host.example')
  })

  test('accepts host_origin listed in the explicit deployment allowlist', () => {
    const surface = parseEmbedSurface(floatingUrl, {
      __ATLASCLAW_EMBED_ALLOWED_HOST_ORIGINS__: ['https://host.example'],
    })
    expect(surface.hostOrigin).toBe('https://host.example')
  })

  test('rejects an attacker-chosen origin even when another origin is trusted', () => {
    const surface = parseEmbedSurface(
      floatingUrl.replace('host.example', 'evil.example'),
      { document: { referrer: 'https://host.example/page' } },
    )
    expect(surface.hostOrigin).toBeNull()
  })
})

describe('sidebar content as text (F-0064)', () => {
  test('renders string content as text, never as HTML', () => {
    const container = document.createElement('div')
    container.id = 'sidebar-dynamic-content'
    document.body.appendChild(container)

    try {
      setSidebarContent('<img src=x onerror=window.__pwned=1>')
      const img = container.querySelector('img')
      expect(img).toBeNull()
      expect(container.textContent).toBe('<img src=x onerror=window.__pwned=1>')
    } finally {
      container.remove()
    }
  })
})

describe('router navigation guard fails closed (F-0078)', () => {
  test('onBeforeRoute throwing cancels navigation', async () => {
    const { createRouter } = await import('../../app/frontend/scripts/router.js')
    const loader = jest.fn().mockResolvedValue({ mount: jest.fn(), unmount: jest.fn() })
    const router = createRouter(
      [{ path: '/protected', loader, children: [] }],
      {
        onBeforeRoute: jest.fn(() => { throw new Error('auth backend down') }),
        onError: jest.fn(),
      },
    )

    await router.navigate('/protected')

    expect(loader).not.toHaveBeenCalled()
  })
})

describe('open-redirect guards (F-0005)', () => {
  function extractInlineScript(html, marker) {
    const scripts = html.match(/<script>([\s\S]*?)<\/script>/g) || []
    const found = scripts.find((block) => block.includes(marker))
    return found ? found.replace(/^<script>/, '').replace(/<\/script>$/, '') : null
  }

  function evalBuildUrl(html) {
    const source = extractInlineScript(html, '__atlasclawBuildUrl')
    expect(source).not.toBeNull()
    // The server substitutes a JSON literal for this token at render time;
    // stand in an empty base path for the test.
    const executable = source.replace('__ATLASCLAW_BASE_PATH_JSON__', '""')
    const sandbox = {}
    const fn = new Function('sandboxOut', `${executable}; sandboxOut.buildUrl = window.__atlasclawBuildUrl;`)
    fn(sandbox)
    return sandbox.buildUrl
  }

  test('index.html buildUrl keeps backslash paths local', async () => {
    const fs = await import('fs')
    const path = await import('path')
    const html = fs.readFileSync(
      path.resolve(__dirname, '../../app/frontend/index.html'), 'utf-8')

    window.__atlasclawBasePath = ''
    const buildUrl = evalBuildUrl(html)
    // Browsers normalize "/\evil.com" to "//evil.com"; the builder must not.
    expect(buildUrl('/\\evil.com')).toBe('/evil.com')
    expect(buildUrl('/\\evil.com').startsWith('//')).toBe(false)
    expect(buildUrl('/models')).toBe('/models')
  })

  test('login.html normalizeRedirectTarget sends backslash targets to the fallback', async () => {
    const fs = await import('fs')
    const path = await import('path')
    const html = fs.readFileSync(
      path.resolve(__dirname, '../../app/frontend/login.html'), 'utf-8')

    const match = html.match(/function normalizeRedirectTarget\(\S[\s\S]*?\n    \}/)
    expect(match).not.toBeNull()
    const fallback = '/app-fallback'
    const stubWindow = {
      __atlasclawBasePath: '',
      __atlasclawBuildUrl: (p) => p,
    }
    const fn = new Function(
      'window', 'fallbackRedirect',
      `${match[0]}; return normalizeRedirectTarget;`,
    )
    const normalizeRedirectTarget = fn(stubWindow, fallback)

    expect(normalizeRedirectTarget('/\\evil.com')).toBe(fallback)
    expect(normalizeRedirectTarget('//evil.com')).toBe(fallback)
    expect(normalizeRedirectTarget('https://evil.com')).toBe(fallback)
    expect(normalizeRedirectTarget('/models')).toBe('/models')
  })
})

describe('escaping tripwires (F-0007/F-0072/F-0074/F-0008)', () => {
  const fs = require('fs')
  const path = require('path')
  const read = (rel) => fs.readFileSync(path.resolve(__dirname, '../..', rel), 'utf-8')

  test('models.js escapes model names interpolated into option markup', () => {
    const src = read('app/frontend/scripts/pages/models.js')
    expect(src).toContain('html += `<option value="${escapeHtml(m)}"')
  })

  test('admin-users.js no longer embeds raw JSON.stringify into data-user', () => {
    const src = read('app/frontend/scripts/pages/admin-users.js')
    expect(src).not.toContain("JSON.stringify(user).replace(/'/g")
    expect(src).toContain('data-user="${escapeHtml(JSON.stringify(user))}"')
  })

  test('admin-users.js role assignment fails closed on unknown roles', () => {
    const src = read('app/frontend/scripts/pages/admin-users.js')
    expect(src).not.toContain('if (!role) return true')
    expect(src).toContain('if (!role) return false')
  })

  test('role-management.js escapeHtml escapes quotes', () => {
    const src = read('app/frontend/scripts/pages/role-management.js')
    expect(src).toContain("String(str ?? '').replace(/[&<>\"']/g")
  })

  test('channels.js escapes schema/config values interpolated into attributes', () => {
    const src = read('app/frontend/scripts/pages/channels.js')
    expect(src).not.toContain('value="${value}"')
    expect(src).toContain('value="${escapeHtml(value)}"')
    expect(src).toContain('data-conn-id="${escapeHtml(conn.id)}"')
    expect(src).toContain('<option value="${escapeHtml(value)}" ${selected}>${escapeHtml(label)}</option>')
  })
})
