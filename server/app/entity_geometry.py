"""Map one exact tagged occurrence to the preserved OCR character geometry."""
from difflib import SequenceMatcher
from bisect import bisect_left
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Literal, TypedDict

from checkier.entities import ENT_RE, parse_amount, parse_tag_attrs


class EntityNotFound(ValueError):
    pass


class EntityBox(TypedDict):
    page: int
    bbox_normalized: list[float]


class EntityLocation(TypedDict):
    entity_id: int
    text: str
    status: Literal['mapped', 'partial', 'unmapped']
    boxes: list[EntityBox]
    message: str | None


def entity_source(tagged: str, entity_id: int):
    parts, matches, cursor, length = [], [], 0, 0
    for match in ENT_RE.finditer(tagged):
        prefix, body = tagged[cursor:match.start()], match[2]
        parts.extend((prefix, body))
        length += len(prefix)
        if parse_tag_attrs(match[1]).get('id') == str(entity_id):
            matches.append((length, length + len(body), body))
        length += len(body)
        cursor = match.end()
    parts.append(tagged[cursor:])
    if len(matches) != 1:
        raise EntityNotFound('Сущность не найдена в этой версии документа.')
    return ''.join(parts), matches[0]


def source_span(tagged_plain: str, original: str, start: int, end: int):
    if tagged_plain.rstrip('\n') == original.rstrip('\n'):
        return start, end
    # Edits elsewhere can preserve a complete, uniquely anchored run. Never search
    # for the first occurrence of a sum: repeated values must retain their context.
    if len(tagged_plain) * len(original) > 50_000_000:
        return None
    for block in SequenceMatcher(None, tagged_plain, original, autojunk=False).get_matching_blocks():
        if block.a <= start < end <= block.a + block.size and block.size >= max(32, end - start + 16):
            anchor = tagged_plain[block.a:block.a + block.size]
            if tagged_plain.count(anchor) == original.count(anchor) == 1:
                offset = block.b - block.a
                return start + offset, end + offset
    # Reviewed legacy Markdown can change table spacing and number typography.
    # Align the whole document with source offsets retained, then require a unique
    # contextual anchor AND equal numeric values; never pick a standalone amount.
    left, left_positions = comparable_text(tagged_plain)
    right, right_positions = comparable_text(original)
    a, b = bisect_left(left_positions, start), bisect_left(left_positions, end)
    if a == b:
        return None
    for block in SequenceMatcher(None, left, right, autojunk=False).get_matching_blocks():
        if block.a <= a < b <= block.a + block.size and block.size >= max(32, b - a + 16):
            anchor = left[block.a:block.a + block.size]
            if left.count(anchor) != 1 or right.count(anchor) != 1:
                continue
            offset = block.b - block.a
            mapped = right_positions[a + offset], right_positions[b + offset - 1] + 1
            expected, actual = parse_amount(tagged_plain[start:end]), parse_amount(original[mapped[0]:mapped[1]])
            if expected is not None and actual != expected:
                continue
            return mapped
    # OCR may damage/reorder the label while preserving a complete identifier.
    # Accept an exact value only when it is a standalone occurrence, unique in
    # BOTH documents. Short values and repeated sums still require context.
    value = tagged_plain[start:end]
    if sum(char.isalnum() for char in value) < 4:
        return None
    pattern = re.compile(r'(?<![\w.,/\-])' + re.escape(value) + r'(?![\w.,/\-])')
    tagged_matches = list(pattern.finditer(tagged_plain))
    original_matches = list(pattern.finditer(original))
    if (len(tagged_matches) == len(original_matches) == 1
            and tagged_matches[0].span() == (start, end)):
        return original_matches[0].span()
    return None


def comparable_text(text: str):
    # Ignore Markdown formatting, whitespace, and numeric separators when matching
    # reviewed text to OCR, retaining each character's source offset for span mapping.
    ignored = set()
    for match in re.finditer(r'<!--[\s\S]*?-->|<br\s*/?>|(?m:^[ \t]*#{1,6}[ \t]+)|(?m:^[ \t|:\-]+$)', text):
        ignored.update(range(match.start(), match.end()))
    positions = [i for i, char in enumerate(text)
                 if i not in ignored and not char.isspace() and char not in '|*\\'
                 and not (char in ',.' and 0 < i < len(text) - 1 and text[i - 1].isdigit() and text[i + 1].isdigit())]
    return ''.join(text[i] for i in positions), positions


def locate_entity(tagged: str, entity_id: int, pdf: Path, original_hash: str) -> EntityLocation:
    """Match a tagged entity to the preserved OCR text and character geometry.

    Return one enclosing normalized box per page, with mapped/partial status for
    complete/incomplete character coverage, or unmapped with no boxes if unavailable.
    Raise EntityNotFound when the entity ID does not identify exactly one occurrence.
    """
    plain, (start, end, text) = entity_source(tagged, entity_id)
    result: EntityLocation = {
        'entity_id': entity_id, 'text': text, 'status': 'unmapped', 'boxes': [], 'message': None,
    }

    def unavailable(message: str) -> EntityLocation:
        return {**result, 'message': message}

    # OCR copies the PDF and sidecar together; edits keep pdf_path pointing at
    # that original artifact directory even when markdown_path changes.
    if not pdf.parent.name.endswith('.assets'):
        return unavailable('Для этого документа не сохранены координаты OCR.')
    sidecar = pdf.parent.parent / (pdf.parent.name.removesuffix('.assets') + '.boxes.json')
    if not sidecar.is_file():
        return unavailable('Для этого документа не сохранены координаты OCR.')
    try:
        manifest = json.loads(sidecar.read_text(encoding='utf-8'))
        root = sidecar.parent.resolve()

        def artifact(name):
            path = (root / name.replace('\\', '/')).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Artifact outside the OCR directory')
            return path

        if (manifest['schema_version'] != '2.0' or manifest['source']['sha256'] != original_hash
                or artifact(manifest['preview_pdf']) != pdf.resolve()):
            return unavailable('Координаты не соответствуют версии исходного документа.')
        original_bytes = artifact(manifest['markdown']['path']).read_bytes()
        if hashlib.sha256(original_bytes).hexdigest() != manifest['markdown']['sha256']:
            return unavailable('Исходный OCR-текст изменён; сохранённые координаты устарели.')
        original = original_bytes.decode('utf-8')
        span = source_span(plain, original, start, end)
        if span is None:
            return unavailable('Текст сущности изменён или не удалось однозначно привязать его к PDF.')
        start, end = span
        covered: set[int] = set()
        page_boxes: dict[int, list[float]] = {}
        for char in manifest['characters']:
            positions = char.get('markdown_span')
            if positions is None or positions[0] >= end or positions[1] <= start:
                continue
            x0, y0, x1, y1 = char['bbox_normalized']
            if (not all(math.isfinite(v) for v in (x0, y0, x1, y1))
                    or not 0 <= x0 < x1 <= 1 or not 0 <= y0 < y1 <= 1
                    or not 1 <= char['page'] <= len(manifest['pages'])):
                continue
            page = char['page']
            bounds = page_boxes.get(page, [x0, y0, x1, y1])
            page_boxes[page] = [min(bounds[0], x0), min(bounds[1], y0),
                                max(bounds[2], x1), max(bounds[3], y1)]
            covered.update(range(max(start, positions[0]), min(end, positions[1])))
        if not page_boxes:
            return unavailable('Для выбранной сущности нет распознанных символов с координатами.')
        boxes: list[EntityBox] = [
            {'page': page, 'bbox_normalized': bounds} for page, bounds in page_boxes.items()
        ]
        complete = all(i in covered or original[i].isspace() for i in range(start, end))
        return {
            **result, 'boxes': boxes, 'status': 'mapped' if complete else 'partial',
            'message': None if complete else 'Подсвечена только часть символов: остальные не имеют точных координат OCR.',
        }
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, IndexError):
        return unavailable('Не удалось прочитать сохранённые координаты OCR.')
