# API Usage Inventory

This document lists all external API integrations in the application.

## 1. NVIDIA NIM API
**Purpose:** OCR (Optical Character Recognition) for extracting text from images

**API Key Required:** `NVIDIA_API_KEY`

**Endpoints:**
- OCR: `https://ai.api.nvidia.com/v1/cv/nvidia/nemoretriever-ocr-v1`
- Chat/Parser: `https://integrate.api.nvidia.com/v1/chat/completions`

**Used In:**
- `config.py` - Configuration setup
- `processing.py` - `call_nim_ocr_api()` function for OCR
- `routes.py` - Question number extraction and redaction features
- `redact.py` - Picture redaction in images
- `test.sh` - Testing script
- `templates/question_entry_v2.html` - Frontend OCR feature

**Features:**
- Automatic question number extraction from cropped images
- Text detection and OCR processing
- Image redaction for removing pictures from scanned documents

---

## 2. Google Gemini API
**Purpose:** AI-powered question classification and question-answer extraction

**API Key Required:** `GEMINI_API_KEY` or `GOOGLE_API_KEY`

**Endpoints:**
- `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent`

**Used In:**
- `gemini_classifier.py` - `classify_questions_with_gemini()` - Classifies questions into subjects and chapters
- `gemini_subjective.py` - Subjective question generation
- `qtab_routes.py` - `process_image_for_questions()` - Extracts question-answer pairs from images
- `classifier_routes.py` - Auto-classification of cropped questions (via user setting)
- `neetprep.py` - NeetPrep question classification (via user setting)

**Features:**
- Automatic question classification by subject and NCERT chapter
- Question-answer pair extraction from answer key images
- Subjective question generation
- Model: `gemini-2.0-flash-lite`

---

## 3. OpenRouter API (Amazon Nova)
**Purpose:** Alternative AI model for question classification

**API Key Required:** `OPENROUTER_API_KEY`

**Endpoints:**
- `https://openrouter.ai/api/v1/chat/completions`

**Used In:**
- `nova_classifier.py` - `classify_questions_with_nova()` - Classifies questions using Amazon Nova model
- `classifier_routes.py` - Auto-classification (when user selects Nova model)
- `neetprep.py` - NeetPrep question classification (when user selects Nova model)
- `test.py` - Testing script for Nova API

**Features:**
- Question classification by subject and NCERT chapter
- Alternative to Gemini classifier
- Model: `amazon/nova-2-lite-v1:free`
- User-selectable via Settings page

---

## 4. NeetPrep GraphQL API
**Purpose:** Fetch questions and test attempts from NeetPrep platform

**API Key Required:** None (uses session/headers)

**Endpoints:**
- `https://www.neetprep.com/graphql`

**Used In:**
- `neetprep.py` - `run_hardcoded_query()` function

**Features:**
- Fetch user test attempts
- Get incorrect question IDs
- Retrieve question details (text, options, correct answer, level, topics)
- Batch processing of questions

**GraphQL Queries:**
- `GetAttempts` - Fetch test attempts
- `GetIncorrectIds` - Get incorrect question IDs from attempts
- `GetQuestionDetails` - Retrieve full question data

---

## 5. External Resource Downloads
**Purpose:** Download files from external sources

**Used In:**
- `routes.py` - Download PDFs and images from URLs
- `cli.py` - CLI download functionality
- `utils.py` - Download Arial font from GitHub
- `json_processor.py` / `json_processor_v3.py` - Download images from URLs for PDF generation

**Endpoints (Examples):**
- GitHub: `https://github.com/kavin808/arial.ttf/raw/refs/heads/master/arial.ttf`
- User-provided PDF/image URLs

---

## API Key Summary

| Environment Variable | Required For | Used By |
|---------------------|--------------|---------|
| `NVIDIA_API_KEY` | NVIDIA NIM OCR | processing.py, routes.py, redact.py, config.py |
| `GEMINI_API_KEY` or `GOOGLE_API_KEY` | Google Gemini AI | gemini_classifier.py, gemini_subjective.py, qtab_routes.py |
| `OPENROUTER_API_KEY` | Amazon Nova via OpenRouter | nova_classifier.py, test.py |

---

## User-Configurable API Settings

### Classifier Model Selection
**Location:** Settings page (`templates/settings.html`)

**Database Field:** `users.classifier_model`

**Options:**
1. **Gemini Classifier (Default)** - Uses Google Gemini API
2. **Amazon Nova Lite** - Uses OpenRouter API

**Affects:**
- `classifier_routes.py` - Auto-classification of cropped questions
- `neetprep.py` - NeetPrep question classification

Users can choose their preferred AI model for question classification based on:
- API key availability
- Model performance preferences
- Cost considerations

---

## Rate Limiting & Timeouts

### Configured Timeouts:
- NVIDIA NIM OCR: 300 seconds (5 minutes)
- Gemini API: 300 seconds (5 minutes)
- Nova API: 300 seconds (5 minutes)
- NeetPrep GraphQL: 30 seconds
- Font download: 30 seconds

### Batch Processing:
- Classifier batch size: 7 questions per batch
- Wait time between batches:
  - Classifier routes: 5 seconds
  - NeetPrep: 6 seconds

---

## Notes

1. **API Key Storage:** All API keys are stored as environment variables, not in the database
2. **Error Handling:** All API calls include error handling with logging
3. **Fallback Behavior:** If APIs are unavailable, features gracefully disable with user notifications
4. **Security:** API keys are never exposed in templates or client-side code
