import assert from 'node:assert/strict'
import {test} from 'node:test'
import {Marked} from 'marked'
import {sourceRanges, textOffsets} from '../src/components/viewers/entitySource.ts'

function leafRanges(content) {
    const tokens = new Marked().lexer(content)
    return [...sourceRanges(content, tokens)].filter(([token]) => token.type === 'text' && !token.tokens)
}

test('repeated text in table cells maps to separate source occurrences', () => {
    const content = '# 12\n\n| Amount | Amount |\n| --- | --- |\n| 12 | 12 |\n\n12'
    const amounts = leafRanges(content).filter(([token]) => token.text === '12')
    const expected = [...content.matchAll(/12/g)].map(match => match.index)
    assert.deepEqual(amounts.map(([, range]) => range.start), expected)
    for (const [token, range] of amounts) {
        assert.equal(content.slice(range.start, range.end), token.raw)
    }
})

test('links, formatting, code and definitions do not shift later selections', () => {
    const content = '[12](https://example.com/12) **12** `12`\n\n[key]: https://example.com/12\n\n12'
    const amounts = leafRanges(content).filter(([token]) => token.text === '12')
    assert.deepEqual(amounts.map(([, range]) => range.start), [1, content.indexOf('**12**') + 2, content.lastIndexOf('12')])
})

test('unmapped transformed Markdown cannot map later text to an earlier occurrence', () => {
    const content = '> first\n> second\n\nsecond'
    const ranges = leafRanges(content)
    assert.equal(ranges.at(-1)[1].start, content.lastIndexOf('second'))
})

test('custom entity tokens retain their complete source range', () => {
    const raw = '<entity type="number" value="12" unit="kg" id="1">12</entity>'
    const content = `12 ${raw} 12`
    const entity = {type: 'entity', raw, text: '12'}
    const tokens = [{
        type: 'paragraph', raw: content, tokens: [
            {type: 'text', raw: '12 ', text: '12 '}, entity, {type: 'text', raw: ' 12', text: ' 12'},
        ]
    }]
    const range = sourceRanges(content, tokens).get(entity)
    assert.deepEqual(range, {start: 3, end: 3 + raw.length})
    assert.equal(content.slice(range.start, range.end), raw)
})

test('encoded characters map selection offsets back to Markdown', () => {
    const raw = 'A &amp; &#8364; Z'
    const segment = textOffsets(raw, text => text === '&amp;' ? '&' : '€')
    assert.equal(segment.text, 'A & € Z')
    const start = segment.text.indexOf('€')
    assert.equal(raw.slice(segment.offsets[start], segment.offsets[start + 1]), '&#8364;')
    assert.equal(raw.slice(segment.offsets[2], segment.offsets[3]), '&amp;')
})

test('surrogate pairs preserve following selection offsets', () => {
    const raw = '😀 12'
    const segment = textOffsets(raw, text => text)
    assert.equal(raw.slice(segment.offsets[3], segment.offsets[5]), '12')
})
