# API

Base path: `/api/v1`.

The frontend uses the same API contract with stubs and the real processing pipeline.

## Reports

- GET /reports
  List reports with names, stages, processing statuses, and result summaries.

- POST /reports
  Create a report. Accepts a name and optional context: contract number, reporting period, and target row.

- GET /reports/{report_id}
  Return report details, document statuses, processing progress, blockers, and available actions. This is the main polling endpoint.

- PATCH /reports/{report_id}
  Update the report name, main file, or context before verification starts. 

- POST /reports/{report_id}/run-entity-extraction
  Start entity extraction across documents. Requires all documents to have completed OCR with no remaining file errors.

- POST /reports/{report_id}/run-verification
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

- GET /reports/{report_id}/documents/{document_id}/markdown
  Return the current OCR markdown and its revision.

- POST /reports/{report_id}/documents/{document_id}/markdown
  Save edited markdown as a new revision. Accepts content and base_revision.

- GET /reports/{report_id}/documents/{document_id}/annotations
  Return markdown containing <entity> tags, structured entity parameters, and the annotation revision.

- POST /reports/{report_id}/documents/{document_id}/annotations
  Save edited entity annotations as a new revision. Validate tag syntax, entity IDs, types, and values. Accepts base_revision.

## Requirements

- GET /reports/{report_id}/requirements
  List requirements. Filtering by verdict, type, category, and suppression status work on the client's side.

- POST /reports/{report_id}/requirements/{requirement_id}
  Edit a formula or natural-language condition. Accepts base_revision. Create a revision and automatically queue verification.

- POST /reports/{report_id}/requirements/{requirement_id}/suppression
  Suppress an LLM-generated requirement and record a reason. Preserve the requirement and its original verification result.

- DELETE /reports/{report_id}/requirements/{requirement_id}/suppression
  Remove suppression and restore the requirement's contribution to the summary.

- POST /reports/{report_id}/requirements/{requirement_id}/comments
  Add a comment.
