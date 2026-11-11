# API

Base path: `/api/v1`.

The frontend uses the same API contract with stubs and the real processing pipeline.

## Reports

- GET /reports
  List reports with names, stages, processing statuses, and result summaries. Supports pagination.

- POST /reports
  Create a report. Accepts a name and optional context: contract number, reporting period, and target row.

- GET /reports/{report_id}
  Return report details, document statuses, processing progress, blockers, and available actions. This is the main polling endpoint.

- PATCH /reports/{report_id}
  Update the report name or context before verification starts.

- POST /reports/{report_id}/entity-extraction
  Start entity extraction across documents. Requires all documents to have completed OCR with no remaining file errors.

- POST /reports/{report_id}/verification
  Generate and check requirements. Requires all documents to have completed entity extraction.

- GET /reports/{report_id}/summary
  Return the overall conclusion, counts of passed, failed, unverified, and suppressed requirements, and a short explanation of the main problems. Suppression is counted separately from the original verdict.

## Documents

- POST /reports/{report_id}/documents
  Upload one file using multipart/form-data. Accepts role: final_report or supporting. Return the document ID after upload; OCR continues asynchronously.

- GET /reports/{report_id}/documents/{document_id}
  Return metadata, role, OCR and extraction statuses, current revisions, user-edited flags, and concrete processing errors.

- DELETE /reports/{report_id}/documents/{document_id}
  Delete a document, including a failed upload. The user can upload it again afterward. Available during the upload stage.

- GET /reports/{report_id}/documents/{document_id}/original
  Serve the original file for download or preview.

- GET /reports/{report_id}/documents/{document_id}/preview
  Return a browser-viewable preview, such as a PDF representation of DOCX or XLSX, for side-by-side editing.

- GET /reports/{report_id}/documents/{document_id}/markdown
  Return the current OCR markdown and its revision.

- PUT /reports/{report_id}/documents/{document_id}/markdown
  Save edited markdown as a new revision. Accepts content and base_revision.

- GET /reports/{report_id}/documents/{document_id}/annotations
  Return markdown containing <entity> tags, structured entity parameters, and the annotation revision.

- PUT /reports/{report_id}/documents/{document_id}/annotations
  Save edited entity annotations as a new revision. Validate tag syntax, entity IDs, types, and values. Accepts base_revision.

- GET /reports/{report_id}/entities/{entity_id}
  Resolve an entity to its value, document, source revision, and exact source fragment. Used when clicking an entity in a requirement.

## Requirements

- GET /reports/{report_id}/requirements
  List requirements. Supports filtering by verdict, type, category, and suppression status, plus pagination.

- GET /reports/{report_id}/requirements/{requirement_id}
  Return the requirement, verification result, source evidence, revision, and suppression information. Also used to poll after editing.

- PATCH /reports/{report_id}/requirements/{requirement_id}
  Edit a formula or natural-language condition. Accepts base_revision. Create a revision and automatically queue verification.

- PUT /reports/{report_id}/requirements/{requirement_id}/suppression
  Suppress an LLM-generated requirement and record a reason. Preserve the requirement and its original verification result.

- DELETE /reports/{report_id}/requirements/{requirement_id}/suppression
  Remove suppression and restore the requirement's contribution to the summary.

- GET /reports/{report_id}/requirements/{requirement_id}/comments
  List comments in chronological order.

- POST /reports/{report_id}/requirements/{requirement_id}/comments
  Add a comment.

## Processing and revisions

- Report stage: documents, entities, or verification.
- Report processing_status: idle, queued, running, completed, or failed.
- Each document has independent OCR and extraction statuses.
- Uploading is frontend state until the server accepts the upload.
- Report details include available_actions and blockers. The server determines whether the user can advance.
- Requirements have verdict: passed, failed, or unverified.
- Verification progress and technical errors use a separate verification_status. Insufficient evidence produces an unverified verdict.
- Text requirements have a condition describing what is checked and reasoning explaining the result.
- Suppression is separate from the verdict and is shown separately in the summary.
- Evidence references the exact document revision and fragment used for verification, including page, cell, or bounding-box coordinates where available.
- Document and annotation edits create revisions. Version history is stored but does not need a dedicated UI or API endpoint initially.
- Editing requests include base_revision. Stale edits return 409 Conflict.
- Earlier-stage edits are locked after advancing in the initial implementation.
- Background processing requests return 202 Accepted. Poll the report for stage progress and an individual requirement after a recheck.
- Repeating a start request while processing is running must not create duplicate work. After failure, it retries failed work while preserving edits.

## Requirement result fields

- id: requirement identifier.
- revision: current requirement revision.
- type: formula or text.
- equation: formula expression, when type is formula.
- condition: natural-language condition, when type is text.
- related_entity_ids: entities referenced by the requirement.
- verification_status: queued, running, completed, or failed.
- verdict: passed, failed, or unverified; absent until a current result is available.
- category: issue category, such as amount_mismatch, missing_document, duplicate_document, period_mismatch, document_link_mismatch, or other_mismatch.
- actual: value from the final report and its unit, when available.
- expected: calculated or extracted expected value and its unit, when available.
- difference: actual minus expected, when applicable.
- reasoning: explanation of the verification result.
- evidence: source references containing entity_id, document_id, document_revision, quote, and available location coordinates.
- suppression: null or the recorded suppression information.

Use decimal strings for monetary values and ISO dates.

## Stub scenarios

- Successful report with matching values.
- OCR failure with a concrete error message.
- Amount mismatch with supporting evidence.
- Insufficient evidence producing an unverified result.
- Suppressed LLM finding that retains its original verdict.

Keep fixtures deterministic so they can later serve as API contract test fixtures.
