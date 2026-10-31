
import os
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

load_dotenv(Path(__file__).resolve().parent / "backend" / ".env", override=False)

client = Groq(api_key=os.environ["GROQ_API_KEY"])

response = client.chat.completions.create(
    model="qwen/qwen3.8-27b",
    messages=[
        {
            "role": "user",
            "content": "Reply with exactly: Qwen is working."
        }
    ],
    temperature=0,
    max_completion_tokens=50,
)

print(response.choices[0].message.content)