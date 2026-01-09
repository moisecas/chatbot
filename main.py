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

BUSINESS_WHATSAPP_NUMBER = os.getenv("BUSINESS_WHATSAPP_NUMBER", "573001112233")
MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "5"))
MAX_IMAGE_BYTES = MAX_IMAGE_MB * 1024 * 1024

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

def _sb_headers() -> dict:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Faltan credenciales de Supabase.")
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }

# -----------------------------
# Helpers & Database
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
    headers["prefer"] = "return=representation"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers=headers, json=[data])
        if r.status_code >= 300:
            raise HTTPException(status_code=500, detail=f"Error DB: {r.text}")
        rows = r.json()
        return rows[0] if rows else {}

async def _insert_lead_image(row: dict) -> None:
    url = f"{SUPABASE_URL}/rest/v1/lead_images"
    headers = _sb_headers()
    headers["prefer"] = "return=minimal"
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(url, headers=headers, json=[row])

async def _upload_to_storage(bucket: str, path: str, content: bytes, content_type: str) -> None:
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}"
    headers = _sb_headers()
    headers["content-type"] = content_type
    headers["x-upsert"] = "true"
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, content=content)
        if r.status_code >= 300:
            raise HTTPException(status_code=500, detail=f"Error Storage: {r.text}")

def _send_email_sync(subject: str, body: str) -> None:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
        return # O lanzar error si es crítico
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
    html = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
  <title>Skins - Diseño Personalizado</title>
  <style>
    :root {
      /* Ajuste de colores para mejor contraste */
      --bg: #0b1220;
      --panel: #0f1b33;
      --bubble-bot: #1e293b; 
      --bubble-user: #2563eb;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --accent: #3b82f6;
      --danger: #ef4444;
      --ok: #22c55e;
      --border: rgba(255,255,255,.15);
      
      /* Inputs más claros para legibilidad */
      --input-bg: #e2e8f0;
      --input-text: #0f172a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, sans-serif;
      background: radial-gradient(circle at 50% 0%, #1e293b 0%, var(--bg) 80%);
      color: var(--text);
      height: 100vh;
      display: flex;
      flex-direction: column;
    }
    .wrap {
      max-width: 600px;
      margin: 0 auto;
      width: 100%;
      height: 100%;
      display: flex;
      flex-direction: column;
      position: relative;
    }
    
    /* Header limpio sin WhatsApp */
    header {
      flex: 0 0 auto;
      display: flex; align-items: center; justify-content: space-between;
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      background: rgba(15,27,51,.95);
      backdrop-filter: blur(10px);
      z-index: 10;
    }
    header .title { font-weight: 700; font-size: 1.1rem; }
    header .hint { color: var(--muted); font-size: 0.8rem; }

    /* Chat Area */
    .chat {
      flex: 1;
      overflow-y: auto;
      padding: 16px;
      padding-bottom: 20px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      scroll-behavior: smooth;
    }
    
    .bubble {
      max-width: 85%;
      padding: 12px 14px;
      border-radius: 16px;
      line-height: 1.4;
      white-space: pre-wrap;
      font-size: 15px;
      animation: popIn 0.3s ease-out;
    }
    @keyframes popIn { from{opacity:0; transform:translateY(5px);} to{opacity:1; transform:translateY(0);} }
    
    .bot { background: var(--bubble-bot); border-bottom-left-radius: 4px; color: #e2e8f0; }
    .user { background: var(--bubble-user); margin-left: auto; border-bottom-right-radius: 4px; color: white; }

    /* Controls Area */
    .controls {
      flex: 0 0 auto;
      padding: 12px 16px;
      background: #111827;
      border-top: 1px solid var(--border);
    }
    
    /* Inputs mejorados (Fondo claro, letra oscura) */
    input, select, textarea {
      width: 100%;
      padding: 12px 14px;
      border-radius: 12px;
      border: 2px solid transparent;
      background: var(--input-bg);
      color: var(--input-text);
      font-size: 16px; /* Evita zoom en iOS */
      outline: none;
      transition: border-color 0.2s;
    }
    input:focus, select:focus, textarea:focus {
      border-color: var(--accent);
    }
    textarea { min-height: 80px; resize: none; font-family: inherit; }

    /* Botones */
    .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .btn {
      flex: 1;
      cursor: pointer;
      border: none;
      border-radius: 12px;
      padding: 12px;
      background: var(--accent);
      color: white;
      font-weight: 600;
      text-align: center;
      text-decoration: none;
      font-size: 15px;
    }
    .btn:active { transform: scale(0.98); }
    .btn.secondary { background: #334155; color: #f8fafc; }
    .btn.whatsapp { background: #25D366; color: white; }
    .btn.danger { background: var(--danger); }

    .pill-wrap { display: flex; gap: 8px; flex-wrap: wrap; }
    .pill {
      background: rgba(255,255,255,0.1);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 8px 16px;
      border-radius: 20px;
      cursor: pointer;
      font-size: 14px;
    }
    .pill:hover { background: rgba(255,255,255,0.2); }

    /* Grid de Imágenes */
    .grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 10px;
      margin-top: 8px;
    }
    .card {
      border-radius: 12px;
      overflow: hidden;
      background: #000;
      position: relative;
      border: 2px solid transparent;
      cursor: pointer;
    }
    .card.selected {
      border-color: var(--ok);
      box-shadow: 0 0 10px rgba(34, 197, 94, 0.4);
    }
    .card img {
      width: 100%; height: 120px; object-fit: cover; display: block;
    }
    .card .cap {
      padding: 6px; font-size: 11px; text-align: center; color: #cbd5e1; background: rgba(0,0,0,0.6);
    }
    .card .check {
      position: absolute; top: 5px; right: 5px;
      background: var(--ok); color: black;
      width: 20px; height: 20px; border-radius: 50%;
      display: none; align-items: center; justify-content: center; font-weight: bold;
    }
    .card.selected .check { display: flex; }

    /* Upload Block */
    .imgblock {
      background: #1e293b;
      padding: 10px;
      border-radius: 12px;
      margin-bottom: 8px;
      border: 1px solid var(--border);
    }
    .imgblock label { font-size: 13px; color: var(--accent); font-weight: bold; display: block; margin-bottom: 4px; }
    
    .tiny { font-size: 12px; color: var(--muted); margin-top: 4px; }
    .hidden { display: none; }
    
    /* Botón Precio visible en todos */
    #btnPrice { display: inline-flex; font-size: 13px; padding: 6px 12px; height: auto; flex: 0; white-space: nowrap; }

  </style>
</head>
<body>

<div class="wrap">
  <header>
    <div>
      <div class="title">Diseña tu Skin</div>
      <div class="hint">Personaliza tu consola</div>
    </div>
    <button class="btn secondary" id="btnPrice" type="button">Ver precios</button>
  </header>

  <section class="chat" id="msgs"></section>
  
  <div class="controls" id="controls"></div>
</div>

<script>
  // -----------------------------
  // DATOS Y CONFIGURACIÓN
  // -----------------------------
  const MAX_BYTES = __MAX_BYTES__; // Inyectado
  const BUSINESS_WA = "__BUSINESS_WA_NUMBER__";
  
  // Categorías con placeholders (aquí puedes meter hasta 20 imágenes por lista)
  const GALLERIES = {
    "Anime": [
      "https://images.unsplash.com/photo-1623939012339-5b3dd892c57f?w=400&q=70",
      "https://images.unsplash.com/photo-1541562232579-512a21360020?w=400&q=70",
      "https://images.unsplash.com/photo-1578632767115-351597cf2477?w=400&q=70",
      "https://images.unsplash.com/photo-1607604276583-eef5f0b7e6d5?w=400&q=70"
    ],
    "Deportes": [
      "https://images.unsplash.com/photo-1579952363873-27f3bade9f55?w=400&q=70",
      "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?w=400&q=70",
      "https://images.unsplash.com/photo-1517649763962-0c623066013b?w=400&q=70"
    ],
    "Abstracto": [
      "https://images.unsplash.com/photo-1550684848-fac1c5b4e853?w=400&q=70",
      "https://images.unsplash.com/photo-1541701494587-cb58502866ab?w=400&q=70",
      "https://images.unsplash.com/photo-1550745165-9bc0b252726f?w=400&q=70"
    ]
  };

  const PRICES = [
    "Skin Estándar: $80.000 COP",
    "Skin Premium: $120.000 COP",
    "Diseño Personalizado: $160.000 COP"
  ];

  const STATE = {
    name: "",
    email: "",
    whatsapp: "",
    console: "",
    mode: "", // 'gallery' | 'custom'
    selected_design_url: "",
    custom_files: []
  };

  const msgs = document.getElementById("msgs");
  const controls = document.getElementById("controls");
  const btnPrice = document.getElementById("btnPrice");

  // -----------------------------
  // FUNCIONES BASE
  // -----------------------------
  function scrollBot() {
    setTimeout(() => { msgs.scrollTop = msgs.scrollHeight; }, 100);
  }

  function addBubble(text, who="bot", isHtml=false) {
    const div = document.createElement("div");
    div.className = "bubble " + who;
    if (isHtml) div.innerHTML = text;
    else div.textContent = text;
    msgs.appendChild(div);
    scrollBot();
  }

  function setControls(html) {
    controls.innerHTML = html;
    // Auto focus al primer input si existe
    const inp = controls.querySelector("input, select");
    if (inp) inp.focus();
  }

  function showError(msg) {
    addBubble("⚠️ " + msg, "bot");
  }

  // -----------------------------
  // FLUJO DEL CHAT
  // -----------------------------
  function start() {
    addBubble("👋 ¡Hola! Soy MoisoBot. Vamos a crear un skin brutal para tu consola.");
    setTimeout(askName, 600);
  }

  function askName() {
    addBubble("Para empezar, ¿cómo te llamas?");
    setControls(`
      <div class="row">
        <input id="inName" placeholder="Escribe tu nombre aquí..." autocomplete="name" />
        <button class="btn" onclick="handleName()">Siguiente</button>
      </div>
      <div class="tiny">⬇️ Escribe abajo</div>
    `);
  }

  window.handleName = () => {
    const v = document.getElementById("inName").value.trim();
    if (!v) return showError("Necesito un nombre para continuar.");
    STATE.name = v;
    addBubble(v, "user");
    askConsole();
  };

  function askConsole() {
    addBubble(`Un gusto, ${STATE.name}. ¿Qué consola tienes?`);
    setControls(`
      <div class="row">
        <select id="selConsole">
          <option value="">Selecciona tu consola...</option>
          <option value="PS4 Fat">PS4 Fat</option>
          <option value="PS4 Slim">PS4 Slim</option>
          <option value="PS4 Pro">PS4 Pro</option>
          <option value="PS5 Fat">PS5 Fat</option>
          <option value="PS5 Slim">PS5 Slim</option>
          <option value="Xbox One">Xbox One</option>
          <option value="Xbox One S">Xbox One S</option>
          <option value="Xbox One X">Xbox One X</option>
          <option value="Xbox Series S">Xbox Series S</option>
          <option value="Xbox Series X">Xbox Series X</option>
          <option value="other">Otra...</option>
        </select>
        <button class="btn" onclick="handleConsole()">Listo</button>
      </div>
    `);
  }

  window.handleConsole = () => {
    const sel = document.getElementById("selConsole");
    const val = sel.value;
    if (!val) return showError("Selecciona una consola de la lista.");
    STATE.console = val;
    addBubble(val, "user");
    askMethod();
  };

  function askMethod() {
    addBubble("¿Qué prefieres?");
    setControls(`
      <div class="row">
        <button class="btn secondary" onclick="startGallery()">Ver Diseños</button>
        <button class="btn" onclick="startCustom()">Personalizado</button>
      </div>
      <div class="tiny">Personalizado = Subes tus propias fotos.</div>
    `);
  }

  // --- MODO GALERÍA ---
  window.startGallery = () => {
    STATE.mode = "gallery";
    addBubble("¡De una! Tengo estas categorías. Toca una para ver los diseños:");
    
    const cats = Object.keys(GALLERIES).map(c => 
      `<button class="pill" onclick="showCategory('${c}')">${c}</button>`
    ).join("");

    setControls(`<div class="pill-wrap">${cats}</div><div class="tiny" style="margin-top:8px"><button class="btn secondary" style="padding:5px 10px" onclick="startCustom()">Mejor personalizado</button></div>`);
  };

  window.showCategory = (cat) => {
    addBubble(`📂 Categoría: ${cat}`, "user");
    const imgs = GALLERIES[cat];
    
    let html = `<div class="grid">`;
    imgs.forEach((url, i) => {
      html += `
        <div class="card" onclick="selectDesign(this, '${url}')">
          <div class="check">✓</div>
          <img src="${url}" loading="lazy" />
          <div class="cap">Diseño ${i+1}</div>
        </div>
      `;
    });
    html += `</div>`;
    
    addBubble(html, "bot", true);
    addBubble("Toca el diseño que te guste para seleccionarlo.", "bot");
  };

  window.selectDesign = (el, url) => {
    // Quitar selección previa
    document.querySelectorAll(".card.selected").forEach(c => c.classList.remove("selected"));
    // Seleccionar nuevo
    el.classList.add("selected");
    STATE.selected_design_url = url;

    setControls(`
      <div class="tiny" style="margin-bottom:5px">Has seleccionado un diseño.</div>
      <div class="row">
        <button class="btn ok" onclick="askContactData()">✅ Quiero este diseño</button>
      </div>
      <div class="row" style="margin-top:5px">
        <button class="btn secondary" onclick="startGallery()">Ver otras categorías</button>
      </div>
    `);
  };

  // --- MODO PERSONALIZADO ---
  window.startCustom = () => {
    STATE.mode = "custom";
    addBubble("Modo Personalizado 🎨. Sube tus imágenes y dime qué quieres.");
    renderUploadForm();
  };

  function renderUploadForm() {
    setControls(`
      <div id="uploadArea">
        <div class="imgblock">
          <label>Imagen 1</label>
          <input type="file" id="fileInput" accept="image/*" onchange="checkFileSize(this)" />
          <textarea id="fileDetail" placeholder="Detalles: colores, posición, texto..."></textarea>
        </div>
      </div>
      <div class="tiny" id="sizeWarning"></div>
      <div class="row">
        <button class="btn" onclick="handleCustomSubmit()">Continuar</button>
      </div>
    `);
  }

  window.checkFileSize = (input) => {
    const file = input.files[0];
    const warning = document.getElementById("sizeWarning");
    warning.innerHTML = "";
    
    if (file && file.size > MAX_BYTES) {
      // Imagen muy pesada
      input.value = ""; // Limpiar
      addBubble("⚠️ Esa imagen es muy pesada (" + (file.size/1024/1024).toFixed(1) + "MB). El límite es " + (MAX_BYTES/1024/1024) + "MB.", "bot");
      
      // Ofrecer WhatsApp inmediatamente
      setControls(`
        <div class="bubble bot">La imagen es muy grande. Es mejor que nos envíes todo por WhatsApp.</div>
        <div class="row">
          <a href="${buildWaLink(true)}" target="_blank" class="btn whatsapp">Enviar por WhatsApp</a>
          <button class="btn secondary" onclick="renderUploadForm()">Intentar otra imagen</button>
        </div>
      `);
    }
  };

  window.handleCustomSubmit = () => {
    const fi = document.getElementById("fileInput");
    const de = document.getElementById("fileDetail");
    
    if (!fi.files || fi.files.length === 0) return showError("Sube al menos una imagen.");
    if (!de.value.trim()) return showError("Escribe algún detalle sobre el diseño.");

    STATE.custom_files = [fi.files[0]];
    STATE.custom_details = [de.value];
    
    addBubble("📸 Imagen cargada y detalles guardados.", "user");
    askContactData();
  };

  // --- CONTACTO FINAL ---
  function askContactData() {
    addBubble("¡Ya casi! Dame tu WhatsApp y correo para contactarte.");
    setControls(`
      <div style="display:grid; gap:8px;">
        <input id="inWa" type="tel" placeholder="WhatsApp (+57...)" />
        <input id="inEmail" type="email" placeholder="Correo electrónico" />
        <button class="btn" onclick="submitFinal()">Enviar Pedido</button>
      </div>
    `);
  }

  async function submitFinal() {
    const wa = document.getElementById("inWa").value;
    const em = document.getElementById("inEmail").value;
    
    if (!wa || !em.includes("@")) return showError("Revisa los datos (WhatsApp y Correo).");
    
    STATE.whatsapp = wa;
    STATE.email = em;

    // Loading visual
    setControls(`<div class="tiny">Enviando solicitud... ⏳</div>`);
    
    const fd = new FormData();
    fd.append("name", STATE.name);
    fd.append("email", STATE.email);
    fd.append("whatsapp", STATE.whatsapp);
    fd.append("console", STATE.console);
    fd.append("design_choice", STATE.mode === "gallery" ? "Galería: " + STATE.selected_design_url : "Personalizado");
    fd.append("has_design", STATE.mode === "custom" ? "true" : "false");

    if (STATE.mode === "custom") {
      fd.append("images", STATE.custom_files[0]);
      fd.append("details", STATE.custom_details[0]);
    }

    try {
      const res = await fetch("/submit", { method: "POST", body: fd });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Error en servidor");
      }
      
      // Éxito
      addBubble("✅ ¡Solicitud recibida! Nos pondremos en contacto.", "bot");
      showFinalActions();
      
    } catch (e) {
      showError("Hubo un error enviando: " + e.message);
      // Fallback a WhatsApp si falla servidor
      setControls(`
        <div class="row">
          <a href="${buildWaLink()}" class="btn whatsapp">Enviar manual por WhatsApp</a>
        </div>
      `);
    }
  }

  function showFinalActions() {
    setControls(`
      <div class="tiny">Si deseas agilizar, escríbenos ya:</div>
      <div class="row">
        <a href="${buildWaLink()}" target="_blank" class="btn whatsapp">Hablar por WhatsApp</a>
        <button class="btn secondary" onclick="location.reload()">Empezar otro</button>
      </div>
    `);
  }

  function buildWaLink(isHeavyError=false) {
    let text = `Hola, soy ${STATE.name}. Quiero un skin para ${STATE.console}. `;
    if (isHeavyError) text += "Intenté subir la imagen en la web pero era muy pesada, te la paso por aquí.";
    else if (STATE.mode === 'gallery') text += "Elegí un diseño de la galería.";
    else text += "Es un diseño personalizado.";
    
    return `https://wa.me/${BUSINESS_WA}?text=${encodeURIComponent(text)}`;
  }

  // Evento precio
  btnPrice.onclick = () => {
    addBubble("💰 <b>Precios actuales:</b><br>" + PRICES.join("<br>"), "bot", true);
  };

  // Arrancar
  start();

</script>
</body>
</html>
    """
    html = html.replace("__BUSINESS_WA_NUMBER__", BUSINESS_WHATSAPP_NUMBER)
    html = html.replace("__MAX_BYTES__", str(MAX_IMAGE_BYTES))
    return HTMLResponse(html)

@app.post("/submit")
async def submit(
    name: str = Form(...),
    email: str = Form(...),
    whatsapp: str = Form(...),
    console: str = Form(...),
    design_choice: str = Form(""),
    has_design: str = Form("false"),
    whatsapp_prefill: str = Form(""),
    details: Optional[List[str]] = Form(None),
    images: Optional[List[UploadFile]] = File(None),
):
    # --- Validaciones y Lógica Backend (Igual que antes pero robustecida) ---
    name = name.strip()
    console = console.strip()
    whatsapp = _clean_phone(whatsapp)
    
    if not _basic_email_ok(email):
        raise HTTPException(400, "Email inválido")

    has_design_bool = _truthy(has_design)
    details = details or []
    images = images or []

    # Insertar Lead
    lead_data = {
        "name": name,
        "email": email,
        "whatsapp": whatsapp,
        "console": console,
        "design_choice": design_choice,
        "has_design": has_design_bool,
        "whatsapp_prefill": whatsapp_prefill
    }
    
    try:
        lead = await _insert_lead(lead_data)
        lead_id = lead.get("id", "unknown")
    except Exception as e:
        print(f"Error insertando lead: {e}")
        raise HTTPException(500, "Error guardando datos")

    uploaded_info = []

    # Procesar imágenes si es personalizado
    if has_design_bool and images:
        for idx, file in enumerate(images):
            # Leer contenido para validar tamaño real en backend también
            content = await file.read()
            size = len(content)
            
            if size > MAX_IMAGE_BYTES:
                # Opcional: Podrías simplemente saltarla o lanzar error. 
                # Lanzar error detiene todo el proceso.
                raise HTTPException(400, f"Imagen muy pesada (> {MAX_IMAGE_MB}MB)")

            ct = file.content_type
            ext = ALLOWED_IMAGE_TYPES.get(ct, ".jpg")
            path = f"{lead_id}/{uuid.uuid4().hex}{ext}"
            
            await _upload_to_storage(SUPABASE_BUCKET, path, content, ct)
            public_url = _make_public_url(SUPABASE_BUCKET, path)
            
            detail_text = details[idx] if len(details) > idx else ""
            
            row = {
                "lead_id": lead_id,
                "storage_bucket": SUPABASE_BUCKET,
                "storage_path": path,
                "public_url": public_url,
                "original_filename": file.filename,
                "content_type": ct,
                "size_bytes": size,
                "details": detail_text
            }
            await _insert_lead_image(row)
            uploaded_info.append(row)

    # Enviar correo (Background task idealmente, aquí sync para MVP)
    try:
        email_body = f"""
        Nuevo Lead de Skins:
        --------------------
        Nombre: {name}
        Consola: {console}
        WhatsApp: {whatsapp}
        Email: {email}
        Modo: {design_choice}
        
        Imágenes adjuntas: {len(uploaded_info)}
        """
        for img in uploaded_info:
            email_body += f"\n- {img['public_url']} ({img['details']})"
            
        await run_in_threadpool(_send_email_sync, f"Nuevo Lead Skins: {name}", email_body)
    except Exception as e:
        print(f"Error enviando email: {e}")

    return JSONResponse({"ok": True, "lead_id": lead_id})