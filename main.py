#!/usr/bin/env python3
import os
import hmac
import hashlib
import requests
import openai
import json
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from datetime import datetime
import re
from weasyprint import HTML # --- NEW: Import library for PDF generation ---

# === LOAD CONFIGURATION FROM ENVIRONMENT VARIABLES ===
# A function to ensure all required environment variables are set on startup.
def load_env_vars():
    required_vars = [
        "GOOGLE_DRIVE_FOLDER_ID", "OPENAI_API_KEY", "ZOOM_SECRET_TOKEN",
        "ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET",
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"
    ]
    config = {}
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            raise ValueError(f"❌ Missing required environment variable: {var}")
        config[var] = value
    return config

try:
    config = load_env_vars()
except ValueError as e:
    print(e)
    exit(1) # Exit if configuration is missing

# Set the OpenAI API key
openai.api_key = config["OPENAI_API_KEY"]

# The prompt for analyzing the consultation
CONSULTATION_FRAMEWORK = """
You are a medical consultation analyst. Your task is to create a clean, professional, and easy-to-read consultation report from a transcript. Use Markdown for formatting.

# Consultation Summary Report

---

## Overview
- **Provider:** [Insert or infer]
- **Consult Duration:** {duration}
- **Overall Score:** [Rate 1–10 based on completeness of the 15-point framework]

## Key Takeaways
*Provide a brief, 2-3 sentence summary of the main points of the consultation, including key recommendations and outcomes.*

---

## Detailed Framework Analysis

1.  **Introduction of Provider:**
2.  **Confirmation of Patient by Name and DOB:**
3.  **Confirmation of Patient Location:**
4.  **Confirmation of Current Regimen:**
5.  **Symptoms, Goals for Treatment:**
6.  **Health Updates, Medication Reconciliation, Preventative Screening:**
7.  **Blood Donation Regimen:**
8.  **Lab Review:**
9.  **HRT/Peptide/Other Recommendations:**
10. **Blood Donation Plans:**
11. **Lab Follow-up Plan:**
12. **Refill Needs:**
13. **CC Confirmation:**
14. **Shipping Address Confirmation:**
15. **Review Plan & Patient Q&A:**

---
*For each of the 15 points above, extract the relevant information. If a section is not discussed, write: “❌ Not addressed.”*
"""

# --- NEW: HTML Template for Professional Reports ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Consultation Summary</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            background-color: #f8f9fa;
            margin: 0;
            padding: 20px;
        }}
        .container {{
            max-width: 800px;
            margin: 20px auto;
            background-color: #ffffff;
            border: 1px solid #dee2e6;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            padding: 40px;
        }}
        h1, h2 {{
            color: #2c3e50;
            border-bottom: 2px solid #3498db;
            padding-bottom: 10px;
        }}
        h1 {{
            text-align: center;
            margin-bottom: 30px;
        }}
        hr {{
            border: none;
            border-top: 1px solid #dee2e6;
            margin: 30px 0;
        }}
        .overview {{
            background-color: #ecf0f1;
            padding: 20px;
            border-radius: 5px;
            margin-bottom: 30px;
        }}
        .overview ul {{
            list-style: none;
            padding: 0;
        }}
        .overview li {{
            font-size: 1.1em;
            margin-bottom: 10px;
        }}
        strong {{
            color: #34495e;
        }}
        .framework-item {{
            margin-bottom: 15px;
        }}
        .not-addressed {{
            color: #e74c3c;
            font-style: italic;
        }}
    </style>
</head>
<body>
    <div class="container">
        {report_content}
    </div>
</body>
</html>
