import os
import re
import uuid
import smtplib
from email.message import EmailMessage
from typing import List, Optional

import httpx
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

app = FastAPI()


# -----------------------------
# Config (ENV)
# -----------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "lead-images")

BUSINESS_EMAIL_TO = os.getenv("BUSINESS_EMAIL_TO", "")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)

BUSINESS_WHATSAPP_NUMBER = os.getenv("BUSINESS_WHATSAPP_NUMBER", "573001112233")  # +57..., sin '+'

MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "5"))
MAX_IMAGE_BYTES = MAX_IMAGE_MB * 1024 * 1024

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _sb_headers() -> dict:
    """Headers para PostgREST y Storage usando service_role (solo backend)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Faltan SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY en variables de entorno.")
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }


# -----------------------------
# Helpers
# -----------------------------
def _basic_email_ok(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip(), re.I))


def _clean_phone(s: str) -> str:
    s = s.strip()
    return re.sub(r"[^\d\+\s\(\)]", "", s)


def _truthy(s: str) -> bool:
    return str(s).strip().lower() in {"1", "true", "yes", "si", "sí", "y"}


def _make_public_url(bucket: str, path: str) -> str:
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"


async def _insert_lead(data: dict) -> dict:
    url = f"{SUPABASE_URL}/rest/v1/leads"
    headers = _sb_headers()
    headers["prefer"] = "return=representation"  # devuelve el registro insertado

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers=headers, json=[data])
        if r.status_code >= 300:
            raise HTTPException(status_code=500, detail=f"Error insertando lead: {r.status_code} {r.text}")

        rows = r.json()
        if not rows:
            raise HTTPException(status_code=500, detail="Supabase no devolvió el lead insertado.")
        return rows[0]


async def _insert_lead_image(row: dict) -> None:
    url = f"{SUPABASE_URL}/rest/v1/lead_images"
    headers = _sb_headers()
    headers["prefer"] = "return=minimal"

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers=headers, json=[row])
        if r.status_code >= 300:
            raise HTTPException(status_code=500, detail=f"Error insertando lead_image: {r.status_code} {r.text}")


async def _upload_to_storage(bucket: str, path: str, content: bytes, content_type: str) -> None:
    # Upload simple a Supabase Storage
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}"
    headers = _sb_headers()
    headers["content-type"] = content_type
    headers["x-upsert"] = "true"

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, content=content)
        if r.status_code >= 300:
            raise HTTPException(status_code=500, detail=f"Error subiendo a Storage: {r.status_code} {r.text}")


def _send_email_sync(subject: str, body: str) -> None:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD and SMTP_FROM and BUSINESS_EMAIL_TO):
        raise RuntimeError("Faltan variables SMTP_* o BUSINESS_EMAIL_TO.")

    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = BUSINESS_EMAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


# -----------------------------
# Routes
# -----------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    # IMPORTANTE: NO es f-string para evitar conflictos con { } y ${ } del HTML/JS
    html = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Skins - Solicitud de diseño</title>
  <style>
    :root {
      --bg: #0b1220;
      --panel: #0f1b33;
      --bubble-bot: #14264a;
      --bubble-user: #1d3b7a;
      --text: #e9eefc;
      --muted: #a9b6d8;
      --accent: #4aa3ff;
      --danger: #ff5b6e;
      --ok: #38d39f;
      --card: #0f1b33;
      --border: rgba(255,255,255,.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      background: radial-gradient(1200px 600px at 20% 0%, #14264a 0%, var(--bg) 55%);
      color: var(--text);
    }
    .wrap {
      max-width: 980px;
      margin: 0 auto;
      padding: 16px;
      display: grid;
      grid-template-columns: 1fr;
      gap: 12px;
    }
    header {
      display:flex; align-items:center; justify-content:space-between;
      gap: 12px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 14px;
      background: rgba(15,27,51,.6);
      backdrop-filter: blur(8px);
    }
    header .title { font-weight: 700; letter-spacing: .2px; }
    header .hint { color: var(--muted); font-size: 13px; }

    .chat {
      border: 1px solid var(--border);
      border-radius: 16px;
      background: rgba(15,27,51,.6);
      backdrop-filter: blur(8px);
      overflow: hidden;
      display: grid;
      grid-template-rows: 1fr auto;
      min-height: 72vh;
    }
    .msgs {
      padding: 14px;
      overflow:auto;
      max-height: 72vh;
    }
    .bubble {
      max-width: 82%;
      padding: 10px 12px;
      border-radius: 14px;
      margin: 8px 0;
      border: 1px solid var(--border);
      line-height: 1.35;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .bot { background: var(--bubble-bot); border-top-left-radius: 6px; }
    .user { background: var(--bubble-user); margin-left: auto; border-top-right-radius: 6px; }

    .muted { color: var(--muted); font-size: 13px; }
    .controls {
      padding: 12px;
      border-top: 1px solid var(--border);
      display: grid;
      gap: 10px;
      background: rgba(10,18,32,.7);
    }
    .row { display:flex; gap: 8px; flex-wrap: wrap; }
    input, select, textarea {
      width: 100%;
      padding: 11px 12px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: rgba(0,0,0,.18);
      color: var(--text);
      outline: none;
    }
    textarea { min-height: 90px; resize: vertical; }

    .btn {
      cursor:pointer;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 10px 12px;
      background: rgba(74,163,255,.15);
      color: var(--text);
      font-weight: 650;
      text-decoration: none;
      display:inline-flex;
      align-items:center;
      justify-content:center;
    }
    .btn:hover { border-color: rgba(74,163,255,.6); }
    .btn.secondary { background: rgba(255,255,255,.06); }
    .btn.ok { background: rgba(56,211,159,.18); }

    .pill {
      padding: 8px 10px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,.06);
      cursor:pointer;
      font-weight: 600;
    }
    .pill:hover { border-color: rgba(74,163,255,.6); }

    .grid {
      display:grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 10px;
      margin-top: 6px;
    }
    .card {
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow:hidden;
      background: rgba(0,0,0,.18);
    }
    .card img {
      width: 100%;
      height: 140px;
      object-fit: cover;
      display:block;
    }
    .card .cap {
      padding: 8px 10px;
      color: var(--muted);
      font-size: 12px;
    }

    .imgblock {
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 10px;
      background: rgba(0,0,0,.14);
      display:grid;
      gap: 8px;
    }
    .oktext { color: var(--ok); font-weight: 650; }
    .tiny { font-size: 12px; color: var(--muted); }

    /* “Ver precio” SOLO web (desktop) */
    #btnPrice { display: inline-flex; }
    @media (max-width: 768px) {
      #btnPrice { display: none; }
      .grid { grid-template-columns: 1fr; }
      .bubble { max-width: 92%; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <div class="title">Solicitud de skins (MVP)</div>
        <div class="hint">Una sola página · estilo chat-formulario</div>
      </div>
      <div class="row">
        <button class="btn secondary" id="btnPrice" type="button">Ver precio</button>
        <a class="btn ok" id="btnWhatsApp" target="_blank" rel="noopener">Contactar por WhatsApp</a>
      </div>
    </header>

    <section class="chat">
      <div class="msgs" id="msgs"></div>
      <div class="controls" id="controls"></div>
    </section>
  </div>

<script>
  // -----------------------------
  // CONFIG: cambia aquí tus imágenes y precios
  // -----------------------------
  const GALLERY_SET_1 = [
    "https://images.unsplash.com/photo-1511512578047-dfb367046420?auto=format&fit=crop&w=900&q=70",
    "https://images.unsplash.com/photo-1612287230202-1ff1d85d1bdf?auto=format&fit=crop&w=900&q=70",
    "https://images.unsplash.com/photo-1605902711622-cfb43c44367f?auto=format&fit=crop&w=900&q=70",
    "https://images.unsplash.com/photo-1612815154858-60aa4c59eaa6?auto=format&fit=crop&w=900&q=70"
  ];

  const GALLERY_SET_2 = [
    "https://images.unsplash.com/photo-1580128637423-1e6c0d9e8d25?auto=format&fit=crop&w=900&q=70",
    "https://images.unsplash.com/photo-1603481546579-65d935ba9cdd?auto=format&fit=crop&w=900&q=70",
    "https://images.unsplash.com/photo-1550745165-9bc0b252726f?auto=format&fit=crop&w=900&q=70",
    "https://images.unsplash.com/photo-1550745166-9bc0b252726f?auto=format&fit=crop&w=900&q=70"
  ];

  const PRICES = [
    { name: "Skin estándar (placeholder)", value: "$80.000 COP" },
    { name: "Skin premium (placeholder)", value: "$120.000 COP" },
    { name: "Personalizado (placeholder)", value: "$160.000 COP" }
  ];

  // Se inyecta desde backend (ENV) por reemplazo de string:
  const BUSINESS_WA_NUMBER = "__BUSINESS_WA_NUMBER__";

  // -----------------------------
  // State
  // -----------------------------
  const state = {
    name: "",
    email: "",
    whatsapp: "",
    console: "",
    design_choice: "",     // "view_designs" | "custom"
    has_design: "no",      // "yes" si sube imágenes
    gallery_page: 0,       // 0 none, 1 set1, 2 set2
    custom_blocks: []      // { fileEl, detailsEl }
  };

  const msgs = document.getElementById("msgs");
  const controls = document.getElementById("controls");
  const btnWA = document.getElementById("btnWhatsApp");
  const btnPrice = document.getElementById("btnPrice");

  function scrollDown() {
    msgs.scrollTop = msgs.scrollHeight;
  }

  function addBubble(text, who="bot") {
    const div = document.createElement("div");
    div.className = "bubble " + (who === "user" ? "user" : "bot");
    div.textContent = text;
    msgs.appendChild(div);
    scrollDown();
  }

  function setControls(html) {
    controls.innerHTML = html;
  }

  function updateWhatsAppLink() {
    const name = state.name || "Hola";
    const consola = state.console || "una consola";
    const msg = encodeURIComponent(`Hola, soy ${name}. Quiero un diseño para ${consola}.`);
    btnWA.href = `https://wa.me/${BUSINESS_WA_NUMBER}?text=${msg}`;
  }

  function showError(msg) {
    addBubble("❌ " + msg, "bot");
  }

  function start() {
    addBubble("👋 ¡Hola! Te voy a pedir unos datos para tu skin.");
    updateWhatsAppLink();
    stepName();
  }

  function stepName() {
    addBubble("¿Cuál es tu nombre?");
    setControls(`
      <div class="row">
        <input id="inName" placeholder="Tu nombre" autocomplete="name" />
        <button class="btn" id="btnNext">Continuar</button>
      </div>
      <div class="tiny">Tip: esto sirve para prellenar WhatsApp y el correo.</div>
    `);
    document.getElementById("btnNext").onclick = () => {
      const v = document.getElementById("inName").value.trim();
      if (!v) return showError("Escribe tu nombre.");
      state.name = v;
      addBubble(v, "user");
      updateWhatsAppLink();
      stepEmail();
    };
  }

  function stepEmail() {
    addBubble("Perfecto. ¿Tu correo?");
    setControls(`
      <div class="row">
        <input id="inEmail" placeholder="correo@ejemplo.com" autocomplete="email" />
        <button class="btn" id="btnNext">Continuar</button>
      </div>
    `);
    document.getElementById("btnNext").onclick = () => {
      const v = document.getElementById("inEmail").value.trim();
      if (!v || !v.includes("@")) return showError("Escribe un correo válido.");
      state.email = v;
      addBubble(v, "user");
      stepWhatsApp();
    };
  }

  function stepWhatsApp() {
    addBubble("¿Tu WhatsApp (con indicativo si puedes)?");
    setControls(`
      <div class="row">
        <input id="inWA" placeholder="+57 3xx xxx xxxx" autocomplete="tel" />
        <button class="btn" id="btnNext">Continuar</button>
      </div>
    `);
    document.getElementById("btnNext").onclick = () => {
      const v = document.getElementById("inWA").value.trim();
      if (!v) return showError("Escribe tu número de WhatsApp.");
      state.whatsapp = v;
      addBubble(v, "user");
      stepConsole();
    };
  }

  function stepConsole() {
    addBubble("¿Para cuál consola es el skin?");
    setControls(`
      <div class="row">
        <select id="selConsole">
          <option value="">Selecciona…</option>
          <option>PS4</option>
          <option>PS5</option>
          <option>XBOX</option>
          <option>Switch</option>
          <option value="other">Otra</option>
        </select>
        <input id="inOther" placeholder="Escribe la consola" style="display:none;" />
        <button class="btn" id="btnNext">Continuar</button>
      </div>
    `);

    const sel = document.getElementById("selConsole");
    const inOther = document.getElementById("inOther");
    sel.onchange = () => {
      inOther.style.display = sel.value === "other" ? "block" : "none";
    };

    document.getElementById("btnNext").onclick = () => {
      let v = sel.value;
      if (!v) return showError("Selecciona una consola.");
      if (v === "other") {
        v = inOther.value.trim();
        if (!v) return showError("Escribe cuál consola es.");
      }
      state.console = v;
      addBubble(v, "user");
      updateWhatsAppLink();
      stepDesignChoice();
    };
  }

  function stepDesignChoice() {
    addBubble("¿Quieres ver diseños primero o prefieres uno personalizado?");
    setControls(`
      <div class="row">
        <button class="pill" id="btnView">Ver diseños</button>
        <button class="pill" id="btnCustom">Quiero personalizado</button>
      </div>
      <div class="muted">Si ves diseños, podrás dar “Ver más” y luego pasar a personalizado.</div>
    `);

    document.getElementById("btnView").onclick = () => {
      state.design_choice = "view_designs";
      addBubble("Quiero ver diseños", "user");
      showGallery1();
    };
    document.getElementById("btnCustom").onclick = () => {
      state.design_choice = "custom";
      state.has_design = "yes";
      addBubble("Quiero personalizado", "user");
      showCustomUpload();
    };
  }

  function galleryHtml(urls) {
    return `
      <div class="grid">
        ${urls.map((u, i) => `
          <div class="card">
            <img src="${u}" alt="Diseño ${i+1}" loading="lazy" />
            <div class="cap">Diseño ${i+1}</div>
          </div>
        `).join("")}
      </div>
    `;
  }

  function showGallery1() {
    state.gallery_page = 1;
    addBubble("Aquí tienes algunos diseños (tanda 1):");
    const div = document.createElement("div");
    div.className = "bubble bot";
    div.innerHTML = galleryHtml(GALLERY_SET_1);
    msgs.appendChild(div);
    scrollDown();

    setControls(`
      <div class="row">
        <button class="btn" id="btnMore">Ver más</button>
        <button class="btn secondary" id="btnSkip">Me voy a personalizado</button>
      </div>
    `);

    document.getElementById("btnMore").onclick = () => showGallery2();
    document.getElementById("btnSkip").onclick = () => {
      state.design_choice = "custom";
      state.has_design = "yes";
      addBubble("Me voy a personalizado", "user");
      showCustomUpload();
    };
  }

  function showGallery2() {
    state.gallery_page = 2;
    addBubble("Tanda 2 (más diseños):");
    const div = document.createElement("div");
    div.className = "bubble bot";
    div.innerHTML = galleryHtml(GALLERY_SET_2);
    msgs.appendChild(div);
    scrollDown();

    setControls(`
      <div class="row">
        <button class="btn ok" id="btnCustomNow">Quiero personalizado</button>
      </div>
      <div class="muted">Ahora subes tus imágenes + detalles.</div>
    `);

    document.getElementById("btnCustomNow").onclick = () => {
      state.design_choice = "custom";
      state.has_design = "yes";
      addBubble("Quiero personalizado", "user");
      showCustomUpload();
    };
  }

  function addImageBlock() {
    const idx = state.custom_blocks.length + 1;
    const block = document.createElement("div");
    block.className = "imgblock";
    block.innerHTML = `
      <div><strong>Imagen ${idx}</strong></div>
      <input type="file" accept="image/*" class="inFile" />
      <textarea class="inDetails" placeholder="Detalles obligatorios: colores, estilo, texto, ubicaciones, referencias..."></textarea>
      <div class="tiny">Cada imagen debe tener detalles. Máx. tamaño por imagen lo valida el servidor.</div>
    `;
    document.getElementById("customArea").appendChild(block);
    state.custom_blocks.push({
      fileEl: block.querySelector(".inFile"),
      detailsEl: block.querySelector(".inDetails")
    });
  }

  function showCustomUpload() {
    addBubble("Listo. Sube una o varias imágenes y describe los detalles de cada una.");
    setControls(`
      <div id="customArea" style="display:grid; gap:10px;"></div>

      <div class="row">
        <button class="btn secondary" id="btnAddImg" type="button">Agregar otra imagen</button>
        <button class="btn" id="btnSubmit" type="button">Enviar solicitud</button>
      </div>
      <div class="muted">Validación mínima: si es personalizado, debe haber al menos 1 imagen y 1 detalle por imagen.</div>
      <div id="status" class="tiny"></div>
    `);

    state.custom_blocks = [];
    addImageBlock();

    document.getElementById("btnAddImg").onclick = () => addImageBlock();
    document.getElementById("btnSubmit").onclick = () => submitLead();
  }

  async function submitLead() {
    const status = document.getElementById("status");
    status.textContent = "Enviando...";

    const fd = new FormData();
    fd.append("name", state.name);
    fd.append("email", state.email);
    fd.append("whatsapp", state.whatsapp);
    fd.append("console", state.console);
    fd.append("design_choice", state.design_choice || "");
    fd.append("has_design", state.has_design || "no");
    fd.append("whatsapp_prefill", `Hola, soy ${state.name}. Quiero un diseño para ${state.console}.`);

    if (state.has_design === "yes") {
      let anyFile = false;
      for (const b of state.custom_blocks) {
        const f = b.fileEl.files && b.fileEl.files[0];
        const d = (b.detailsEl.value || "").trim();
        if (f) {
          anyFile = true;
          fd.append("images", f);
          fd.append("details", d);
        }
      }
      if (!anyFile) {
        status.textContent = "";
        return showError("Si es personalizado, sube al menos 1 imagen.");
      }
    }

    try {
      const res = await fetch("/submit", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) {
        status.textContent = "";
        return showError(data.detail || "Error enviando.");
      }
      addBubble("✅ Solicitud enviada. Te contactaremos pronto.", "bot");
      status.innerHTML = `<span class="oktext">OK</span> · Lead ID: ${data.lead_id}`;
      updateWhatsAppLink();
    } catch (e) {
      status.textContent = "";
      showError("Error de red. Intenta de nuevo.");
    }
  }

  btnPrice.onclick = () => {
    addBubble("Precios (placeholder):");
    const lines = PRICES.map(p => `- ${p.name}: ${p.value}`).join("\\n");
    addBubble(lines, "bot");
  };

  start();
</script>

</body>
</html>
"""

    html = html.replace("__BUSINESS_WA_NUMBER__", BUSINESS_WHATSAPP_NUMBER)
    return HTMLResponse(html)


@app.post("/submit")
async def submit(
    name: str = Form(...),
    email: str = Form(...),
    whatsapp: str = Form(...),
    console: str = Form(...),
    design_choice: str = Form(""),
    has_design: str = Form("no"),
    whatsapp_prefill: str = Form(""),
    details: Optional[List[str]] = Form(None),
    images: Optional[List[UploadFile]] = File(None),
):
    # Validaciones básicas
    name = name.strip()
    email = email.strip()
    whatsapp = _clean_phone(whatsapp)
    console = console.strip()

    if not name:
        raise HTTPException(status_code=400, detail="Nombre es obligatorio.")
    if not _basic_email_ok(email):
        raise HTTPException(status_code=400, detail="Correo inválido.")
    if not whatsapp:
        raise HTTPException(status_code=400, detail="WhatsApp es obligatorio.")
    if not console:
        raise HTTPException(status_code=400, detail="Consola es obligatoria.")

    has_design_bool = _truthy(has_design)
    details = details or []
    images = images or []

    # Regla: si has_design=yes -> mínimo 1 imagen y 1 detalle por imagen
    if has_design_bool:
        if len(images) < 1:
            raise HTTPException(status_code=400, detail="Debes subir al menos 1 imagen.")
        if len(details) != len(images):
            raise HTTPException(status_code=400, detail="Cada imagen debe tener su detalle (mismo número).")
        for d in details:
            if not str(d).strip():
                raise HTTPException(status_code=400, detail="Cada imagen debe tener detalles (no vacío).")

    # Inserta lead
    lead_data = {
        "name": name,
        "email": email,
        "whatsapp": whatsapp,
        "console": console,
        "design_choice": (design_choice or "").strip(),
        "has_design": has_design_bool,
        "whatsapp_prefill": (whatsapp_prefill or "").strip(),
    }
    lead = await _insert_lead(lead_data)
    lead_id = lead["id"]

    uploaded_rows: List[dict] = []

    # Sube imágenes + inserta lead_images
    if has_design_bool:
        for idx, up in enumerate(images):
            ct = (up.content_type or "").lower().strip()
            if ct not in ALLOWED_IMAGE_TYPES:
                raise HTTPException(status_code=400, detail=f"Tipo de imagen no permitido: {ct}. Usa JPG/PNG/WEBP.")

            content = await up.read()
            size_bytes = len(content)

            if size_bytes <= 0:
                raise HTTPException(status_code=400, detail="Una imagen viene vacía.")
            if size_bytes > MAX_IMAGE_BYTES:
                raise HTTPException(status_code=400, detail=f"Imagen supera el máximo permitido ({MAX_IMAGE_MB}MB).")

            ext = ALLOWED_IMAGE_TYPES[ct]

            safe_name = (up.filename or "image").strip()
            safe_name = re.sub(r"[^a-zA-Z0-9\.\-_]+", "_", safe_name)[:80]

            path = f"{lead_id}/{uuid.uuid4().hex}{ext}"
            await _upload_to_storage(SUPABASE_BUCKET, path, content, ct)

            public_url = _make_public_url(SUPABASE_BUCKET, path)

            row = {
                "lead_id": lead_id,
                "storage_bucket": SUPABASE_BUCKET,
                "storage_path": path,
                "public_url": public_url,
                "original_filename": safe_name,
                "content_type": ct,
                "size_bytes": size_bytes,
                "details": details[idx].strip(),
            }
            await _insert_lead_image(row)
            uploaded_rows.append(row)

    # Email al negocio
    subject = f"Nuevo lead skins: {name} ({console})"
    lines = []
    lines.append("Nuevo lead recibido\n")
    lines.append(f"Nombre: {name}")
    lines.append(f"Correo: {email}")
    lines.append(f"WhatsApp: {whatsapp}")
    lines.append(f"Consola: {console}")
    lines.append(f"Design choice: {design_choice}")
    lines.append(f"Has design/images: {has_design_bool}")
    if whatsapp_prefill:
        lines.append(f"Mensaje WhatsApp (prefill): {whatsapp_prefill}")

    if uploaded_rows:
        lines.append("\nImágenes:")
        for i, r in enumerate(uploaded_rows, start=1):
            lines.append(f"\n#{i}")
            lines.append(f"Link: {r['public_url']}")
            lines.append(f"Detalles: {r['details']}")
            lines.append(f"Archivo: {r.get('original_filename','')}")
            lines.append(f"Tipo: {r.get('content_type','')} · Tamaño: {r.get('size_bytes',0)} bytes")
    else:
        lines.append("\n(No adjuntó imágenes)")

    body = "\n".join(lines)

    try:
        await run_in_threadpool(_send_email_sync, subject, body)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lead guardado pero falló el correo: {str(e)}")

    return JSONResponse({"ok": True, "lead_id": lead_id})
