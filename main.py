import os
import re
import uuid
import smtplib
import html
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

BUSINESS_WHATSAPP_NUMBER = os.getenv("BUSINESS_WHATSAPP_NUMBER", "573183483807")
MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "5"))
MAX_IMAGE_BYTES = MAX_IMAGE_MB * 1024 * 1024

ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

# -----------------------------
# Funciones de Seguridad
# -----------------------------
def sanitize_input(text: str) -> str:
    if not text: return ""
    return html.escape(text.strip())

def validate_phone(phone: str) -> str:
    return re.sub(r"[^0-9]", "", phone)

def _sb_headers() -> dict:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Faltan credenciales de Supabase.")
    return {"apikey": SUPABASE_SERVICE_ROLE_KEY, "authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"}

def _basic_email_ok(email: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email))

def _truthy(s: str) -> bool:
    return str(s).strip().lower() in {"1", "true", "yes", "si", "sí", "y"}

def _make_public_url(bucket: str, path: str) -> str:
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"

# -----------------------------
# Helpers DB
# -----------------------------
async def _get_gallery_images(console_model: str) -> List[dict]:
    """
    CAMBIO: Filtra por 'console_model' exacto (ej: 'PS4 Fat') 
    para coincidir con la nueva estructura de la base de datos.
    """
    url = f"{SUPABASE_URL}/rest/v1/gallery"
    params = {
        "select": "*",
        "console_model": f"eq.{console_model}", 
        "order": "id.asc"
    }
    headers = _sb_headers()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, headers=headers, params=params)
        if r.status_code == 200:
            return r.json()
        return []

async def _insert_lead(data: dict) -> dict:
    url = f"{SUPABASE_URL}/rest/v1/leads"
    headers = _sb_headers()
    headers["prefer"] = "return=representation"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers=headers, json=[data])
        return r.json()[0] if r.status_code < 300 and r.json() else {}

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
        await client.post(url, headers=headers, content=content)

def _send_email_sync(subject: str, body: str) -> None:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD): return
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
# API: Obtener Galería
# -----------------------------
@app.get("/api/gallery/{console_type}")
async def get_gallery(console_type: str):
    # Recibimos el nombre exacto de la consola y consultamos
    images = await _get_gallery_images(console_type)
    return images

# -----------------------------
# Frontend
# -----------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    html = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
  <title>Skins Colombia - Pedidos</title>
  <style>
    :root {
      --bg: #0b1220; --bubble-bot: #1e293b; --bubble-user: #2563eb;
      --text: #f1f5f9; --muted: #94a3b8; --accent: #3b82f6; --ok: #22c55e;
      --input-bg: #f8fafc; --input-text: #0f172a; --danger: #ef4444;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; font-family: system-ui, -apple-system, sans-serif;
      background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column;
    }
    .wrap { max-width: 600px; margin: 0 auto; width: 100%; height: 100%; display: flex; flex-direction: column; }
    header { padding: 12px 16px; background: rgba(15,27,51,0.95); display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.1); }
    .chat { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; scroll-behavior: smooth; }
    .bubble { max-width: 85%; padding: 12px 14px; border-radius: 16px; line-height: 1.4; font-size: 15px; animation: popIn 0.3s ease-out; }
    .bot { background: var(--bubble-bot); border-bottom-left-radius: 4px; color: #e2e8f0; }
    .user { background: var(--bubble-user); margin-left: auto; border-bottom-right-radius: 4px; color: white; }
    @keyframes popIn { from{opacity:0; transform:translateY(5px);} to{opacity:1; transform:translateY(0);} }
    .controls { padding: 12px 16px; background: #111827; border-top: 1px solid rgba(255,255,255,0.1); }
    input, select, textarea { width: 100%; padding: 12px; border-radius: 10px; border: none; background: var(--input-bg); color: var(--input-text); font-size: 16px; outline: none; margin-bottom: 8px; }
    textarea { min-height: 60px; resize: vertical; }
    .btn { width: 100%; border: none; border-radius: 10px; padding: 12px; background: var(--accent); color: white; font-weight: 600; cursor: pointer; text-align: center; margin-top: 4px; display:block; }
    .btn.secondary { background: #334155; }
    .btn.whatsapp { background: #25D366; }
    .btn.ok { background: var(--ok); color: #000; }
    .btn.add { background: transparent; border: 1px dashed var(--muted); color: var(--muted); margin-bottom:10px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
    .card { border-radius: 8px; overflow: hidden; background: #000; position: relative; border: 2px solid transparent; cursor: pointer; }
    .card.selected { border-color: var(--ok); }
    .card img { width: 100%; height: 110px; object-fit: cover; display: block; }
    .card .cap { font-size: 10px; padding: 4px; text-align: center; color: #ccc; }
    .combo-card { background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 12px; margin-bottom: 8px; cursor: pointer; }
    .combo-card.active { border-color: var(--ok); background: rgba(34, 197, 94, 0.1); }
    .upload-row { background: rgba(255,255,255,0.05); padding: 10px; border-radius: 10px; margin-bottom: 8px; border: 1px solid rgba(255,255,255,0.1); }
    .upload-row label { display: block; font-size: 12px; color: var(--accent); margin-bottom: 4px; font-weight: bold; }
    .check-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; padding: 8px; background: rgba(255,255,255,0.05); border-radius: 8px; }
    .check-row input { width: auto; margin: 0; }
    .tiny { font-size: 12px; color: var(--muted); }
    .loader { text-align:center; padding: 20px; color: var(--muted); }
  </style>
</head>
<body>
<div class="wrap">
  <header>
    <div style="font-weight:700">Skins Colombia</div>
    <button class="btn secondary" style="width:auto; padding:6px 12px; margin:0;" onclick="window.location.reload()">Reiniciar</button>
  </header>
  <section class="chat" id="msgs"></section>
  <div class="controls" id="controls"></div>
</div>

<script>
  const BUSINESS_WA = "__BUSINESS_WA_NUMBER__";
  
  // Combos fijos
  const COMBOS = [
    { id: "c1", title: "Combo 1", price: 80000, desc: "2 controles + Arriba + Frontal + Abajo/Lados" },
    { id: "c2", title: "Combo 2", price: 65000, desc: "Arriba + Frontal + Abajo o Lados" },
    { id: "c3", title: "Combo 3", price: 55000, desc: "Arriba + Frontal" },
    { id: "c4", title: "Combo 4", price: 60000, desc: "Arriba + Frontal + 2 Mandos" },
    { id: "c6", title: "Combo 6 (Solo PS5)", price: 40000, desc: "Solo Frontal", only: ["PS5 Fat", "PS5 Slim"] },
    { id: "c7", title: "Combo 7 (Solo Series X)", price: 60000, desc: "4 Caras de la consola", only: ["Xbox Series X"] }
  ];

  // CAMBIO: Agregamos 'all_images' para guardar la galería en memoria
  const STATE = { 
    name: "", 
    console: "", 
    design_url: "", 
    combo_id: "", 
    extra_control: false, 
    is_custom: false, 
    base_price: 0, 
    custom_uploads: [],
    all_images: [] 
  };
  
  const msgs = document.getElementById("msgs");
  const controls = document.getElementById("controls");

  function scrollBot() { setTimeout(() => msgs.scrollTop = msgs.scrollHeight, 100); }
  function addBubble(txt, who="bot", html=false) {
    const d = document.createElement("div"); d.className="bubble "+who;
    if(html) d.innerHTML=txt; else d.textContent=txt;
    msgs.appendChild(d); scrollBot();
  }
  function setControls(html) { controls.innerHTML = html; }
  function showError(m) { addBubble("⚠️ "+m, "bot"); }

  // 1. INICIO
  function start() {
    addBubble("👋 Hola, bienvenido a Skins Colombia.");
    setTimeout(() => {
        addBubble("¿Cómo te llamas?");
        setControls(`<div class="row"><input id="inName" placeholder="Tu nombre..." /><button class="btn" onclick="handleName()">Siguiente</button></div>`);
    }, 500);
  }

  window.handleName = () => {
    const v = document.getElementById("inName").value.trim();
    if(!v) return showError("Escribe tu nombre");
    STATE.name = v;
    addBubble(v, "user");
    askConsole();
  };

  function askConsole() {
    addBubble(`Un gusto ${STATE.name}. ¿Qué consola tienes?`);
    setControls(`
      <select id="selConsole">
        <option value="">Selecciona...</option>
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
        <option value="Switch">Nintendo Switch</option>
      </select>
      <button class="btn" onclick="handleConsole()">Ver Diseños</button>
    `);
  }

  window.handleConsole = () => {
    const c = document.getElementById("selConsole").value;
    if(!c) return showError("Selecciona una consola");
    
    // CAMBIO: Ya no usamos categoría genérica (xbox/play), usamos el nombre exacto
    STATE.console = c;
    
    addBubble(c, "user");
    fetchGallery(); 
  };

  // 2. GALERÍAS
  async function fetchGallery() {
    addBubble(`Buscando los mejores diseños para ${STATE.console}... ⏳`, "bot");
    setControls(`<div class="loader">Cargando...</div>`);
    
    try {
        // CAMBIO: Enviamos el nombre exacto de la consola (con encodeURIComponent por los espacios)
        const res = await fetch(`/api/gallery/${encodeURIComponent(STATE.console)}`);
        const images = await res.json();
        
        if(images.length === 0) {
            addBubble("Aún no tengo diseños cargados para esta consola en la galería.", "bot");
            setControls(`<button class="btn secondary" onclick="startCustom()">🎨 Ir a Diseño Personalizado</button>`);
            return;
        }

        STATE.all_images = images;
        renderBatch(1); // Renderizamos la primera tanda

    } catch(e) {
        addBubble("Hubo un error cargando la galería. Vamos a personalizado.", "bot");
        setControls(`<button class="btn" onclick="startCustom()">Personalizado</button>`);
    }
  }

  // CAMBIO: Función para renderizar por lotes de 10
  function renderBatch(batchNumber) {
    const limit = 10;
    const start = (batchNumber - 1) * limit;
    const end = start + limit;
    
    const slice = STATE.all_images.slice(start, end);
    const hasMore = STATE.all_images.length > end;

    // Mensaje AMIGABLE
    let msg = "";
    if(batchNumber === 1) msg = "¡Checa estos diseños brutales! 🔥";
    else msg = "Aquí tienes más opciones exclusivas:";

    addBubble(msg, "bot");
    
    let html = `<div class="grid">`;
    slice.forEach((img) => {
      html += `
        <div class="card" onclick="selectDesign(this, '${img.image_url}')">
            <img src="${img.image_url}" loading="lazy">
        </div>`;
    });
    html += `</div>`;
    
    addBubble(html, "bot", true);
    
    let buttonsHtml = "";
    
    if (hasMore) {
        // Si hay más: Botón "Ver más"
        buttonsHtml = `
            <div class="row">
                <button class="btn secondary" onclick="renderBatch(${batchNumber + 1})">Ver más diseños</button>
                <button class="btn secondary" onclick="startCustom()">Prefiero Personalizado</button>
            </div>
        `;
    } else {
        // Si es el final: Solo Personalizado y WA
        buttonsHtml = `
            <button class="btn secondary" onclick="startCustom()">🎨 Ninguno me convence, quiero personalizado</button>
            <a href="https://wa.me/${BUSINESS_WA}" target="_blank" class="btn whatsapp">💬 Contactar WhatsApp</a>
        `;
    }

    setControls(buttonsHtml);
    addBubble("Toca una imagen para seleccionar.", "bot");
  }

  window.selectDesign = (el, url) => {
    document.querySelectorAll(".card.selected").forEach(c => c.classList.remove("selected"));
    el.classList.add("selected");
    STATE.design_url = url;
    STATE.is_custom = false;
    showCombos();
  };

  window.startCustom = () => {
    STATE.is_custom = true;
    STATE.design_url = "Personalizado";
    addBubble("Prefiero personalizado.", "user");
    showCombos();
  }

  // 3. COMBOS
  window.showCombos = () => {
    const available = COMBOS.filter(c => !c.only || c.only.includes(STATE.console));
    let html = `<div class="tiny">Elige tu combo (Envío Gratis + Pago Contra Entrega):</div>`;
    available.forEach(c => {
        html += `
        <div class="combo-card" onclick="selectCombo(this, '${c.id}', ${c.price})">
            <div class="combo-head"><span>${c.title}</span> <span>$${c.price.toLocaleString()}</span></div>
            <div class="tiny" style="color:#ccc">${c.desc}</div>
        </div>`;
    });

    html += `
      <div class="check-row">
        <input type="checkbox" id="chkExtra" onchange="toggleExtra(this)">
        <label for="chkExtra">Control adicional (+$16.000)</label>
      </div>
      <button class="btn ok" id="btnOrder" disabled onclick="askShippingData()">Seleccionar Combo</button>
      <button class="btn whatsapp" onclick="consultarCombo()">Dudas del combo</button>
    `;
    
    addBubble("💰 Precios para " + STATE.console + ":", "bot");
    setControls(html);
  };

  window.selectCombo = (el, id, price) => {
    document.querySelectorAll(".combo-card").forEach(c => c.classList.remove("active"));
    el.classList.add("active");
    STATE.combo_id = id;
    STATE.base_price = price;
    document.getElementById("btnOrder").disabled = false;
    updateTotalBtn();
  };

  window.toggleExtra = (chk) => {
    STATE.extra_control = chk.checked;
    if (STATE.combo_id) updateTotalBtn();
  };

  function updateTotalBtn() {
     const t = (STATE.base_price || 0) + (STATE.extra_control ? 16000 : 0);
     document.getElementById("btnOrder").textContent = `Pedir ($${t.toLocaleString()})`;
  }

  window.consultarCombo = () => {
      const msg = `Hola, me interesa el combo para ${STATE.console}, pero tengo dudas.`;
      window.open(`https://wa.me/${BUSINESS_WA}?text=${encodeURIComponent(msg)}`, '_blank');
  };

  // 4. DATOS Y CARGA
  window.askShippingData = () => {
    const t = (STATE.base_price || 0) + (STATE.extra_control ? 16000 : 0);
    const combo = COMBOS.find(c => c.id === STATE.combo_id);
    addBubble(`Elegí: ${combo.title}. Total: $${t.toLocaleString()}`, "user");
    
    if (STATE.is_custom) renderCustomUploadForm(combo);
    else renderShippingForm();
  };

  function renderCustomUploadForm(combo) {
      const partes = combo.desc.split('+').map(p => p.trim()).join(", ");
      addBubble(`☝️ Tu combo incluye: <b>${partes}</b>.<br>Puedes usar una imagen diferente para cada parte.`, "bot", true);
      addBubble("Sube tus imágenes de referencia:", "bot");

      setControls(`
        <div id="uploadList"></div>
        <button class="btn add" onclick="addUploadRow()">+ Agregar otra imagen</button>
        <button class="btn" onclick="finishUploads()">Continuar</button>
      `);
      addUploadRow();
  }

  window.addUploadRow = () => {
      const container = document.getElementById("uploadList");
      const idx = container.children.length + 1;
      const div = document.createElement("div");
      div.className = "upload-row";
      div.innerHTML = `
        <label>Imagen ${idx}</label>
        <input type="file" class="file-in" accept="image/*">
        <textarea class="desc-in" placeholder="¿En qué parte va esta imagen? (Ej: Frente, Arriba...)"></textarea>
      `;
      container.appendChild(div);
  };

  window.finishUploads = () => {
      const rows = document.querySelectorAll(".upload-row");
      const uploads = [];
      let hasFile = false;
      rows.forEach(row => {
          const fileIn = row.querySelector(".file-in");
          const descIn = row.querySelector(".desc-in");
          if(fileIn.files.length > 0) {
              hasFile = true;
              uploads.push({ file: fileIn.files[0], detail: descIn.value.trim() || "Sin detalles" });
          }
      });
      if (!hasFile) return showError("Sube al menos una imagen para continuar.");
      STATE.custom_uploads = uploads;
      addBubble(`✅ He adjuntado ${uploads.length} imágenes.`, "user");
      renderShippingForm();
  };

  function renderShippingForm() {
      addBubble("Para el envío gratis y pago contra entrega, necesito tus datos:", "bot");
      setControls(`
        <div style="background:#1e293b; padding:10px; border-radius:10px;">
            <label class="tiny">Nombre completo de quien recibe (Nombre + Apellido):</label>
            <input id="inReceiver" value="${STATE.name}" placeholder="Ej: Juan Pérez">
            <label class="tiny">WhatsApp:</label>
            <input id="inWa" type="tel" placeholder="300 123 4567">
            <label class="tiny">Correo Electrónico:</label>
            <input id="inEmail" type="email" placeholder="ejemplo@correo.com">
            <label class="tiny">Ciudad:</label>
            <input id="inCity" placeholder="Ej: Bogotá">
            <label class="tiny">Barrio:</label>
            <input id="inBarrio" placeholder="Ej: Chapinero">
            <label class="tiny">Dirección Exacta:</label>
            <input id="inAddress" placeholder="Cl 123 # 45-67 Apto 101">
            <button class="btn ok" onclick="submitFinal()">✅ FINALIZAR PEDIDO</button>
        </div>
      `);
  }

  async function submitFinal() {
      const rec = document.getElementById("inReceiver").value.trim();
      const wa = document.getElementById("inWa").value.trim();
      const email = document.getElementById("inEmail").value.trim();
      const city = document.getElementById("inCity").value.trim();
      const bar = document.getElementById("inBarrio").value.trim();
      const addr = document.getElementById("inAddress").value.trim();

      if (rec.length < 5 || !rec.includes(" ")) return showError("Nombre y Apellido requeridos.");
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return showError("Correo inválido.");
      const waClean = wa.replace(/[^0-9]/g, '');
      if (waClean.length < 7) return showError("Número de WhatsApp inválido.");
      if (!city || !bar || !addr) return showError("Falta ciudad, barrio o dirección.");

      STATE.receiver = rec; STATE.whatsapp = wa; STATE.email = email;
      STATE.city = city; STATE.barrio = bar; STATE.address = addr;

      addBubble("Enviando pedido... ⏳", "bot");
      
      const fd = new FormData();
      fd.append("name", STATE.name);
      fd.append("receiver_name", STATE.receiver);
      fd.append("whatsapp", STATE.whatsapp);
      fd.append("email", STATE.email);
      fd.append("city", STATE.city);
      fd.append("neighborhood", STATE.barrio);
      fd.append("address", STATE.address);
      fd.append("console", STATE.console);
      
      const t = (STATE.base_price || 0) + (STATE.extra_control ? 16000 : 0);
      const combo = COMBOS.find(c => c.id === STATE.combo_id);
      
      let det = `Combo: ${combo.title}. Total: $${t.toLocaleString()}. `;
      if (STATE.extra_control) det += " + Control Adicional.";
      det += STATE.is_custom ? " [Personalizado]" : ` [Galería: ${STATE.design_url}]`;
      
      fd.append("design_choice", det);
      fd.append("has_design", STATE.is_custom ? "true" : "false");
      
      if (STATE.is_custom && STATE.custom_uploads.length > 0) {
          STATE.custom_uploads.forEach(u => {
              fd.append("images", u.file);
              fd.append("image_details", u.detail);
          });
      }

      try {
          await fetch("/submit", { method: "POST", body: fd });
          
          if (STATE.is_custom) {
             addBubble("✅ ¡Pedido Personalizado Recibido!", "bot");
             addBubble("Te enviaremos la propuesta de diseño por WhatsApp/Correo en máx 3 días.", "bot");
          } else {
             addBubble("✅ ¡Pedido Confirmado!", "bot");
             addBubble("Te contactaremos al WhatsApp para el despacho.", "bot");
          }
          
          const waText = `Hola, hice un pedido de ${combo.title} por $${t}. A nombre de ${STATE.receiver} en ${STATE.city}.`;
          setControls(`<a href="https://wa.me/${BUSINESS_WA}?text=${encodeURIComponent(waText)}" class="btn whatsapp">Abrir WhatsApp</a>`);
      } catch (e) {
          addBubble("Error conectando. Envíanos los datos por WhatsApp.", "bot");
      }
  }

  start();
</script>
</body>
</html>
"""
    html = html.replace("__BUSINESS_WA_NUMBER__", BUSINESS_WHATSAPP_NUMBER)
    return HTMLResponse(html)
    