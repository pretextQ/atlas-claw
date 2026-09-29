/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * WP-21 regression tests: CSS accessibility and layout contract.
 *
 * Covers: no dead deep-chat descendant selectors (F-0083), capped modals
 * scroll their body (F-0084), the narrow breakpoint keeps the table grid
 * (F-0085), every removed outline has a focus-visible replacement (F-0086),
 * and role-management toggle styles are scoped (F-0087/F-0088).
 */

const fs = require('fs')
const path = require('path')

const read = (rel) => fs.readFileSync(path.resolve(__dirname, '../..', rel), 'utf-8')

describe('deep-chat styling (F-0083)', () => {
  test('no descendant selectors target the shadow DOM', () => {
    const src = read('app/frontend/static/style.css')
    expect(src).not.toMatch(/^deep-chat\s+(table|th|td|tr|pre|code)\b/m)
    // The exposed part hook is the supported styling surface.
    expect(src).toContain('deep-chat::part(')
  })
})

describe('modal scrolling (F-0084)', () => {
  test('both account modals scroll their body', () => {
    const src = read('app/frontend/styles/account-settings.css')
    expect(src).toContain('.account-provider-token-modal .modal-body')
    expect(src).toContain('.account-password-modal .modal-body')
    expect(src).toMatch(/\.account-provider-token-modal \.modal-body,[\s\S]{0,200}overflow-y:\s*auto/)
  })
})

describe('channels table breakpoint (F-0085)', () => {
  test('the narrow breakpoint keeps the grid instead of display:block', () => {
    const src = read('app/frontend/styles/channels.css')
    const narrow = src.slice(src.lastIndexOf('@media (max-width: 768px)'))
    expect(narrow).not.toMatch(/\.ch-table\s*{[^}]*display:\s*block/)
    expect(narrow).toMatch(/\.ch-table\s*{[^}]*grid-template-columns/)
  })
})

describe('focus visibility (F-0086)', () => {
  test('inputs that drop the outline have a focus-visible ring', () => {
    const admin = read('app/frontend/styles/admin-users.css')
    expect(admin).toContain('.search-input:focus-visible')
    expect(admin).toContain('.user-filter-pill select:focus-visible')

    const main = read('app/frontend/styles/main.css')
    expect(main).toContain('.session-search-input:focus-visible')
    expect(main).toContain('.session-delete-btn:focus-visible')
    // The delete button must not rely on colour alone.
    const deleteRule = main.slice(main.indexOf('.session-delete-btn:focus-visible'))
    expect(deleteRule).toContain('box-shadow')
  })
})

describe('role-management toggle scoping (F-0087/F-0088)', () => {
  test('toggle styles are scoped to the page root', () => {
    const src = read('app/frontend/styles/role-management.css')
    expect(src).not.toMatch(/^\.toggle-switch\b/m)
    expect(src).toContain('.role-management-page .toggle-switch')
  })

  test('the hidden checkbox exposes keyboard focus on its sibling', () => {
    const src = read('app/frontend/styles/role-management.css')
    expect(src).toMatch(/\.role-management-page \.toggle-switch input:focus-visible \+ span/)
  })
})
