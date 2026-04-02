🤖 Workflow Automation Bot (v2.0 Enterprise Edition)
Deep business process automation system for a funeral service agency.
The project integrates Computer Vision (OCR), multi-level AI-agent processing,
and stealth integration with legacy accounting systems (1C) via WinAPI.

🌟 Key Features
🧠 Multi-Agent Pipeline
Distributed data processing across specialized AI agents:

Vision Agent: Digitization of handwritten forms using gemini-3-flash-preview. Recognizes complex handwriting,
abbreviations, and unstructured lists.

Logic Engine: Automatic data normalization based on directories (services, cemeteries)
and tariff calculation (extra points, floor carrying fees) using gemini-3.1-flash-lite-preview.

1C Sync Assistant: Visual duplicate control. Scans the 1C interface in real-time to prevent duplicate data entry.

🛡️ Data Integrity (Bulletproof Validation)
Every operation passes through strict Pydantic v2 schemas.

Includes mathematical sum checks and protection against data type overflow (PostgreSQL compatible).

🕶️ Stealth Integration
Automation of external Windows applications via WinAPI (ctypes).

Keyboard input emulation and layout management without direct database access.

🧪 Resilience Testing
Implementation of Property-based testing (Hypothesis) to uncover edge cases when parsing complex strings and numbers.

🛠 Tech Stack

Category:	Tools
Language:	Python 3.13
AI Stack:	Google Gemini API (Flash & Lite models)
Validation:	Pydantic v2, Hypothesis (Testing)
Automation:	PyAutoGUI, Keyboard, WinAPI (ctypes), Pyperclip
Processing:	Pillow (PIL), JSON normalization

├── src/
│   ├── agent_vision.py      # Digitization of forms (Agent 1)
│   ├── agent_logic.py       # Business rules and normalization (Agent 2)
│   ├── agent_booked_ocr.py  # Computer vision for 1C interface (Agent 3)
│   ├── stealth_1c.py        # Low-level Windows API operations
│   ├── validator.py         # Pydantic schemas and math control
│   └── utils.py             # Bulletproof data processing utilities
├── tests/                   # Test coverage (Pytest + Hypothesis)
├── data/                    # Local storage (inputs/outputs/logs)
└── main.py                  # Entry point (CLI Interface)

🚀 Getting Started
Clone the repo: git clone [https://github.com/Andrey-Sema/hier_automato.git](https://github.com/Andrey-Sema/hier_automato.git)

Setup environment: python -m venv .venv

Install dependencies: pip install -r requirements.txt

Configure .env: Add your GOOGLE_API_KEY.