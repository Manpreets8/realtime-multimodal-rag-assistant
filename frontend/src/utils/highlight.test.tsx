import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { highlightRanges, normalizeRanges } from './highlight'

describe('normalizeRanges', () => {
  it('sorts, clips and merges overlapping or touching ranges', () => {
    expect(
      normalizeRanges(
        [
          [10, 14],
          [0, 3],
          [2, 5],
          [5, 6],
          [-4, 1],
          [50, 90],
          [8, 8],
        ],
        60,
      ),
    ).toEqual([
      [0, 6],
      [10, 14],
      [50, 60],
    ])
  })
})

describe('highlightRanges', () => {
  it('wraps exactly the given ranges in <mark> and keeps all text', () => {
    const { container } = render(<p>{highlightRanges('18 days of annual leave per year', [[0, 7], [18, 23]])}</p>)

    expect([...container.querySelectorAll('mark')].map((m) => m.textContent)).toEqual(['18 days', 'leave'])
    expect(container.textContent).toBe('18 days of annual leave per year')
  })

  it('renders text containing markup as text, never HTML', () => {
    const { container } = render(<p>{highlightRanges('<img src=x onerror=alert(1)>', [[0, 4]])}</p>)

    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toBe('<img src=x onerror=alert(1)>')
  })
})
