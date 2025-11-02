import os
import json
import requests
import sys

def classify_questions_with_gemini(questions):
    """
    Classifies a batch of biology questions using the Gemini API.
    `questions` should be a list of strings.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("Neither GEMINI_API_KEY nor GOOGLE_API_KEY environment variable is set.")

    # Construct the input text with all questions
    input_text = "\n".join([f"{i+1}. {q}" for i, q in enumerate(questions)])

    prompt = f"""
**System Role:** You are a NEET Biology Question Classifier specialized in mapping questions to their corresponding chapters from the NEET syllabus.

Your task is to analyze each biology question and classify it into the most relevant chapter(s) from the official NEET syllabus structure below.

**NEET Biology Syllabus Chapters (Use these exact titles):**

1. The Living World
2. Biological Classification
3. Plant Kingdom
4. Animal Kingdom
5. Morphology of Flowering Plants
6. Anatomy of Flowering Plants
7. Structural Organisation in Animals
8. Cell: The Unit of Life
9. Biomolecules
10. Cell Cycle and Cell Division
11. Photosynthesis in Higher Plants
12. Respiration in Plants
13. Plant Growth and Development
14. Breathing and Exchange of Gases
15. Body Fluids and Circulation
16. Excretory Products and their Elimination
17. Locomotion and Movement
18. Neural Control and Coordination
19. Chemical Coordination and Integration
20. Sexual Reproduction in Flowering Plants
21. Human Reproduction
22. Reproductive Health
23. Principles of Inheritance and Variation
24. Molecular Basis of Inheritance
25. Evolution
26. Health and Disease
27. Improvement in Food Production
28. Microbes in Human Welfare
29. Biotechnology - Principles and Processes
30. Biotechnology and Its Applications
31. Organisms and Populations
32. Ecosystem
33. Biodiversity and Its Conservation

**Classification Guidelines:**

1. **Primary Classification**: Identify the single most relevant chapter that directly addresses the question's core concept
2. **Multi-Chapter Questions**: If a question explicitly spans 2-3 distinct chapters, include all relevant chapters
3. **Confidence Scoring** (0.0 to 1.0):
   - **1.0**: Perfect match
   - **0.8-0.9**: Strong match
   - **0.5-0.7**: Moderate match
   - **Below 0.5**: Avoid unless unavoidable
4. **Non-Biology Questions**: If a question is not from Biology (e.g., it's from Physics or Chemistry), set `chapter_index` to 0 and `chapter_title` to 'Non-Biology'.

**Critical Requirements:**

- Use ONLY the chapter titles exactly as listed above, or 'Non-Biology'.
- Preserve the original question text completely
- Output ONLY valid JSON
- Each question gets an index starting from 1

**Output JSON Schema:**

```json
{{
  "data": [
    {{
      "index": 1,
      "chapter_index": <chapter number or 0>,
      "chapter_title": "<exact chapter title from list or 'Non-Biology'>",
      "original_question_text": "<complete original question with all formatting>",
      "confidence": <0.0 to 1.0>
    }}
  ],
  "success": [true]
}}
```

Now classify the following question(s):
```
{input_text}
```
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}

    request_body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
        }
    }

    print(f"Sending request to Gemini API. Body: {json.dumps(request_body, indent=2)}")

    try:
        response = requests.post(url, headers=headers, json=request_body, timeout=300) # Increased timeout for larger batches
        response.raise_for_status()
        
        print(f"Received raw response from Gemini: {response.text}")
        
        # The response text itself is the JSON data we need
        response_json = response.json()
        nested_json_string = response_json['candidates'][0]['content']['parts'][0]['text']
        return json.loads(nested_json_string)

    except requests.exceptions.RequestException as e:
        print(f"Error during Gemini API call: {repr(e)}", file=sys.stderr)
        print(f"Response body: {e.response.text if e.response else 'N/A'}", file=sys.stderr)
        return None
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"Error parsing Gemini response: {repr(e)}", file=sys.stderr)
        print(f"Raw response text: {response.text if 'response' in locals() else 'N/A'}", file=sys.stderr)
        return None

if __name__ == '__main__':
    test_questions = [
        "1. What is the function of the collecting duct in the nephron, and how is its permeability to water regulated?",
        "2. Explain the law of independent assortment with a dihybrid cross as an example.",
        "3. Describe the key differences between C3 and C4 pathways in photosynthesis.",
        "4. What are the main components of a nucleosome, and what is its role in DNA packaging?"
    ]
    classification = classify_questions_with_gemini(test_questions)
    if classification:
        print(json.dumps(classification, indent=2))
