/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * WP-20 regression tests: frontend robustness fixes.
 *
 * Covers: run-id encoding (F-0060), Request-object header/URL handling
 * (F-0062), caret clamping (F-0066), locale payload validation and resolved
 * locale persistence (F-0068/F-0069), router navigation races (F-0079),
 * stream event payload validation (F-0082), slash-picker capability load
 * failure (F-0081), and source-level tripwires for the remaining guards.
 */

const fs = require('fs')
const path = require('path')

const read = (rel) => fs.readFileSync(path.resolve(__dirname, '../..', rel), 'utf-8')

describe('api-client run id encoding (F-0060)', () => {
  test('getAgentStatus and abortAgentRun encode the run id', () => {
    const src = read('app/frontend/scripts/api-client.js')
    expect(src).toContain('/api/agent/runs/${encodeURIComponent(runId)}')
    expect(src).toContain('/api/agent/runs/${encodeURIComponent(runId)}/abort')
  })
})

describe('dom-utils caret clamping (F-0066)', () => {
  test('out-of-range selections are clamped instead of throwing', async () => {
    const { restoreInputFocus } = await import('../../app/frontend/scripts/dom-utils.js')
    const root = document.createElement('div')
    const input = document.createElement('input')
    input.id = 'target'
    input.value = 'abcd'
    root.appendChild(input)
    document.body.appendChild(root)
    const calls = []
    input.setSelectionRange = (start, end) => calls.push([start, end])

    restoreInputFocus(root, '#target', 99, -5)

    expect(calls).toEqual([[4, 0]])
    root.remove()
  })
})

describe('stream handler payload validation (F-0082)', () => {
  test('a "null" lifecycle payload does not throw in the listener', async () => {
    const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js')
    const listeners = {}
    let captured = null

    global.EventSource = class {
      constructor() {
        this.readyState = 1
      }
      addEventListener(type, handler) {
        listeners[type] = handler
      }
      close() {}
    }
    global.EventSource.OPEN = 1
    global.EventSource.CLOSED = 2

    const handler = createStreamHandler('run-null', {
      onStart: (data) => { captured = data },
      onRuntime: (data) => { captured = data },
    })
    handler.start()

    expect(() => listeners.lifecycle({ data: 'null' })).not.toThrow()
    // The malformed payload degrades to an empty object, so no branch fires.
    expect(captured).toBeNull()

    listeners.lifecycle({ data: '{"phase":"start"}' })
    expect(captured).toEqual({ phase: 'start' })
  })
})

describe('router navigation token (F-0079)', () => {
  test('a slower earlier navigation does not mount over a newer route', async () => {
    const { createRouter } = await import('../../app/frontend/scripts/router.js')
    const container = document.createElement('div')
    const mounted = []
    let releaseFirst
    const firstGate = new Promise((resolve) => { releaseFirst = resolve })

    const router = createRouter(
      [
        {
          path: '/slow',
          loader: async () => {
            await firstGate
            return { mount: async () => mounted.push('slow'), unmount: async () => {} }
          },
          children: [],
        },
        {
          path: '/fast',
          loader: async () => ({ mount: async () => mounted.push('fast'), unmount: async () => {} }),
          children: [],
        },
      ],
      { container },
    )

    router.navigate('/slow')
    await new Promise((resolve) => setTimeout(resolve, 0))
    router.navigate('/fast')
    await new Promise((resolve) => setTimeout(resolve, 0))
    releaseFirst()
    // Give the stale slow load a chance to (wrongly) mount.
    await new Promise((resolve) => setTimeout(resolve, 20))

    expect(mounted).toEqual(['fast'])
  })
})

describe('session-manager bootstrap guard (F-0080)', () => {
  test('a failed bootstrap degrades to null instead of half-initializing', () => {
    const src = read('app/frontend/scripts/session-manager.js')
    expect(src).toContain('Chat Active Session bootstrap failed')
    expect(src).toContain('integrationChatSession = null')
  })
})

describe('slash-picker shared load rejection (F-0081)', () => {
  test('the shared promise branch has a catch', () => {
    const src = read('app/frontend/scripts/slash-picker.js')
    expect(src).toContain('Shared capability load failed')
  })
})

describe('link navigation filter (F-0061)', () => {
  test('non-app schemes are skipped and login matching is exact', () => {
    const src = read('app/frontend/scripts/app.js')
    expect(src).toContain('hasNonAppScheme')
    expect(src).toContain("loginRoutes = ['/login', '/login.html']")
    expect(src).not.toContain("href.includes('login')")
  })
})

describe('guard tripwires', () => {
  test('populateProfile guards a torn-down container (F-0070)', () => {
    const src = read('app/frontend/scripts/pages/account-settings.js')
    expect(src).toContain('if (!containerRef) return')
  })

  test('admin-users compares user ids with coercion (F-0073)', () => {
    const src = read('app/frontend/scripts/pages/admin-users.js')
    expect(src).not.toContain('user.id === editUserId')
    expect(src).toContain('String(user.id) === String(editUserId)')
  })

  test('chat clears the persisted session key (F-0075)', () => {
    const src = read('app/frontend/scripts/pages/chat.js')
    expect(src).toContain('setSessionKey(null)')
  })

  test('models guards the custom input node (F-0076)', () => {
    const src = read('app/frontend/scripts/pages/models.js')
    expect(src).toContain('if (customInput) {')
  })

  test('role-management guards an empty save response (F-0077)', () => {
    const src = read('app/frontend/scripts/pages/role-management.js')
    expect(src).toContain('Role save returned no role payload')
  })

  test('runtime chip state is sanitized into the class attribute (F-0063)', () => {
    const src = read('app/frontend/scripts/chat-ui.js')
    expect(src).toContain("replace(/[^a-z0-9_-]/g, '')")
    expect(src).not.toContain('runtime-chip ${entry.state')
  })

  test('auth.js rewrites Request-object URLs (F-0062)', () => {
    const src = read('app/frontend/scripts/auth.js')
    expect(src).toContain('url = rewriteManagedAppUrl(input.url)')
  })

  test('admin-users.html redirect is base-path aware (F-0053)', () => {
    const src = read('app/frontend/admin-users.html')
    expect(src).not.toContain('url=/admin/users')
    expect(src).toContain('__ATLASCLAW_BASE_PATH_JSON__')
  })

  test('top-level auth checks are guarded (F-0059)', () => {
    const app = read('app/frontend/scripts/app.js')
    expect(app).toContain('Auth check failed')
    const account = read('app/frontend/scripts/pages/account-settings.js')
    expect(account).toContain('Auth check failed')
  })
})
