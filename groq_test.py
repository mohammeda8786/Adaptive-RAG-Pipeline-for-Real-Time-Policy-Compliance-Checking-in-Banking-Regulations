
import os
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

load_dotenv(Path(__file__).resolve().parent / "backend" / ".env", override=False)

client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)

model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

try:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": "Explain RAG in simple words."
            }
        ],
        temperature=0.2,
        max_completion_tokens=300,
    )

    print("Groq LLM connected successfully!\n")
    print(response.choices[0].message.content)

except Exception as e:
    print("Groq LLM test failed:")
    print(e)