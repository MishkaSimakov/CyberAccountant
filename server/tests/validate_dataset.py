"""Run the real input collection and check character geometry and Markdown offsets."""
import hashlib
import json
from pathlib import Path
from server.app.ocr import convert, parser

ROOT = Path(__file__).resolve().parents[2]


def main():
    source_dir = ROOT / 'данные'
    output_dir = ROOT / 'output/all'
    output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in source_dir.iterdir() if p.is_file()) + list(ROOT.glob('*.docx'))
    summary = []
    for source in files:
        output = output_dir / (source.name + '.md')
        args = parser().parse_args([str(source), str(output)])
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        try:
            data = convert(source, output, args)
            markdown = output.read_text(encoding='utf-8')
            assert original_hash == hashlib.sha256(source.read_bytes()).hexdigest(), 'source modified'
            assert data['schema_version'] == '2.0' and 'tokens' not in data
            assert len({c['id'] for c in data['characters']}) == len(data['characters'])
            assert sum(page['character_count'] for page in data['pages']) == len(data['characters'])
            for char in data['characters']:
                assert len(char['text']) == 1
                if char['markdown_span'] is not None:
                    start, end = char['markdown_span']
                    assert markdown[start:end] in (char['text'], '\\' + char['text']), char['id']
                assert 'bbox' not in char
                x0, y0, x1, y1 = char['bbox_normalized']
                assert 0 <= x0 < x1 <= 1, char['id']
                assert 0 <= y0 < y1 <= 1, char['id']
            # Check key real scan fields without loading the reference workbook.
            if source.name.startswith('01_'):
                compact = markdown.replace(' ', '').replace('<br>', '')
                for value in ['5558474,83', '1671359,96', '1145798,10', '3204744,51', '11580377,40', '93237,27']:
                    assert value in compact, 'missing report amount ' + value
                header = next(line for line in markdown.splitlines() if line.startswith('| '))
                header_cells = [cell.strip() for cell in header.split('|')[1:-1]]
                assert header_cells[0] == 'Этап' and header_cells[-1] == 'Итого'
                assert markdown.count('Главный бухгалтер') == 1
                assert markdown.count('Руководитель проекта') == 1
            if source.name.startswith('12_'):
                assert 'Получатель:' in markdown
                assert '650000,00' in markdown.replace(' ', '')
            summary.append({'file': source.name, 'pages': len(data['pages']),
                            'characters': len(data['characters']), 'warnings': len(data['warnings']), 'ok': True})
            print('OK', source.name, len(data['characters']), flush=True)
        except Exception as exc:
            summary.append({'file': source.name, 'ok': False, 'error': str(exc)})
            print('FAILED', source.name, str(exc), flush=True)
    (ROOT / 'output/validation.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    passed = sum(item['ok'] for item in summary)
    print(f'{passed}/{len(summary)} passed')
    return int(passed != len(summary))


if __name__ == '__main__':
    raise SystemExit(main())
