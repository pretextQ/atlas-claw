/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * WP-19 regression tests: locale files must not declare duplicate keys.
 *
 * JSON.parse silently keeps the last duplicate, so a repeated key means the
 * rendered copy stops matching the first (intended) string with no error
 * anywhere. These tests scan each locale for repeats within one object.
 */

const fs = require('fs')
const path = require('path')

const LOCALES = ['en-US.json', 'zh-CN.json']

/**
 * Return the keys declared more than once inside the same JSON object.
 * @param {string} raw - Raw JSON text.
 * @returns {string[]} Duplicated key names.
 */
function findDuplicateKeys(raw) {
  const duplicates = []
  const stack = []
  let i = 0
  while (i < raw.length) {
    const ch = raw[i]
    if (ch === '{') {
      stack.push(new Set())
      i += 1
    } else if (ch === '}') {
      stack.pop()
      i += 1
    } else if (ch === '"') {
      const start = i
      i += 1
      while (i < raw.length && (raw[i] !== '"' || raw[i - 1] === '\\')) i += 1
      const token = raw.slice(start + 1, i)
      i += 1
      let j = i
      while (j < raw.length && /\s/.test(raw[j])) j += 1
      if (raw[j] === ':' && stack.length) {
        const current = stack[stack.length - 1]
        if (current.has(token)) {
          duplicates.push(token)
        } else {
          current.add(token)
        }
      }
    } else {
      i += 1
    }
  }
  return duplicates
}

function readLocale(file) {
  return fs.readFileSync(
    path.resolve(__dirname, '../../app/frontend/locales', file), 'utf-8')
}

describe('locale files', () => {
  test.each(LOCALES)('%s declares no duplicate keys', (file) => {
    expect(findDuplicateKeys(readLocale(file))).toEqual([])
  })

  test.each(LOCALES)('%s keeps the model section copy', (file) => {
    const data = JSON.parse(readLocale(file))
    expect(typeof data.model.subtitle).toBe('string')
    expect(data.model.subtitle.length).toBeGreaterThan(0)
    expect(data.model.saveConfig).toBeTruthy()
    expect(data.model.deployModel).toBeTruthy()
  })

  test('the duplicate detector reports a repeat it is given', () => {
    // Guard against the scanner silently degrading into a no-op.
    expect(findDuplicateKeys('{"a": 1, "a": 2}')).toEqual(['a'])
    expect(findDuplicateKeys('{"a": {"b": 1}, "b": 2}')).toEqual([])
  })
})
