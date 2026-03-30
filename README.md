# 🛡️ OpenLabel — Engine Room (Member 2 Backend)

> **Consumer OSINT Backend** — FastAPI server powering the OpenLabel food label intelligence platform.

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green?logo=fastapi)
![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-orange?logo=supabase)
![Gemini](https://img.shields.io/badge/Gemini_2.0-Flash-purple?logo=google)

---

## 📐 Architecture

```
Flutter App (M1)          This Server (M2)           AI Brain (M3)
   │                          │                          │
   ├── POST /scan/image  ────►│                          │
   │   (base64 image)         ├── Gemini Vision ────────►│
   │                          │◄── Structured JSON ──────┤
   │                          │                          │
   ├── POST /scan/link   ────►│                          │
   │   (product URL)          ├── Scrape → OSINT ───────►│
   │                          │◄── Analysis Report ──────┤
   │                          │                          │
   │◄── JSON Response ────────┤                          │
   │                          ├── Store in Supabase      │
   │                          │                          │
```

---

## 🚀 Quick Start

### 1. Prerequisites
- **Python 3.11+**
- **Supabase Project** (free tier works)
- **Google Gemini API Key** ([Get one here](https://aistudio.google.com/apikey))

### 2. Clone & Setup
```bash
cd OpenLabel

# Create virtual environment
python -m venv .venv

# Activate it
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Variables
```bash
# Copy the template
copy .env.example .env   # Windows
cp .env.example .env     # macOS/Linux

# Edit .env with your actual values
```

Required variables:
| Variable | Description |
|---|---|
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_ANON_KEY` | Public anon key (from Supabase settings) |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key (from Supabase settings) |
| `GEMINI_API_KEY` | Google Gemini API key |

### 4. Setup Database
Run the SQL queries from `supabase_setup.sql` in your Supabase SQL Editor. (See the SQL section below)

### 5. Run the Server
```bash
# Development (auto-reload)
uvicorn main:app --reload --port 8000

# Or via Python
python main.py
```

### 6. Open API Docs
Visit: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 📡 API Endpoints

### 🔍 Scan & Analyze

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/v1/scan/image` | Optional | Analyze food label image |
| `POST` | `/api/v1/scan/link` | Optional | Analyze product from URL |

### 📊 Reports & History

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/v1/reports` | Required | List scan history (paginated) |
| `GET` | `/api/v1/reports/{id}` | Required | Get full report detail |

### 👤 User Profile

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/v1/profile` | Required | Get allergy preferences |
| `PUT` | `/api/v1/profile` | Required | Update preferences |

### 🏥 Health

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/` | None | Health check |
| `GET` | `/health` | None | Health check (alias) |

---

## 📦 Request/Response Examples

### POST /api/v1/scan/image
```json
// Request
{
    "image_base64": "<base64-encoded-image>",
    "product_name": "Dabur Honey",
    "retail_price": 299
}

// Response
{
    "scan_id": "uuid-here",
    "product_name": "Dabur Honey",
    "trust_score": "RED",
    "overall_verdict": "Multiple sugar variants detected suggesting deceptive ingredient listing.",
    "flags": [
        {
            "title": "Sugar Splitting Detected",
            "description": "Found 4 types of sugar: Invert Syrup, Dextrose, Maltodextrin, Fructose...",
            "category": "Sugar"
        },
        {
            "title": "Below Wholesale Price",
            "description": "Retail ₹299/kg vs wholesale ₹350/kg suggests adulteration.",
            "category": "OSINT"
        }
    ],
    "upf_score": 6,
    "fssai_number": "10014011000123",
    "legal_draft_available": true,
    "legal_draft_text": "To: The District Consumer Forum..."
}
```

### POST /api/v1/scan/link
```json
// Request
{
    "url": "https://www.bigbasket.com/pd/40123456/product-name/"
}
```

---

## 🏗️ Project Structure

```
OpenLabel/
├── main.py                  # FastAPI app entry point
├── config.py                # Environment settings
├── database.py              # Supabase client init
├── auth.py                  # JWT auth dependencies
├── requirements.txt
├── .env.example
├── .gitignore
│
├── models/
│   ├── schemas.py           # API request/response models
│   └── ai_schemas.py        # Gemini output schemas + system prompt
│
├── routers/
│   ├── scan.py              # POST /scan/image, POST /scan/link
│   ├── reports.py           # GET /reports, GET /reports/{id}
│   └── users.py             # GET/PUT /profile
│
├── services/
│   ├── ai_engine.py         # Gemini 2.0 Flash integration
│   ├── scraper.py           # E-commerce product scraper
│   ├── fssai_validator.py   # FSSAI 14-digit validator
│   └── osint_data.py        # Wholesale price OSINT
│
└── data/
    └── wholesale_prices.json # Commodity reference prices
```

---

## 🔗 Integration Notes

### For Member 1 (Flutter App)
- Send images as **base64-encoded JPEG/PNG** in the request body
- Use Supabase Auth to get the JWT token, pass in `Authorization: Bearer <token>` header
- Scan endpoints work **without auth** too (anonymous scans), but report history needs auth

### For Member 3 (AI Prompts)
- The system prompt is embedded in `models/ai_schemas.py`
- Gemini returns **structured JSON** matching the `OpenLabelReport` schema
- To modify AI behavior, update `TECH_JUSTICE_SYSTEM_PROMPT` in `ai_schemas.py`

---

## License
This project is for educational purposes as part of the OpenLabel Consumer OSINT initiative.
"# OpenLabel" 
