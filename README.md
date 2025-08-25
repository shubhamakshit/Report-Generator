Of course. Here is a beautiful and comprehensive README.md file for your project. It's structured to be professional, easy to read, and visually appealing using Markdown.

You can save this content directly into a file named README.md in your project's root directory.

🚀 DocuPDF: Smart Scanner & PDF Generator

DocuPDF is a web-based utility that transforms images of documents—like test papers, notes, or book pages—into a polished, organized, and enhanced PDF. It mimics the functionality of modern mobile scanner apps, providing a powerful 8-point perspective crop, image enhancement tools, and detailed metadata entry, all within your browser.

<br>




✨ Key Features

Multi-File Upload: Upload multiple images at once. The app intelligently sorts them based on numbers in the filenames (e.g., IMG_001.jpg, IMG_002.jpg).

Scanner-Like Cropping: A powerful and intuitive 8-point perspective correction tool lets you define the exact boundaries of your document, even if the photo was taken at an angle.

Touch-Friendly UI: The cropping interface is fully responsive and touch-friendly, complete with a magnifying loupe for pixel-perfect precision on any device.

Image Enhancement: Fine-tune your scans with adjustments for brightness, contrast, and gamma to ensure maximum readability.

Detailed Data Entry: Add structured metadata to each image, such as Question Number, Subject, Status (Correct/Wrong/Unattempted), and more.

Quality-of-Life Tools:

"Same Subject for All" toggle to quickly fill in subject details.

"Disable Time" checkbox for questions where time tracking is not applicable.

Custom PDF Generation:

Combine all your processed images into a single A4 PDF.

Customize the number of images per page (1, 2, 4, 6, or 8).

Filter the PDF to include only "All," "Wrong," or "Unattempted" questions.

Modern Dark Theme: An elegant and easy-on-the-eyes dark theme powered by Bootstrap 5.

🔧 Tech Stack

Backend: Flask (Python)

Image Processing: OpenCV, Pillow

Frontend: HTML5, Bootstrap 5, JavaScript (for interactive cropping and UI logic)

Dependencies: requests (for a font-fetching fallback)

⚙️ Installation & Setup

Follow these steps to get the application running on your local machine.

Prerequisites

Python 3.7+

pip package installer

Step-by-Step Guide

Clone the Repository

code
Bash
download
content_copy
expand_less

git clone https://github.com/your-username/docupdf.git
cd docupdf

Create and Activate a Virtual Environment (Recommended)

code
Bash
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
# Create the environment
python -m venv venv

# Activate it
# On Windows:
.\venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

Install Dependencies
The project includes a requirements.txt file to install all necessary packages.

code
Bash
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
pip install -r requirements.txt

Run the Application

code
Bash
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
flask run

You should see output indicating that the server is running, similar to this:

code
Code
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
* Running on http://127.0.0.1:5000

Open in Browser
Navigate to http://127.0.0.1:5000 in your web browser to start using DocuPDF.

📖 How to Use

The application workflow is designed to be simple and linear.

Step 1: Upload Images

Click "Select images" to open the file picker or drag and drop your files.

You can select multiple images. They will be automatically sorted.

Click "Upload and Start Processing."

Step 2: Crop & Enhance

For each image, you'll be presented with the cropping interface.

Drag the corner points (circles) and edge points (squares) to fit the document's boundary. Use the magnifying loupe that appears on drag for precision.

Use the sliders on the right to adjust brightness, contrast, and gamma.

Click "Save and Next" to process the image and move to the next one.

Step 3: Enter Details

Once all images are processed, the data entry form will appear.

Fill in the details for each question.

Use the "Same Subject for All" toggle and "Disable Time" checkboxes to speed up the process.

Step 4: Generate & Download

At the bottom of the form, choose a name for your PDF, select the number of images per page, and apply a filter if needed.

Click "Generate PDF".

A download link will appear. Click it to save your final document.

📁 Project Structure
code
Code
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
/
├── uploads/              # Stores original user uploads (temporary)
├── processed/            # Stores cropped & enhanced images (temporary)
├── output/               # Stores the final generated PDFs
├── templates/            # Contains all HTML files (index, crop, question_entry)
├── app.py                # The main Flask application logic
├── requirements.txt      # List of Python dependencies
└── README.md             # You are here!
📄 License

This project is licensed under the MIT License. See the LICENSE file for more details.