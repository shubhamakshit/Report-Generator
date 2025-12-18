import os
import json
import requests
import sys
from typing import List, Optional, Dict, Any
from api_key_manager import get_api_key_manager

def classify_questions_with_nova(questions: List[str], start_index: int = 0) -> Optional[Dict[Any, Any]]:
    """
    Classifies a single batch of questions using Amazon Nova via OpenRouter API.
    `questions` should be a list of strings representing one batch.
    `start_index` is the overall starting index for this batch (e.g., 0, 7, 14...).
    """
    # Get API key from the manager
    manager = get_api_key_manager()
    api_key, key_index = manager.get_key('openrouter')
    
    if not api_key:
        raise ValueError("No available OpenRouter API keys. Please set OPENROUTER_API_KEY environment variable.")

    # Construct the input text with the current batch of questions
    # The model expects 1-based indexing in the prompt.
    input_text = "\n".join([f"{j + start_index + 1}. {q}" for j, q in enumerate(questions)])

    prompt = f"""
**System Role:** You are a question classifier for NEET/JEE exams, specialized in mapping questions to their corresponding subjects and chapters from the NCERT syllabus.

Your task is to analyze each question, first classify it into the most relevant subject, and then identify the most relevant chapter(s) from the official syllabus structures provided below.

**Available Subjects (Use these exact titles):**
- Biology
- Chemistry
- Physics
- Mathematics

**Syllabus Chapters (Use these exact titles for the respective subjects):**

---
**1. BIOLOGY (Common for NEET & JEE)**

**Class XI**
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

---
**2. CHEMISTRY (Common for NEET & JEE)**

**Class XI**
1. Some Basic Concepts of Chemistry
2. Structure of Atom
3. Classification of Elements and Periodicity in Properties
4. Chemical Bonding and Molecular Structure
5. States of Matter: Gases and Liquids
6. Thermodynamics
7. Equilibrium
8. Redox Reactions
9. Hydrogen
10. The s-Block Elements
11. The p-Block Elements (Group 13 and 14)
12. Organic Chemistry – Some Basic Principles and Techniques (GOC)
13. Hydrocarbons
14. Environmental Chemistry

**Class XII**
1. The Solid State
2. Solutions
3. Electrochemistry
4. Chemical Kinetics
5. Surface Chemistry
6. General Principles and Processes of Isolation of Elements (Metallurgy)
7. The p-Block Elements (Group 15 to 18)
8. The d- and f- Block Elements
9. Coordination Compounds
10. Haloalkanes and Haloarenes
11. Alcohols, Phenols and Ethers
12. Aldehydes, Ketones and Carboxylic Acids
13. Amines
14. Biomolecules
15. Polymers
16. Chemistry in Everyday Life

---
**3. PHYSICS (Common for NEET & JEE)**

**Class XI**
1. Units and Measurements
2. Motion in a Straight Line
3. Motion in a Plane
4. Laws of Motion
5. Work, Energy and Power
6. System of Particles and Rotational Motion
7. Gravitation
8. Mechanical Properties of Solids
9. Mechanical Properties of Fluids
10. Thermal Properties of Matter
11. Thermodynamics
12. Kinetic Theory
13. Oscillations
14. Waves

**Class XII**
1. Electric Charges and Fields
2. Electrostatic Potential and Capacitance
3. Current Electricity
4. Moving Charges and Magnetism
5. Magnetism and Matter
6. Electromagnetic Induction
7. Alternating Current
8. Electromagnetic Waves
9. Ray Optics and Optical Instruments
10. Wave Optics
11. Dual Nature of Radiation and Matter
12. Atoms
13. Nuclei
14. Semiconductor Electronics: Materials, Devices and Simple Circuits
15. Communication Systems

---
**4. MATHEMATICS (For JEE Only)**

**Class XI**
1. Sets
2. Relations and Functions
3. Trigonometric Functions
4. Principle of Mathematical Induction
5. Complex Numbers and Quadratic Equations
6. Linear Inequalities
7. Permutations and Combinations
8. Binomial Theorem
9. Sequences and Series
10. Straight Lines
11. Conic Sections
12. Introduction to Three Dimensional Geometry
13. Limits and Derivatives
14. Mathematical Reasoning
15. Statistics
16. Probability

**Class XII**
1. Relations and Functions
2. Inverse Trigonometric Functions
3. Matrices
4. Determinants
5. Continuity and Differentiability
6. Application of Derivatives
7. Integrals
8. Application of Integrals
9. Differential Equations
10. Vector Algebra
11. Three Dimensional Geometry
12. Linear Programming
13. Probability

---

**Classification Guidelines:**

1.  **Primary Classification**: Identify the single most relevant subject, and then the most relevant chapter(s) within that subject, that directly addresses the question's core concept.
2.  **Multi-Chapter Questions**: If a question explicitly spans 2-3 distinct chapters, include all relevant chapters.
3.  **Confidence Scoring** (0.0 to 1.0):
    *   **1.0**: Perfect match
    *   **0.8-0.9**: Strong match
    *   **0.5-0.7**: Moderate match
    *   **Below 0.5**: Avoid unless unavoidable.
4.  **Non-Syllabus Questions**: If a question is not from any of the provided subjects/chapters, set `subject` to 'Unclassified' and `chapter_title` to 'Unclassified'.

**Critical Requirements:**

-   Use ONLY the subject titles exactly as listed above, or 'Unclassified'.
-   Use ONLY the chapter titles exactly as listed above, or 'Unclassified'.
-   Preserve the original question text completely.
-   Output ONLY valid JSON.
-   The "index" field MUST match the question number shown in the input (e.g., if the question is numbered "8.", then "index": 8).

**Output JSON Schema:**

```json
{{
  "data": [
    {{
      "index": 1,
      "subject": "<exact subject title from list or 'Unclassified'>",
      "chapter_index": <chapter number or 0>,
      "chapter_title": "<exact chapter title from list or 'Unclassified'>",
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

Output ONLY the JSON response, nothing else.
"""

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    request_body = {
        "model": "amazon/nova-2-lite-v1:free",
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }

    print(f"Sending batch to Nova API with {len(questions)} questions.")
    print(f"Sending request to Nova API. Body: {json.dumps(request_body, indent=2)}")  # Full logging enabled

    try:
        response = requests.post(url, headers=headers, json=request_body, timeout=300)
        response.raise_for_status()

        print(f"Received raw response from Nova: {response.text}")  # Full logging enabled

        # Parse the response JSON
        response_json = response.json()

        # Extract the content from Nova's response
        choices = response_json.get('choices', [])
        if not choices:
            print("Error: Nova API returned no choices.")
            return None
        
        content = choices[0].get('message', {}).get('content', '')
        if not content:
            print("Error: Nova API returned empty content.")
            return None

        # Nova often wraps JSON in markdown code blocks, so we need to extract it
        content = content.strip()
        
        # Remove markdown code block markers if present
        if content.startswith('```json'):
            content = content[7:]  # Remove ```json
        elif content.startswith('```'):
            content = content[3:]  # Remove ```
            
        if content.endswith('```'):
            content = content[:-3]  # Remove closing ```
            
        content = content.strip()

        # Parse the JSON from the content
        batch_result = json.loads(content)
        manager.mark_success('openrouter', key_index)
        return batch_result

    except requests.exceptions.RequestException as e:
        print(f"Error during Nova API call: {repr(e)}", file=sys.stderr)
        print(f"Response body: {e.response.text if e.response else 'N/A'}", file=sys.stderr)
        manager.mark_failure('openrouter', key_index)
        return None
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"Error parsing Nova response: {repr(e)}", file=sys.stderr)
        print(f"Raw response text: {response.text if 'response' in locals() else 'N/A'}", file=sys.stderr)
        manager.mark_failure('openrouter', key_index)
        return None
