# Third-party components

The project license covers original code in this repository. It does not relicense
third-party software, model weights, provider services, or user-supplied documents.

- Python: https://www.python.org/psf/license/
- pypdf: https://github.com/py-pdf/pypdf
- pdfplumber and pdfminer.six: https://github.com/jsvine/pdfplumber and https://github.com/pdfminer/pdfminer.six
- Ollama and embedding model: https://github.com/ollama/ollama and https://ollama.com/library/mxbai-embed-large
- Qdrant: https://github.com/qdrant/qdrant
- Open WebUI: https://github.com/open-webui/open-webui — inspect its current license, including branding requirements.
- Gemini API: https://ai.google.dev/gemini-api/terms

Container image tags are explicit for the reference stack; tags are not cryptographic
digest locks. Python runtime base tracks the 3.12 minor series for patch updates. Embedding weights
are fetched by Ollama model name, whose contents may change. Python requirements pin direct
and transitive runtime packages; pyproject lists the direct requirements. Dependency updates
and vulnerability review are separate maintenance tasks before production deployment.
