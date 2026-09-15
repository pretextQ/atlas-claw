/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * sidebar.js component tests
 */

describe('sidebar.js', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="sidebar"></div>'
    window.history.replaceState({}, '', '/')
    jest.resetModules()
  })

  test('renderSidebar keeps back link hidden on chat home', async () => {
    const { renderSidebar } = await import('../../app/frontend/scripts/components/sidebar.js')

    const container = document.getElementById('sidebar')
    renderSidebar(container)

    const backLink = container.querySelector('[data-sidebar-back]')
    const newChatLink = container.querySelector('[data-new-chat]')
    expect(backLink).not.toBeNull()
    expect(backLink.classList.contains('sidebar-back-link-hidden')).toBe(true)
    expect(backLink.getAttribute('href')).toBe('/')
    expect(newChatLink).not.toBeNull()
    expect(newChatLink.classList.contains('sidebar-new-chat-hidden')).toBe(false)
  })

  test('renderSidebar shows back link above new chat on non-chat pages and toggles by route', async () => {
    window.history.replaceState({}, '', '/admin/users')

    const { renderSidebar, updateSidebarActive } = await import('../../app/frontend/scripts/components/sidebar.js')

    const container = document.getElementById('sidebar')
    renderSidebar(container)

    const header = container.querySelector('.sidebar-header')
    const backLink = header.querySelector('[data-sidebar-back]')
    const newChatLink = header.querySelector('[data-new-chat]')

    expect(header.firstElementChild).toBe(backLink)
    expect(backLink.classList.contains('sidebar-primary-action')).toBe(true)
    expect(newChatLink.classList.contains('sidebar-primary-action')).toBe(true)
    expect(backLink.classList.contains('sidebar-back-link-hidden')).toBe(false)
    expect(newChatLink.classList.contains('sidebar-new-chat-hidden')).toBe(true)
    expect(backLink.getAttribute('href')).toBe('/')
    expect(backLink.textContent).toContain('Back to Chat')
    expect(newChatLink).not.toBeNull()

    updateSidebarActive('/')

    expect(backLink.classList.contains('sidebar-back-link-hidden')).toBe(true)
    expect(newChatLink.classList.contains('sidebar-new-chat-hidden')).toBe(false)
  })
})
