# OpenNews Auditor

OpenNews Auditor is a web-based AI system that helps users evaluate the reliability of online news articles.

The system accepts an article URL, extracts the article content and headline, identifies important factual claims, searches external news sources for supporting evidence, and uses AI to verify each claim.

The system then calculates an explainable Trust Score based on the verification results.

## How to Install & Run Locally 

### 1. Unzip the Project

1. Download the project ZIP file and extract it to a location on your computer.
2. Open the extracted **OpenNews Auditor** folder in VS Code.

### 2. Create a Virtual Environment

Open a terminal inside the **OpenNews Auditor** folder and run:

```bash
python -m venv .venv
```

### 3. Activate the Virtual Environment

```bash
.\.venv\Scripts\Activate.ps1
```

### 4. Install Dependencies

```bash
pip install -r requirements.txt
```

### 5. Run the Application

```bash
python -m uvicorn backend.main:app --reload --reload-dir backend
```

### 6. Open the Application

Navigate to the following URL in your web browser:
[http://127.0.0.1:8000](http://127.0.0.1:8000)
