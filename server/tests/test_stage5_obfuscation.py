"""Tests for stage-5 value obfuscation (cfg.obfuscate_types)."""

from __future__ import annotations

from pathlib import Path

from checkier.config import PipelineConfig
from checkier.entities import EntityRegistry
from checkier.stage5_relations import HIDDEN, _obfuscate_docs, _registry_lines


def _cfg(id_style: str = "mnemonic", obfuscate: tuple = ()) -> PipelineConfig:
    return PipelineConfig(
        data_dir=Path("data"),
        out_dir=Path("out"),
        id_style=id_style,
        obfuscate_types=obfuscate,
    )


def _registry() -> EntityRegistry:
    """Two data entities (amount, contract) + one card amount."""
    reg = EntityRegistry(seed=0)
    reg.add(
        type_="amount", raw="1,117,000.00", unit="RUB", norm="1",
        file="f1", role="data", span=(0, 0),
    )
    reg.add(
        type_="contract", raw="24/03/15-01", unit="", norm="1",
        file="f1", role="data", span=(0, 0),
    )
    reg.add(
        type_="amount", raw="5 558 474,83", unit="RUB", norm="1",
        file="f2", role="card", span=(0, 0),
    )
    return reg


DOCS = {
    "f1": (
        "| Проект: <e id=\"{c}\">24/03/15-01</e> |\n"
        "| <e id=\"{a}\">1,117,000.00</e> | total |\n"
    ),
    "f2": "| <e id=\"{card}\">5 558 474,83</e> |\n",
}


def _docs(reg: EntityRegistry, id_style: str) -> dict[str, str]:
    a, c, card = reg.entities
    if id_style == "mnemonic":
        ids = {"a": a.mnemonic, "c": c.mnemonic, "card": card.mnemonic}
    else:
        ids = {"a": str(a.num), "c": str(c.num), "card": str(card.num)}
    return {h8: text.format(**ids) for h8, text in DOCS.items()}


def test_registry_lines_mask_hidden_types_mnemonic():
    reg = _registry()
    cfg = _cfg(obfuscate=("amount",))
    lines = _registry_lines(cfg, reg, in_scope={"f1"})
    for line in lines.splitlines():
        eid = line.split("\t")[0]
        ent = next(e for e in reg.entities if e.mnemonic == eid)
        if ent.type == "amount":
            assert f"value={HIDDEN}" in line
        else:
            assert f"value={HIDDEN}" not in line
            assert f"value={ent.value}" in line


def test_registry_lines_mask_hidden_types_numeric():
    reg = _registry()
    cfg = _cfg(id_style="numeric", obfuscate=("amount",))
    lines = _registry_lines(cfg, reg, in_scope={"f1"})
    amount_lines = [l for l in lines.splitlines() if "type=amount" in l]
    assert len(amount_lines) == 2  # data amount + card amount
    assert all(f"value={HIDDEN}" in l for l in amount_lines)
    assert f"#{reg.entities[1].num}" in lines  # contract line present with #num


def test_registry_lines_no_obfuscation_unchanged():
    reg = _registry()
    cfg = _cfg(obfuscate=())
    lines = _registry_lines(cfg, reg, in_scope={"f1"})
    assert HIDDEN not in lines
    assert f"value={reg.entities[0].value}" in lines


def test_obfuscate_docs_replaces_only_hidden_ids():
    reg = _registry()
    cfg = _cfg(obfuscate=("amount",))
    docs = _docs(reg, "mnemonic")
    out = _obfuscate_docs(cfg, docs, reg)
    a, c, card = (e.mnemonic for e in reg.entities)
    # amount entities masked (data + card), contract intact
    assert f'<e id="{a}">{HIDDEN}</e>' in out["f1"]
    assert f'<e id="{card}">{HIDDEN}</e>' in out["f2"]
    assert f'<e id="{c}">24/03/15-01</e>' in out["f1"]
    # surrounding markdown untouched
    assert "| total |" in out["f1"]


def test_obfuscate_docs_numeric_id_style():
    reg = _registry()
    cfg = _cfg(id_style="numeric", obfuscate=("amount",))
    docs = _docs(reg, "numeric")
    out = _obfuscate_docs(cfg, docs, reg)
    a, c, card = (str(e.num) for e in reg.entities)
    assert f'<e id="{a}">{HIDDEN}</e>' in out["f1"]
    assert f'<e id="{card}">{HIDDEN}</e>' in out["f2"]
    assert f'<e id="{c}">24/03/15-01</e>' in out["f1"]


def test_obfuscate_docs_empty_types_returns_input_unchanged():
    reg = _registry()
    cfg = _cfg(obfuscate=())
    docs = _docs(reg, "mnemonic")
    assert _obfuscate_docs(cfg, docs, reg) is docs


def test_obfuscate_docs_handles_adjacent_tags_and_multiline():
    reg = _registry()
    cfg = _cfg(obfuscate=("amount",))
    a, _, card = (e.mnemonic for e in reg.entities)
    docs = {"f9": f'<e id="{a}">100</e><e id="{card}">200\nmore</e>tail'}
    out = _obfuscate_docs(cfg, docs, reg)
    assert out["f9"] == f'<e id="{a}">{HIDDEN}</e><e id="{card}">{HIDDEN}</e>tail'
