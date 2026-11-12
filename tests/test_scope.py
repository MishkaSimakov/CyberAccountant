"""Tests for target-row detection (stage 4)."""

from cyberaccountant.config import PipelineConfig
from cyberaccountant.entities import EntityRegistry
from cyberaccountant.stage4_scope import find_target_row_keys


def _mk(tmp_path, role_h8: dict[str, str], texts: dict[str, str]):
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path)
    cfg.tagged_dir.mkdir(parents=True, exist_ok=True)
    mapping = {}
    for h8, role in role_h8.items():
        (cfg.tagged_dir / f"{h8}.md").write_text(texts[h8], encoding="utf-8")
        mapping[h8] = {"role": role, "original": f"{role}/{h8}.md", "sha256": "x"}
    return cfg, mapping


def _add_span(reg, tagged, ent_repr, raw, type_, role, h8):
    """Add an entity whose span covers the whole <ent...>raw</ent> tag."""
    start = tagged.index(ent_repr)
    end = start + len(ent_repr)
    return reg.add(type_=type_, raw=raw, unit="RUB" if type_ == "amount" else "",
                   norm="1", file=h8, role=role, span=(start, end))


def test_target_row_found_by_stage_and_year(tmp_path):
    card = (
        "# КФЗ\n"
        "| Показатель | 2025 | 2026 |\n"
        "| 2 этап | <ent t=\"amount\" u=\"RUB\">10,00</ent> | <ent t=\"amount\" u=\"RUB\">20,00</ent> |\n"
        "| <ent t=\"number\">3</ent> этап | <ent t=\"amount\" u=\"RUB\">30,00</ent> | "
        "<ent t=\"amount\" u=\"RUB\">400,00</ent> |\n"
    )
    req = "# README\nПроверяемая строка отчета: «3 этап, 2026 г.».\n"
    cfg, mapping = _mk(tmp_path,
                       {"card1": "card", "req1": "request"},
                       {"card1": card, "req1": req})
    reg = EntityRegistry(0)
    two = _add_span(reg, card, '<ent t="amount" u="RUB">10,00</ent>', "10,00",
                    "amount", "card", "card1")
    three = _add_span(reg, card, '<ent t="number">3</ent>', "3", "number", "card", "card1")
    four = _add_span(reg, card, '<ent t="amount" u="RUB">400,00</ent>', "400,00",
                     "amount", "card", "card1")

    keys, label = find_target_row_keys(cfg, reg, mapping)
    assert label == "3 этап, 2026 г."
    assert three.mnemonic in keys
    assert four.mnemonic in keys   # same row as "3 этап"
    assert two.mnemonic not in keys  # other row


def test_target_row_missing_falls_back_to_none(tmp_path):
    card = "# КФЗ\n| 1 этап | <ent t=\"amount\" u=\"RUB\">5,00</ent> |\n"
    req = "# README\nПроверяемая строка отчета: «7 этап, 2026 г.».\n"
    cfg, mapping = _mk(tmp_path,
                       {"card1": "card", "req1": "request"},
                       {"card1": card, "req1": req})
    reg = EntityRegistry(0)
    _add_span(reg, card, '<ent t="amount" u="RUB">5,00</ent>', "5,00",
              "amount", "card", "card1")
    keys, label = find_target_row_keys(cfg, reg, mapping)
    assert keys is None   # row not found -> caller keeps all card cells as variables
    assert label == "7 этап, 2026 г."


def test_label_prefers_row_spec_over_filename_quote(tmp_path):
    """A «...этап_3.pdf» filename quote must not win over «3 этап, 2026 г.»."""
    card = "# КФЗ\n| <ent t=\"number\">3</ent> этап | <ent t=\"amount\" u=\"RUB\">1,00</ent> |\n"
    req = ("# README\nФайл: «01_Карточка_учета_фактических_затрат_этап_3.pdf».\n"
           "Проверяемая строка отчета: «3 этап, 2026 г.».\n")
    cfg, mapping = _mk(tmp_path,
                       {"card1": "card", "req1": "request"},
                       {"card1": card, "req1": req})
    reg = EntityRegistry(0)
    _add_span(reg, card, '<ent t="number">3</ent>', "3", "number", "card", "card1")
    _add_span(reg, card, '<ent t="amount" u="RUB">1,00</ent>', "1,00",
              "amount", "card", "card1")
    keys, label = find_target_row_keys(cfg, reg, mapping)
    assert label == "3 этап, 2026 г."
    assert keys is not None and len(keys) == 2
