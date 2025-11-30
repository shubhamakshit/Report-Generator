import os
import json
import requests
import sys
import base64
from typing import List, Optional, Dict, Any

def generate_subjective_questions(image_path: str) -> Optional[Dict[Any, Any]]:
    """
    Transcribes and structures subjective questions from an image using the Gemini API.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is not set.", file=sys.stderr)
        return None

    # Read and encode image
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        print(f"Error reading image file: {e}", file=sys.stderr)
        return None

    model_id = "gemini-flash-latest"  
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}

    prompt_text = """
    Analyze the provided image. It contains a list of subjective questions (handwritten or printed).
    
    Task:
    1.  **Transcribe** each question exactly as written.
    2.  **Identify the Topic:** Determine the subject or topic for each question (e.g., "Ascomycetes", "Thermodynamics"). If the header specifies a topic, use that.
    3.  **Structure:** Return the data in the specified JSON format.
    4.  **Numbering:** Use the question number found in the image.
    
    If the image contains multiple questions, extract all of them.
    """

    request_body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                     {
                        "inline_data": {
                            "mime_type": "image/jpeg", # Assuming JPEG/PNG, API is flexible with image/* usually, but let's send jpeg or png based on file if needed, usually jpeg works for generic
                            "data": encoded_string
                        }
                    },
                    {
                        "text": prompt_text
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "data": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question_topic": {"type": "string"},
                                "question_html": {"type": "string"},
                                "question_number_within_topic": {"type": "string"}
                            },
                            "required": ["question_topic", "question_html", "question_number_within_topic"]
                        }
                    }
                },
                "required": ["success", "data"]
            }
        }
    }

    try:
        response = requests.post(url, headers=headers, json=request_body, timeout=120)
        response.raise_for_status()

        response_json = response.json()
        
        # Extract text from candidate
        candidate = response_json.get('candidates', [{}])[0]
        content = candidate.get('content', {})
        parts = content.get('parts', [])
        
        if not parts:
            print("Error: Gemini generated no content.")
            return None
            
        text = parts[0]['text']
        return json.loads(text)

    except requests.exceptions.RequestException as e:
        print(f"Error during Gemini API call: {e}", file=sys.stderr)
        if e.response:
             print(f"Response: {e.response.text}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON response: {e}", file=sys.stderr)
        print(f"Raw text: {text}", file=sys.stderr)
        return None

if __name__ == "__main__":
    # Test the function
    result = generate_subjective_questions("Ascomycetes")
    if result:
        print(json.dumps(result, indent=2))
