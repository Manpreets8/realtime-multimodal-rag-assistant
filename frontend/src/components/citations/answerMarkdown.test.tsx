import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { parseBlocks, parseInline, renderAnswer, type CitedSpan } from './answerMarkdown'

function show(text: string, spans: CitedSpan[] = []) {
  return render(<div data-testid="answer">{renderAnswer(text, { spans, marker: (s) => <sup data-marker={s.key}>[{s.key}]</sup> })}</div>)
}

const span = (text: string, fragment: string, key = 'a'): CitedSpan => {
  const start = text.indexOf(fragment)
  return { key, start, end: start + fragment.length }
}

describe('parseInline', () => {
  it('keeps the raw offsets of visible characters', () => {
    const text = 'Get **18 days** of `leave`.'
    const runs = parseInline(text, 0, text.length)

    expect(runs.map((r) => [r.text, text.slice(r.start, r.end), !!r.bold, !!r.code])).toEqual([
      ['Get ', 'Get ', false, false],
      ['18 days', '18 days', true, false],
      [' of ', ' of ', false, false],
      ['leave', 'leave', false, true],
      ['.', '.', false, false],
    ])
  })

  it('leaves unmatched markers and snake_case alone', () => {
    const text = 'Use 2 * 3 and a snake_case_name or **unclosed'
    expect(parseInline(text, 0, text.length).map((r) => r.text).join('')).toBe(text)
  })
})

describe('parseBlocks', () => {
  it('recognises paragraphs, lists, headings and code', () => {
    const text = '## Leave\nFirst line\nsecond line\n\n- one\n- two\n  continued\n\n1. a\n2) b\n\n```\ncode here\n```'
    const blocks = parseBlocks(text)

    expect(blocks.map((b) => b.kind)).toEqual(['heading', 'paragraph', 'list', 'list', 'code'])
    const bullets = blocks[2] as Extract<(typeof blocks)[number], { kind: 'list' }>
    expect(bullets.ordered).toBe(false)
    expect(bullets.items.map(([s, e]) => text.slice(s, e))).toEqual(['one', 'two\n  continued'])
    expect((blocks[3] as { ordered: boolean }).ordered).toBe(true)
  })
})

describe('renderAnswer', () => {
  it('renders formatting instead of raw markdown', () => {
    show('**Annual leave**\n\n- Full-time: *20 days*\n- Part-time: pro-rated\n\nSee `HR portal`.')

    const answer = screen.getByTestId('answer')
    expect(answer).not.toHaveTextContent('**')
    expect(answer.querySelector('strong')).toHaveTextContent('Annual leave')
    expect(answer.querySelectorAll('li')).toHaveLength(2)
    expect(answer.querySelector('em')).toHaveTextContent('20 days')
    expect(answer.querySelector('code')).toHaveTextContent('HR portal')
  })

  it('puts each marker right after the span it supports, inside lists and bold text', () => {
    const text = 'Leave:\n- You get **20 days** per year.\n- Up to 5 days carry over.'
    show(text, [span(text, 'You get **20 days** per year.', '1'), span(text, 'Up to 5 days carry over.', '2')])

    const items = screen.getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('You get 20 days per year.[1]')
    expect(items[1]).toHaveTextContent('Up to 5 days carry over.[2]')
    expect(items[0].querySelector('[data-marker="1"]')?.previousSibling).toHaveTextContent('per year.')
    expect(items[0].querySelector('strong')?.closest('.decoration-dotted')).not.toBeNull() // cited text
  })

  it('handles a span that ends inside bold text or in the blank line after a paragraph', () => {
    const text = 'The cap is **$275 per night** in London.\n\nOther cities: $200.'
    const second = text.indexOf('Other')
    show(text, [
      span(text, 'The cap is **$275', 'x'),
      { key: 'y', start: text.indexOf(' in London'), end: second }, // ends in the blank line
      { key: 'z', start: second, end: text.length },
    ])

    const paragraphs = screen.getByTestId('answer').querySelectorAll('p')
    expect(paragraphs[0]).toHaveTextContent('The cap is $275[x] per night in London.[y]')
    expect(paragraphs[1]).toHaveTextContent('Other cities: $200.[z]')
  })

  it('never renders answer text as HTML', () => {
    show('<img src=x onerror="alert(1)"> **bold**')

    const answer = screen.getByTestId('answer')
    expect(answer.querySelector('img')).toBeNull()
    expect(answer).toHaveTextContent('<img src=x onerror="alert(1)"> bold')
  })
})
