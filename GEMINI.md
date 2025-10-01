# Report Generator Documentation

This document provides an overview of the Report Generator application, its features, and how the code is structured.

## Core Functionality

The primary purpose of this application is to streamline the process of creating analysis reports from PDF documents or images. It is particularly useful for analyzing test papers or other documents containing questions.

### Workflow

1.  **Upload:** The user can start by uploading either a single PDF file or multiple image files.
    *   If a PDF is uploaded, the application splits it into individual pages, which are treated as images.
2.  **Cropping:** The user is then taken to a cropping interface where they can draw boxes around specific areas of interest on each page (e.g., individual questions).
3.  **Data Entry:** After cropping, the user enters details for each cropped image, including:
    *   Question Number
    *   Status (Correct, Wrong, Unattempted)
    *   Marked Answer
    *   Correct Answer
4.  **PDF Generation:** Finally, the user provides metadata for the report (Subject, Tags, Notes) and generates a consolidated PDF report. This report can be filtered to include all questions or only specific statuses (e.g., "Wrong Only").

### Key Features

*   **PDF & Image Upload:** Supports both PDF and multiple image uploads.
*   **Multi-Box Cropping:** An intuitive interface to select multiple questions from a single page.
*   **NVIDIA NIM OCR:** Optionally, the application can use the NVIDIA NIM OCR API to automatically extract question numbers from the cropped images, saving manual entry time. This requires setting the `NVIDIA_API_KEY` environment variable.
*   **Session Management:** Each upload creates a session, which can be persisted to prevent automatic deletion.
*   **PDF Management:** Generated PDFs are stored and can be managed through a dedicated PDF Manager.

## PDF Management

A key feature of this application is the ability to track and manage the final generated PDFs.

*   **Metadata:** Each generated PDF is stored with the following metadata:
    *   **Subject (Mandatory):** The main subject of the report.
    *   **Tags (Optional):** Comma-separated tags for easy filtering.
    *   **Notes (Optional):** A text area for additional details.
    *   **Source File:** The name of the original PDF or images used to create the report.
    *   **Creation Date:** The date and time the PDF was generated.
*   **Persistence:** Like sessions, generated PDFs can be marked as "Persisted" to prevent them from being automatically deleted.
*   **Auto-Deletion:** A cleanup job runs periodically to delete old, non-persisted session data and generated PDFs (defaulting to older than 1 day).
*   **PDF Manager Dashboard:** A dedicated dashboard at `/pdf_manager` allows users to:
    *   View all generated PDFs.
    *   Search and filter PDFs by subject, tags, or notes.
    *   Download any generated PDF.
    *   Toggle the persistence status of a PDF.
    *   Manually delete a PDF.

## Code Structure

The application is built using Flask, a Python web framework.

### Backend (`app.py`)

This file contains the core logic of the application.

*   **Database Setup (`setup_database`):** Initializes the SQLite database and creates the necessary tables (`sessions`, `images`, `questions`, `generated_pdfs`). It also handles schema migrations, such as adding new columns.
*   **Cleanup (`cleanup_old_data`):** Contains the logic for deleting old, non-persisted data.
*   **Flask Routes:**
    *   `/` & `/v2`: Main landing pages for choosing upload type.
    *   `/upload_pdf` & `/upload_images`: Handle the file uploads and create new sessions.
    *   `/cropv2/<session_id>/<image_index>`: Displays the cropping interface.
    *   `/process_crop_v2`: Processes the cropping data and saves the cropped images.
    *   `/question_entry_v2/<session_id>`: The main data entry page.
    *   `/save_questions`: Saves the question data to the database.
    *   `/generate_pdf`: Generates the final PDF report and saves its metadata.
    *   `/dashboard`: Displays the session management dashboard.
    *   `/pdf_manager`: Displays the new PDF management dashboard.
    *   `/delete_session/<session_id>` & `/toggle_persist/<session_id>`: Handle session deletion and persistence.
    *   `/delete_generated_pdf/<pdf_id>` & `/toggle_persist_generated_pdf/<pdf_id>`: Handle generated PDF deletion and persistence.
    *   `/extract_question_number` & `/extract_all_question_numbers`: (Optional) Routes for the NVIDIA NIM OCR functionality.

### Frontend (`templates/`)

The frontend is built with HTML templates using the Jinja2 templating engine and Bootstrap for styling.

*   **`base.html`:** The base template that other templates extend.
*   **`main.html`:** The main entry point, allowing users to choose between PDF and image upload.
*   **`indexv2.html` & `image_upload.html`:** The upload forms.
*   **`cropv2.html`:** The cropping interface.
*   **`question_entry_v2.html`:** The form for entering question details and generating the final PDF.
*   **`dashboard.html`:** The dashboard for managing upload sessions.
*   **`pdf_manager.html`:** The new dashboard for managing the final generated PDFs.

### Database (`database.db`)

A SQLite database is used for data storage.

*   **`sessions`:** Stores information about each upload session.
*   **`images`:** Stores information about each page/image, including original and cropped versions.
*   **`questions`:** Stores the data for each question.
*   **`generated_pdfs`:** Stores the metadata for each final generated PDF.
