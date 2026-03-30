# OpenLabel Backend Analysis & Flutter Integration Guide

This document provides a comprehensive technical breakdown of the OpenLabel FastAPI backend (Member 2 Service). It outlines the entire system workflow, the purpose of each file and entity, and explicitly details how the Flutter frontend should integrate with this backend.

---

## 🏗️ 1. Project Overview & Architecture

**OpenLabel Engine Room** is a FastAPI-based backend that serves as the central nervous system connecting the Flutter App to Google Gemini AI and a Supabase PostgreSQL database. 

### Core Capabilities:
- **Scan/OCR label images:** Detects food labeling deception via text extraction and contextual AI processing.
- **Link Parsing/Scraping:** Processes e-commerce URLs (e.g., Blinkit, Amazon) to extract product details dynamically.
- **Dual-Image Analysis:** Allows processing both front branding and back ingredient lists together.
- **FSSAI License Validation:** Authenticates FSSAI licenses to look for "OrganicTruth" compliance.
- **Economic Fraud Detection:** Compares stated prices against wholesale/raw material costs.
- **Auto-Drafts Legal Complaints:** Generates "Jago Grahak Jago" consumer rights complaints if high-severity violations are found.

### The System Workflow:
1. **Input Stage:** Flutter sends an HTTP POST request to `/api/v1/scan/...` containing Base64 images or a product URL. It optionally includes an Authorization Bearer token (JWT).
2. **Orchestration Stage (`routers/scan.py`):** 
   - Authentic token is verified (if provided).
   - The OSINT service is optionally queried for external context (wholesale prices for given product name).
3. **Extraction & Processing (`services/ai_engine.py`):**
   - Images are processed using Gemini Vision to extract raw text (OCR).
   - If a URL is used, a scraper (`services/scraper.py`) pulls the DOM text.
4. **AI Reasoning (`ai_logic/llm_service.py`):**
   - Extracted text and OSINT context are passed to a highly structured Gemini 2.0 Flash prompt.
   - The AI outputs a strictly typed `ProductAnalysisResult` containing Trust Scores, Violations (flags), and verdicts.
5. **Validation & Storage:**
   - Any 14-digit FSSAI numbers are structurally validated (`services/fssai_validator.py`).
   - The complete report, along with user details, is inserted into Supabase `scans` table.
6. **Output Stage:** The result is transformed into a `ScanResponse` JSON format and returned to Flutter.

---

## 📂 2. File Directory & Entity Analysis

### Root Level
- **`main.py`**: The FastAPI application entry point. Configures CORS, sets up routing, lifespan context, and includes a small integrated HTML demo at `/demo`.
- **`config.py`**: Pydantic `Settings` manager loading `.env` variables (Supabase Keys, Gemini API key, Ports, CORS origins).
- **`auth.py`**: Authentication dependency using Supabase JWT verification. Exposes `get_current_user` (strict) and `get_optional_user` (allows anonymous scans).
- **`database.py`**: Instantiates and singleton-caches the Supabase Admin (bypasses RLS for backend operations) and Public (respects RLS) clients.
- **`requirements.txt` / `Backend.txt` / `context.txt`**: Project dependencies and AI context blueprints.

### `/routers/` (API Controllers)
- **`scan.py`**: Core logic endpoints. Exposes `/api/v1/scan/image`, `/api/v1/scan/dual-image`, and `/api/v1/scan/link`. Handles saving scans to Supabase.
- **`reports.py`**: History endpoints. Exposes `/api/v1/reports` (paginated list of past scans) and `/api/v1/reports/{scan_id}` (detailed view). Requires Strict Auth.
- **`users.py`**: Profile endpoints. Exposes `/api/v1/profile` for managing user preferences (allergies, preference level).

### `/models/` (Pydantic Schemas - The Intrinsic Entities)
- **`schemas.py`**: The definitive type models representing requests and responses.
  - **Requests**: `ScanImageRequest`, `ScanDualImageRequest`, `ScanLinkRequest`.
  - **Responses**:
    - `ScanResponse`: The primary response payload (Trust score, verdict, flags, FSSAI).
    - `FlagItem`: Entity representing a specific violation (`code`, `title`, `severity`, `evidence`, `rationale`).
    - `ReportSummary` & `ReportListResponse`: Entities for history pagination.
    - `UserProfileResponse` & `UserProfileUpdate`: Entities for user allergy configurations.

### `/services/` (Backend Logic Bridges)
- **`ai_engine.py`**: Adapts internal image/text processing workflows into the underlying `ai_logic` folder. Contains MIME type inference and Gemini Vision OCR fallbacks.
- **`fssai_validator.py`**: A robust logic checker that validates the 14-digit format structure of Indian FSSAI numbers.
- **`osint_data.py`**: Interacts dynamically to establish wholesale thresholds (Adulter-Arb).
- **`scraper.py`**: Built-in Playwright/BeautifulSoup web-scraper for interpreting Blinkit/Amazon/BigBasket pages dynamically.

### `/ai_logic/` (The Member 3 AI core)
- **`llm_service.py`**: Contains the complex, 3-shot prompt injected into Gemini 2.0 Flash to strictly return the required mathematical JSON arrays. 
- **`osint_service.py` / `vision_service.py`**: Sandbox utility scripts defining AI parameters.

---

## 🚀 3. Flutter Integration Guide

To successfully attach the Flutter UI to this FastAPI server, you must follow the specifications below.

> **Base URL:** Replace `https://your-api-url.onrender.com` with your active hosted backend or `http://10.0.2.2:8000` for Android Emulator.

### A. Authentication & Supabase
Flutter should **directly authenticate with Supabase** using the `supabase_flutter` SDK. 
- Login/Signup happens in Flutter using Supabase endpoints.
- Once authenticated, Flutter obtains an **Access Token (JWT)**.
- For all backend calls requiring identity, pass this JWT in the headers:
  ```http
  Authorization: Bearer <SUPABASE_JWT_TOKEN>
  ```

### B. Core API Endpoints for Flutter

#### 1. Scan Single Image (Front Label)
- **Endpoint:** `POST /api/v1/scan/image`
- **Auth:** Optional (`Authorization: Bearer <Token>`)
- **Body (JSON):**
  ```json
  {
    "image_base64": "data:image/jpeg;base64,/9j/4AA...",
    "product_name": "Optional Product Name",
    "retail_price": 60.5
  }
  ```
- **Returns:** `ScanResponse` object.

#### 2. Scan Dual Images (Front & Back)
- **Endpoint:** `POST /api/v1/scan/dual-image`
- **Body (JSON):**
  ```json
  {
    "front_image_base64": "data:image/jpeg;base64,...",
    "back_image_base64": "data:image/jpeg;base64,...",
    "product_name": null,
    "retail_price": null
  }
  ```

#### 3. URL E-Commerce Link Scan
- **Endpoint:** `POST /api/v1/scan/link`
- **Body (JSON):**
  ```json
  {
    "url": "https://blinkit.com/prn/some-product/1234"
  }
  ```

#### 4. Fetch Scan History
- **Endpoint:** `GET /api/v1/reports?page=1&per_page=20`
- **Auth:** **Required**
- **Returns:** Paginated lists of `ReportSummary`. Use this to build a History feed. Note the total count to control infinite scrolling.

#### 5. Fetch Specific Report Full Detail
- **Endpoint:** `GET /api/v1/reports/{scan_id}`
- **Auth:** **Required**
- **Returns:** Matches the `ScanResponse` but includes additional fields (`raw_text_extracted`).

#### 6. User Profile (Allergies)
- **Endpoint:** `GET /api/v1/profile` and `PUT /api/v1/profile`
- **Auth:** **Required**
- **Body for PUT:**
  ```json
  {
    "allergies": ["Peanuts", "Dairy"],
    "preference_level": "Strict"
  }
  ```

### C. Constructing the UI from Responses (The `ScanResponse`)

When Flutter receives a `ScanResponse`, it should govern UI layout as follows:

```json
{
  "scan_id": "uuid...",
  "product_name": "Bournvita",
  "trust_score": 45.0,
  "trust_level": "YELLOW",  // Use this for global background hue (RED/YELLOW/GREEN)
  "overall_verdict": "High sugar content obfuscated as liquid glucose.",
  "flags": [
    {
       "code": "INGREDIENT_SPLITTING",
       "title": "Sugar Splitting",
       "severity": "high", // Render with Red alert icons
       "evidence": "Liquid glucose, invert syrup, maltodextrin",
       "rationale": "Misleads consumer about total sugar percentage"
    }
  ],
  "fssai_number": "10012011000168", // If null, AI couldn't find it. If present, show verified badge.
  "legal_draft_available": true, 
  "legal_draft_text": "To the Consumer Forum..." // Show a button: "Generate Consumer Complaint" if true.
}
```

> **Performance Optimization in Flutter:**
> Before sending images to `/api/v1/scan/image`, ensure Flutter compresses the images. Use a package like `flutter_image_compress` to compress JPEGs down to ~800px width/height and quality 80. Huge multi-megabyte base64 strings will cause latency spikes and timeout issues over cellular networks.
