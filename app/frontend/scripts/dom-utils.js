/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

export function restoreInputFocus(root, selector, selectionStart, selectionEnd) {
  const input = root?.querySelector(selector)
  if (!input) return

  try {
    input.focus({ preventScroll: true })
  } catch (_error) {
    input.focus()
  }

  if (typeof input.setSelectionRange !== 'function') return
  const fallbackPosition = input.value.length
  const start = Number.isInteger(selectionStart) ? selectionStart : fallbackPosition
  const end = Number.isInteger(selectionEnd) ? selectionEnd : start
  try {
    input.setSelectionRange(start, end)
  } catch (_error) {
    // Some input types do not expose selection ranges in every browser.
  }
}
