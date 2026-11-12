"""Quick terminal check: is OpenRouter reachable, and does tagging work on 2 files?

Run from the repo root:
    uv run python scripts/verify_tag.py
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from cyberaccountant.config import PipelineConfig, load_api_credentials
from cyberaccountant.stage1_tag import TagStats, TagProgress, run_tag


def main() -> None:
    url, key = load_api_credentials()
    print(f"API endpoint: {url}")

    # 1. connectivity
    try:
        r = requests.get(url.replace("/responses", "/models"),
                         headers={"Authorization": f"Bearer {key}"},
                         timeout=15)
        print(f"connectivity: HTTP {r.status_code}")
        if r.status_code != 200:
            print("  body:", r.text[:300])
            print("-> OpenRouter is not reachable/auth-failing. Fix network/key first.")
            return
    except Exception as e:
        print(f"connectivity FAILED: {e}")
        print("-> Network issue reaching OpenRouter. Check proxy/firewall/DNS.")
        return

    # 2. tag two real files
    cfg = PipelineConfig(data_dir=Path("data_md"), out_dir=Path("out"), tag_workers=2)
    cfg.files_dir.mkdir(parents=True, exist_ok=True)
    samples = {
        "akt118": "data_md/данные/04_Акт_118_28-01-2026.pdf.md",
        "vedmarch": "data_md/данные/02_03_Ведомость_ЗП_Март_2026.xlsx.md",
    }
    mapping = {}
    for h8, src in samples.items():
        text = Path(src).read_text(encoding="utf-8")
        (cfg.files_dir / f"{h8}.md").write_text(text, encoding="utf-8")
        mapping[h8] = {"original": src, "role": "data", "sha256": "x", "duplicate_of": None}

    def show(prog: TagProgress, stats: TagStats) -> None:
        if prog.ok:
            print(f"\n  [{stats.done}/{stats.total}] OK {prog.file} "
                  f"entities={prog.entities} retries={prog.retries}")
        else:
            print(f"\n  [{stats.done}/{stats.total}] ERR {prog.file} {prog.error}")

    def stream(file: str, token: str) -> None:
        print(token, end="", flush=True)

    print("\nTagging 2 files (streaming model output)...\n")
    try:
        tagged = run_tag(cfg, mapping, on_progress=show, on_token=stream)
    except Exception:
        traceback.print_exc()
        return

    print("\n\n--- Sample tagged output (akt118) ---")
    print(tagged["akt118"][1] if "akt118" in tagged else "(akt118 failed)")
    print("\nSUCCESS" if len(tagged) == 2 else "\nPARTIAL FAILURE")


if __name__ == "__main__":
    main()
