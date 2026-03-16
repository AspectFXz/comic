import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# OpenAI
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
VISION_MODEL = "gpt-5.1"  # or "gpt-5.1" for higher accuracy

# Paths
INPUT_DIR = os.path.join(os.path.dirname(__file__), "input")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
