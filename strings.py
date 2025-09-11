# strings.py - Constants and route strings for the Report Generator application

# Route URLs
ROUTE_INDEX = '/'
ROUTE_INDEX_V2 = '/v2'
ROUTE_IMAGES = '/images'
ROUTE_UPLOAD_PDF = '/upload_pdf'
ROUTE_UPLOAD_IMAGES = '/upload_images'
ROUTE_CROP_V2 = '/cropv2/<session_id>/<int:image_index>'
ROUTE_PROCESS_CROP_V2 = '/process_crop_v2'
ROUTE_QUESTION_ENTRY_V2 = '/question_entry_v2/<session_id>'
ROUTE_DASHBOARD = '/dashboard'
ROUTE_DELETE_SESSION = '/delete_session/<session_id>'
ROUTE_DELETE_QUESTION = '/delete_question/<image_id>'
ROUTE_SAVE_QUESTIONS = '/save_questions'
ROUTE_EXTRACT_QUESTION_NUMBER = '/extract_question_number'
ROUTE_EXTRACT_ALL_QUESTION_NUMBERS = '/extract_all_question_numbers'
ROUTE_GENERATE_PDF = '/generate_pdf'
ROUTE_DOWNLOAD = '/download/<filename>'
ROUTE_SERVE_IMAGE = '/image/<folder>/<filename>'

# HTTP Methods
METHOD_GET = 'GET'
METHOD_POST = 'POST'
METHOD_DELETE = 'DELETE'

# Database constants
DB_SESSIONS_TABLE = 'sessions'
DB_IMAGES_TABLE = 'images'
DB_QUESTIONS_TABLE = 'questions'

# File types
FILE_TYPE_ORIGINAL = 'original'
FILE_TYPE_CROPPED = 'cropped'

# Status messages
STATUS_SUCCESS = 'success'
STATUS_ERROR = 'error'

# Error messages
ERROR_NO_PDF_FILE_PART = 'No PDF file part'
ERROR_NO_SELECTED_FILE = 'No selected file'
ERROR_INVALID_FILE_TYPE = 'Invalid file type, please upload a PDF'
ERROR_NO_IMAGE_FILES_PART = 'No image files part'
ERROR_NO_SELECTED_FILES = 'No selected files'
ERROR_INVALID_IMAGE_TYPE = 'Invalid file type. Please upload only image files (PNG, JPG, JPEG, GIF, BMP)'
ERROR_SESSION_NOT_FOUND = 'Session not found'
ERROR_IMAGE_NOT_FOUND = 'Image not found'
ERROR_PROCESSING_FAILED = 'Processing failed'

# Success messages
SUCCESS_PDF_UPLOADED = 'PDF uploaded successfully'
SUCCESS_IMAGES_UPLOADED = 'Images uploaded successfully'
SUCCESS_QUESTIONS_SAVED = 'Questions saved successfully'
SUCCESS_PDF_GENERATED = 'PDF generated successfully'