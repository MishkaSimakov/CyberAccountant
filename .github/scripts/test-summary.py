"""Write test counts and step outcomes to the GitHub Actions summary."""

import os
from pathlib import Path
import xml.etree.ElementTree as ET


print("## Tests\n")
print(f"Production stack: **{os.environ.get('STACK_RESULT', 'unknown')}**\n")
print(f"Frontend build: **{os.environ.get('CLIENT_BUILD_RESULT', 'unknown')}**\n")
print("| Suite | Result | Passed | Failed | Errors | Skipped |")
print("| --- | --- | ---: | ---: | ---: | ---: |")
for suite, result in (("python", "PYTHON_RESULT"), ("client", "CLIENT_RESULT")):
    path = Path("test-results") / f"{suite}.xml"
    outcome = os.environ.get(result, "unknown")
    if not path.is_file():
        print(f"| {suite} | {outcome} (no report) | — | — | — | — |")
        continue
    cases = list(ET.parse(path).iter("testcase"))
    failed = sum(case.find("failure") is not None for case in cases)
    errors = sum(case.find("error") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    passed = len(cases) - failed - errors - skipped
    print(f"| {suite} | {outcome} | {passed} | {failed} | {errors} | {skipped} |")
print("\nFull JUnit reports, test output and container logs are in the `test-results` artifact.")
