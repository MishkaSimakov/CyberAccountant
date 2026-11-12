import type {Token} from 'marked'

export interface SourceRange {
    start: number
    end: number
}

// Bound every search to its parent token so repeated text maps to the right occurrence.
// Tokens whose Markdown source was transformed by the lexer are left unmapped.
export function sourceRanges(content: string, tokens: Token[]) {
    const ranges = new Map<Token, SourceRange>()

    function visit(items: Token[], start: number, end: number): number {
        let cursor = start
        for (const token of items) {
            const offset = content.indexOf(token.raw, cursor)
            if (offset < 0 || offset + token.raw.length > end) return end
            const limit = offset + token.raw.length
            ranges.set(token, {start: offset, end: limit})
            if (token.type === 'table') {
                let cellCursor = offset
                for (const cell of [...token.header, ...token.rows.flat()]) {
                    cellCursor = visit(cell.tokens, cellCursor, limit)
                }
            } else if (token.type === 'list') {
                visit(token.items, offset, limit)
            } else if ('tokens' in token && token.tokens) {
                visit(token.tokens, offset, limit)
            }
            cursor = limit
        }
        return cursor
    }

    visit(tokens, 0, content.length)
    return ranges
}

// Character references occupy several source characters but one rendered character.
export function textOffsets(raw: string, decode: (text: string) => string) {
    const offsets = [0]
    let text = ''
    for (let i = 0; i < raw.length;) {
        const reference = raw[i] === '&' ? /^&(?:#\d+|#x[\da-f]+|[a-z][\da-z]*);/i.exec(raw.slice(i))?.[0] : undefined
        const value = reference ? decode(reference) : raw[i]!
        const length = reference?.length ?? 1
        for (let j = 0; j < value.length; j++) offsets.push(i + (j === value.length - 1 ? length : 0))
        text += value
        i += length
    }
    return {text, offsets}
}
