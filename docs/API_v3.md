# JSON Upload API v3.0

## Endpoint
`POST /json_upload_v3`

## Description
This endpoint allows PWDLV3 (or any compatible client) to submit test data in a standardized JSON v3.0 format to the Report-Generator. The data includes test metadata, configuration for PDF generation, and detailed information about each question, including image URLs. Report-Generator will validate the schema, download images in parallel, store the data, and optionally generate a PDF or provide an edit URL.

## Headers
*   `Content-Type: application/json` (Required)
*   `Authorization: Bearer <token>` (Optional, if authentication is enabled on Report-Generator)

## Request Body
The request body must be a JSON object conforming to the following schema:

```json
{
  "type": "object",
  "properties": {
    "version": {
      "type": "string",
      "const": "3.0",
      "description": "API version, must be '3.0'"
    },
    "source": {
      "type": "string",
      "description": "Source of the data, e.g., 'pwdlv3'",
      "default": "manual"
    },
    "test_name": {
      "type": "string",
      "description": "Name of the test"
    },
    "test_id": {
      "type": "string",
      "description": "Unique ID of the test from the source system"
    },
    "test_mapping_id": {
      "type": "string",
      "description": "Unique ID for mapping purposes, often same as test_id or a derivative"
    },
    "metadata": {
      "type": "object",
      "patternProperties": {
        ".*": { "type": "string" }
      },
      "description": "Arbitrary key-value metadata for the session"
    },
    "config": {
      "type": "object",
      "properties": {
        "statuses_to_include": {
          "type": "array",
          "items": { "type": "string", "enum": ["wrong", "unattempted", "correct"] },
          "description": "Question statuses to include in generated reports"
        },
        "layout": {
          "type": "object",
          "properties": {
            "images_per_page": { "type": "integer", "minimum": 1 },
            "orientation": { "type": "string", "enum": ["portrait", "landscape"] }
          },
          "required": ["images_per_page", "orientation"]
        }
      },
      "required": ["statuses_to_include", "layout"]
    },
    "questions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "question_number": { "type": "string", "description": "Display number for the question" },
          "image_url": { "type": "string", "format": "uri", "description": "URL of the question image" },
          "status": { "type": "string", "enum": ["wrong", "unattempted", "correct"], "description": "User's attempt status" },
          "marked_solution": { "type": "string", "description": "User's marked option/answer" },
          "correct_solution": { "type": "string", "description": "Correct option/answer" },
          "subject": { "type": "string", "description": "Subject of the question" },
          "chapter": { "type": "string", "description": "Chapter of the question" },
          "topic": { "type": "string", "description": "Topic of the question" },
          "time_taken": { "type": "integer", "minimum": 0, "description": "Time taken by user in seconds" }
        },
        "required": ["question_number", "image_url", "status", "marked_solution", "correct_solution", "subject", "time_taken"]
      },
      "minItems": 1
    },
    "view": {
      "type": "boolean",
      "description": "If true, Report-Generator will auto-generate PDF; if false, returns edit URL."
    }
  },
  "required": ["version", "source", "test_name", "test_id", "test_mapping_id", "config", "questions", "view"]
}
```

## Response

### Success Response (HTTP 200 OK)
```json
{
  "status": "success",
  "message": "Data processed successfully",
  "session_id": "uuid-of-new-session",
  "edit_url": "/question_entry_v2/uuid-of-new-session",
  "pdf_url": "/view_pdf/uuid-of-new-session.pdf"  // Only if 'view' was true
}
```

### Error Response (HTTP 400 Bad Request / 500 Internal Server Error)
```json
{
  "status": "error",
  "message": "Detailed error description, e.g., 'Schema validation failed: Missing required field test_id'",
  "errors": [...] // Optional: specific validation errors
}
```

## Examples

### Curl Example: Submit Test Data for Manual Review
This example sends a minimal payload for a single test, opting for manual review in Report-Generator (i.e., `view: false`).

```bash
curl -X POST "http://localhost:5000/json_upload_v3" \
     -H "Content-Type: application/json" \
     -d '{
           "version": "3.0",
           "source": "pwdlv3",
           "test_name": "Physics Midterm",
           "test_id": "PHY101-MID-2024",
           "test_mapping_id": "PHY101-MID-2024-STUDENT001",
           "metadata": {
             "student_id": "STU001",
             "attempt_date": "2024-11-01"
           },
           "config": {
             "statuses_to_include": ["wrong", "unattempted"],
             "layout": { "images_per_page": 4, "orientation": "portrait" }
           },
           "questions": [
             {
               "question_number": "1",
               "image_url": "https://example.com/question1.png",
               "status": "wrong",
               "marked_solution": "B",
               "correct_solution": "C",
               "subject": "Physics",
               "time_taken": 90
             },
             {
               "question_number": "2",
               "image_url": "https://example.com/question2.png",
               "status": "unattempted",
               "marked_solution": "",
               "correct_solution": "A",
               "subject": "Physics",
               "time_taken": 0
             }
           ],
           "view": false
         }'
```

### Curl Example: Submit Test Data for Auto-PDF Generation
This example sends a similar payload but instructs Report-Generator to automatically generate and save the PDF report (`view: true`).

```bash
curl -X POST "http://localhost:5000/json_upload_v3" \
     -H "Content-Type: application/json" \
     -d '{
           "version": "3.0",
           "source": "pwdlv3",
           "test_name": "Chemistry Final",
           "test_id": "CHM202-FIN-2024",
           "test_mapping_id": "CHM202-FIN-2024-STUDENT002",
           "metadata": {
             "student_id": "STU002",
             "attempt_date": "2024-12-05"
           },
           "config": {
             "statuses_to_include": ["wrong", "unattempted"],
             "layout": { "images_per_page": 6, "orientation": "landscape" }
           },
           "questions": [
             {
               "question_number": "1",
               "image_url": "https://example.com/chem_q1.png",
               "status": "wrong",
               "marked_solution": "D",
               "correct_solution": "B",
               "subject": "Chemistry",
               "time_taken": 110
             }
           ],
           "view": true
         }'
```