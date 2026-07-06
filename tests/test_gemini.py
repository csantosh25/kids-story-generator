import os
from dotenv import load_dotenv
from google import genai

# Load .env file
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("GEMINI_API_KEY not found in .env")

client = genai.Client(api_key=api_key)

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Write one sentence about a sleepy bunny."
)

print("\nGemini Response:\n")
print(response.text)