"""
╔══════════════════════════════════════════════════════════════╗
║                    🛡️ OpenLabel Engine Room                  ║
║              FastAPI Backend — Member 2 Service              ║
║                                                              ║
║  The central nervous system connecting:                      ║
║  Flutter App (M1) ↔ This API ↔ Gemini AI (M3) ↔ Supabase   ║
╚══════════════════════════════════════════════════════════════╝
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from config import get_settings
from models.schemas import HealthResponse
from routers import scan, reports, users

# ── Logging Setup ────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("openlabel")


# ── Application Lifespan ─────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("🚀 OpenLabel Engine Room starting up...")
    logger.info(f"📡 Supabase URL: {get_settings().supabase_url}")
    logger.info(f"🤖 Gemini AI: Ready")
    logger.info(f"📄 API Docs: http://{get_settings().app_host}:{get_settings().app_port}/docs")
    yield
    logger.info("🛑 OpenLabel Engine Room shutting down...")


# ── FastAPI App ──────────────────────────────────────────────
app = FastAPI(
    title="OpenLabel — Engine Room API",
    description=(
        "**Consumer OSINT Backend** that unmasks deceptive food labeling, "
        "calculates economic fraud in pricing, and generates instant legal drafts "
        "for consumer rights.\n\n"
        "### Core Features\n"
        "- 🏷️ **Label-Logic**: OCR & Vision AI to detect split-sugar deception\n"
        "- 💰 **Adulter-Arb**: Economic fraud detection via wholesale price comparison\n"
        "- ✅ **OrganicTruth**: FSSAI license number validation\n"
        "- ⚖️ **Tech-Justice**: Auto-generated legal complaints\n\n"
        "### Integration\n"
        "- **Input**: Flutter App (Member 1) sends images/URLs\n"
        "- **Processing**: Gemini 2.0 Flash AI (Member 3 prompt)\n"
        "- **Storage**: Supabase PostgreSQL\n"
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS Middleware ──────────────────────────────────────────
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Include Routers ──────────────────────────────────────────
app.include_router(scan.router, prefix="/api/v1")
app.include_router(reports.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")


# ── Health Check ─────────────────────────────────────────────
@app.get(
    "/",
    response_model=HealthResponse,
    tags=["🏥 Health"],
    summary="Health check",
)
async def health_check():
    """Returns service health status."""
    return HealthResponse()


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["🏥 Health"],
    summary="Health check (alias)",
)
async def health_check_alias():
    """Alias health check endpoint."""
    return HealthResponse()


@app.get("/demo", include_in_schema=False)
async def demo_page():
    """
    Simple HTML demo UI (no dependencies) for quick manual testing.

    Usage:
    - Open http://127.0.0.1:8000/demo
    - Upload image(s)
    - Click "Analyze"
    """

    # Keep HTML embedded so users can run instantly.
    html = r"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>OpenLabel Demo</title>
    <style>
      body { font-family: Arial, sans-serif; margin: 20px; }
      input, button, textarea { font-size: 14px; }
      textarea { width: 100%; height: 200px; }
      .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-start; }
      .col { flex: 1; min-width: 260px; }
      pre { background: #111; color: #0f0; padding: 12px; overflow: auto; }
      .muted { color: #666; font-size: 12px; }
      img.preview { max-width: 100%; border: 1px solid #ddd; border-radius: 6px; }
      button { padding: 10px 14px; cursor: pointer; }
    </style>
  </head>
  <body>
    <h2>OpenLabel Demo</h2>
    <p class="muted">
      This page sends your image(s) to <code>/api/v1/scan/image</code> or
      <code>/api/v1/scan/dual-image</code> and displays the JSON response.
    </p>

    <div class="row">
      <div class="col">
        <h3>Single image (front)</h3>
        <input type="file" id="frontFile" accept="image/*" />
        <div style="height:8px"></div>
        <img class="preview" id="frontPreview" />
        <div style="height:8px"></div>
        <label>Product name (optional)</label>
        <input type="text" id="productName" placeholder="e.g. Milk" style="width:100%" />
        <div style="height:8px"></div>
        <label>Retail price (optional)</label>
        <input type="number" id="retailPrice" placeholder="e.g. 60" style="width:100%" />
        <div style="height:12px"></div>
        <button onclick="analyzeSingle()">Analyze (Image)</button>
      </div>

      <div class="col">
        <h3>Dual image (front + back)</h3>
        <input type="file" id="dualFrontFile" accept="image/*" />
        <input type="file" id="dualBackFile" accept="image/*" style="margin-top:8px" />
        <div style="height:8px"></div>
        <img class="preview" id="dualFrontPreview" />
        <img class="preview" id="dualBackPreview" style="margin-top:8px" />
        <div style="height:12px"></div>
        <button onclick="analyzeDual()">Analyze (Dual Image)</button>
      </div>
    </div>

    <h3>Response</h3>
    <pre id="out">{"status":"idle"}</pre>

    <script>
      function fileToBase64(file) {
        return new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onerror = () => reject(reader.error);
          reader.onload = () => resolve(reader.result); // data:image/...;base64,...
          reader.readAsDataURL(file);
        });
      }

      function setPreview(fileInput, imgEl) {
        const f = fileInput.files && fileInput.files[0];
        if (!f) return;
        imgEl.src = URL.createObjectURL(f);
      }

      document.getElementById('frontFile').addEventListener('change', (e) => {
        setPreview(e.target, document.getElementById('frontPreview'));
      });
      document.getElementById('dualFrontFile').addEventListener('change', (e) => {
        setPreview(e.target, document.getElementById('dualFrontPreview'));
      });
      document.getElementById('dualBackFile').addEventListener('change', (e) => {
        setPreview(e.target, document.getElementById('dualBackPreview'));
      });

      async function analyzeSingle() {
        const out = document.getElementById('out');
        out.textContent = JSON.stringify({status:'working'}, null, 2);

        const frontFile = document.getElementById('frontFile').files[0];
        if (!frontFile) {
          out.textContent = JSON.stringify({error:'Select a front image file'}, null, 2);
          return;
        }

        const image_base64 = await fileToBase64(frontFile);
        const product_name = document.getElementById('productName').value || null;
        const retail_price_raw = document.getElementById('retailPrice').value;
        const retail_price = retail_price_raw ? Number(retail_price_raw) : null;

        const res = await fetch('/api/v1/scan/image', {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({
            image_base64,
            product_name,
            retail_price
          })
        });

        const json = await res.json().catch(() => ({}));
        out.textContent = JSON.stringify({http_status: res.status, response: json}, null, 2);
      }

      async function analyzeDual() {
        const out = document.getElementById('out');
        out.textContent = JSON.stringify({status:'working'}, null, 2);

        const dualFrontFile = document.getElementById('dualFrontFile').files[0];
        const dualBackFile = document.getElementById('dualBackFile').files[0];
        if (!dualFrontFile || !dualBackFile) {
          out.textContent = JSON.stringify({error:'Select both front and back image files'}, null, 2);
          return;
        }

        const front_image_base64 = await fileToBase64(dualFrontFile);
        const back_image_base64 = await fileToBase64(dualBackFile);
        const product_name = document.getElementById('productName').value || null;
        const retail_price_raw = document.getElementById('retailPrice').value;
        const retail_price = retail_price_raw ? Number(retail_price_raw) : null;

        const res = await fetch('/api/v1/scan/dual-image', {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({
            front_image_base64,
            back_image_base64,
            product_name,
            retail_price
          })
        });

        const json = await res.json().catch(() => ({}));
        out.textContent = JSON.stringify({http_status: res.status, response: json}, null, 2);
      }
    </script>
  </body>
</html>
    """
    return HTMLResponse(html)


# ═══════════════════════════════════════════════════════════
#  Run with: uvicorn main:app --reload --port 8000
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
    )
