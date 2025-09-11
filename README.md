# DocuPDF: Smart Scanner & PDF Generator

DocuPDF is a web-based utility that transforms images of documents—like test papers, notes, or book pages—into a polished, organized, and enhanced PDF. It provides powerful 8-point perspective crop, image enhancement tools, and detailed metadata entry, all within your browser.

## ✨ Key Features

### Dual Input Support
- **PDF Upload**: Upload a PDF document and extract individual pages as images
- **Image Upload**: Upload multiple image files directly (PNG, JPG, JPEG, GIF, BMP)

### Advanced Cropping
- Powerful 8-point perspective correction tool
- Draw multiple crop boxes on a single page/image
- Touch-friendly UI with magnifying loupe for precision

### Image Enhancement
- Fine-tune scans with adjustments for:
  - Brightness
  - Contrast
  - Gamma

### Metadata Management
- Add structured metadata to each question:
  - Question Number
  - Subject
  - Status (Correct/Wrong/Unattempted)
  - Marked Solution
  - Actual Solution
  - Time Taken

### Smart Features
- Automatic question number extraction using NVIDIA NIM OCR (when API key is provided)
- Dashboard for session management
- Custom PDF generation with filtering options

### Modern UI
- Responsive design that works on desktop and mobile
- Dark theme for comfortable extended use
- Keyboard shortcuts for power users

## 🔧 Tech Stack

- **Backend**: Flask (Python)
- **Image Processing**: OpenCV, Pillow, PyMuPDF
- **Frontend**: HTML5, Bootstrap 5, JavaScript
- **Database**: SQLite
- **OCR**: NVIDIA NIM API (optional)

## ⚙️ Installation & Setup

### Prerequisites
- Python 3.7+
- pip package installer

### Step-by-Step Guide

1. **Clone the Repository**
   ```bash
   git clone <repository-url>
   cd Report-Generator
   ```

2. **Create and Activate a Virtual Environment (Recommended)**
   ```bash
   # Create the environment
   python -m venv venv
   
   # Activate it
   # On Windows:
   .\venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the Application**
   ```bash
   python app.py
   ```

5. **Open in Browser**
   Navigate to `http://127.0.0.1:1302` in your web browser

### Environment Variables (Optional)

To enable the automatic question number extraction feature, set the NVIDIA_API_KEY environment variable:

```bash
# On Linux/macOS:
export NVIDIA_API_KEY="your-api-key-here"

# On Windows:
set NVIDIA_API_KEY=your-api-key-here

# Or create a .env file with:
NVIDIA_API_KEY=your-api-key-here
```

If you don't set this variable, the application will still work but the automatic question number extraction feature will be disabled.

## 📖 How to Use

### Workflow Options

1. **PDF Workflow**:
   - Upload a PDF document
   - Each page is converted to an image
   - Crop and enhance individual pages
   - Enter question details
   - Generate final PDF

2. **Image Workflow**:
   - Upload multiple image files directly
   - Crop and enhance individual images
   - Enter question details
   - Generate final PDF

### Step 1: Choose Input Method
- Select either PDF upload or multiple image upload from the main page

### Step 2: Crop & Enhance
- For each page/image, draw crop boxes around questions
- Use the sliders to adjust brightness, contrast, and gamma
- Save and continue to the next page/image

### Step 3: Enter Details
- Fill in metadata for each extracted question
- Use productivity features like "Same Subject for All"
- Extract question numbers automatically (if NVIDIA API is configured)

### Step 4: Generate & Download
- Choose PDF name and layout options
- Filter questions by status if needed
- Generate and download your final document

## 📁 Project Structure

```
/
├── uploads/              # Stores original user uploads (temporary)
├── processed/            # Stores cropped & enhanced images (temporary)
├── output/               # Stores the final generated PDFs
├── templates/            # Contains all HTML files
├── app.py                # The main Flask application logic
├── strings.py            # Route constants and string definitions
├── requirements.txt      # List of Python dependencies
└── README.md             # This file
```

## 🧪 Testing

Run the test suite to verify functionality:

```bash
python test.py
```

## 📄 License

This project is licensed under the MIT License.