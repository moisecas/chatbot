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

# Asegúrate de que este número esté correcto (57 + número)
BUSINESS_WHATSAPP_NUMBER = os.getenv("BUSINESS_WHATSAPP_NUMBER", "573183483807")
MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "5"))
MAX_IMAGE_BYTES = MAX_IMAGE_MB * 1024 * 1024

ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

# -----------------------------
# Helpers & Database
# -----------------------------
def _sb_headers() -> dict:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Faltan credenciales de Supabase.")
    return {"apikey": SUPABASE_SERVICE_ROLE_KEY, "authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"}

def _basic_email_ok(email: str) -> bool:
    return "@" in email and "." in email

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
            print(f"Warning DB: {r.text}")
            return {}
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
  <title>Skins Colombia - Pedidos</title>
  <style>
    :root {
      --bg: #0b1220;
      --bubble-bot: #1e293b; 
      --bubble-user: #2563eb;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --accent: #3b82f6;
      --ok: #22c55e;
      --input-bg: #f8fafc;
      --input-text: #0f172a;
      --danger: #ef4444;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; font-family: system-ui, -apple-system, sans-serif;
      background: var(--bg); color: var(--text);
      height: 100vh; display: flex; flex-direction: column;
    }
    .wrap {
      max-width: 600px; margin: 0 auto; width: 100%; height: 100%;
      display: flex; flex-direction: column;
    }
    header {
      padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,0.1);
      background: rgba(15,27,51,0.95); display: flex; justify-content: space-between; align-items: center;
    }
    .chat {
      flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px;
      scroll-behavior: smooth;
    }
    .bubble {
      max-width: 85%; padding: 12px 14px; border-radius: 16px;
      line-height: 1.4; font-size: 15px; animation: popIn 0.3s ease-out;
    }
    .bot { background: var(--bubble-bot); border-bottom-left-radius: 4px; color: #e2e8f0; }
    .user { background: var(--bubble-user); margin-left: auto; border-bottom-right-radius: 4px; color: white; }
    @keyframes popIn { from{opacity:0; transform:translateY(5px);} to{opacity:1; transform:translateY(0);} }

    .controls {
      padding: 12px 16px; background: #111827; border-top: 1px solid rgba(255,255,255,0.1);
    }
    
    input, select, textarea {
      width: 100%; padding: 12px; border-radius: 10px; border: none;
      background: var(--input-bg); color: var(--input-text); font-size: 16px; outline: none; margin-bottom: 8px;
    }
    textarea { min-height: 60px; resize: vertical; }

    .btn {
      width: 100%; border: none; border-radius: 10px; padding: 12px;
      background: var(--accent); color: white; font-weight: 600; cursor: pointer; text-align: center; margin-top: 4px; display:block;
    }
    .btn.secondary { background: #334155; }
    .btn.whatsapp { background: #25D366; }
    .btn.ok { background: var(--ok); color: #000; }
    .btn.add { background: transparent; border: 1px dashed var(--muted); color: var(--muted); margin-bottom:10px; }
    
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
    .card {
      border-radius: 8px; overflow: hidden; background: #000; position: relative; border: 2px solid transparent; cursor: pointer;
    }
    .card.selected { border-color: var(--ok); }
    .card img { width: 100%; height: 110px; object-fit: cover; display: block; }
    .card .cap { font-size: 10px; padding: 4px; text-align: center; color: #ccc; }

    .combo-card {
      background: #1e293b; border: 1px solid #334155; border-radius: 10px;
      padding: 12px; margin-bottom: 8px; cursor: pointer;
    }
    .combo-card.active { border-color: var(--ok); background: rgba(34, 197, 94, 0.1); }
    
    .upload-row {
      background: rgba(255,255,255,0.05); padding: 10px; border-radius: 10px; margin-bottom: 8px; border: 1px solid rgba(255,255,255,0.1);
    }
    .upload-row label { display: block; font-size: 12px; color: var(--accent); margin-bottom: 4px; font-weight: bold; }
    
    .check-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; padding: 8px; background: rgba(255,255,255,0.05); border-radius: 8px; }
    .check-row input { width: auto; margin: 0; }
    
    .tiny { font-size: 12px; color: var(--muted); }
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
  // CONFIG
  const BUSINESS_WA = "__BUSINESS_WA_NUMBER__";
  
  // Data Placeholders
  const DESIGN_BATCH_1 = [
    "https://images.unsplash.com/photo-1534423861386-85a16f5d13fd?w=300", "https://images.unsplash.com/photo-1511512578047-dfb367046420?w=300",
    "https://images.unsplash.com/photo-1542751371-adc38448a05e?w=300", "https://images.unsplash.com/photo-1593118247619-e2d6f056869e?w=300",
    "https://images.unsplash.com/photo-1612287230202-1ff1d85d1bdf?w=300", "https://images.unsplash.com/photo-1560253023-3ec5d502959f?w=300",
    "https://images.unsplash.com/photo-1626379953822-baec19c3accd?w=300", "https://images.unsplash.com/photo-1579373903781-fd5c0c30c4cd?w=300",
    "https://images.unsplash.com/photo-1616588589676-60b30c3c1681?w=300", "https://images.unsplash.com/photo-1605901309584-818e25960b8f?w=300"
  ];
  const DESIGN_BATCH_2 = [
    "https://images.unsplash.com/photo-1600080972464-8cb882e6a9f0?w=300", "https://images.unsplash.com/photo-1552820728-8b83bb6b773f?w=300",
    "https://images.unsplash.com/photo-1518709268805-4e9042af9f23?w=300", "https://images.unsplash.com/photo-1493711662062-fa541adb3fc8?w=300",
    "https://images.unsplash.com/photo-1513542789411-b6a5d4f31634?w=300", "https://images.unsplash.com/photo-1605810230434-7631ac76ec81?w=300",
    "https://images.unsplash.com/photo-1550745165-9bc0b252726f?w=300", "https://images.unsplash.com/photo-1531297461136-82lw9f23?w=300",
    "https://images.unsplash.com/photo-1492684223066-81342ee5ff30?w=300", "https://images.unsplash.com/photo-1592155931584-901ac15763e3?w=300"
  ];

  const COMBOS = [
    { id: "c1", title: "Combo 1", price: 80000, desc: "2 controles + Arriba + Frontal + Abajo/Lados" },
    { id: "c2", title: "Combo 2", price: 65000, desc: "Arriba + Frontal + Abajo o Lados" },
    { id: "c3", title: "Combo 3", price: 55000, desc: "Arriba + Frontal" },
    { id: "c4", title: "Combo 4", price: 60000, desc: "Arriba + Frontal + 2 Mandos" },
    { id: "c6", title: "Combo 6 (Solo PS5)", price: 40000, desc: "Solo Frontal", only: ["PS5 Fat", "PS5 Slim"] },
    { id: "c7", title: "Combo 7 (Solo Series X)", price: 60000, desc: "4 Caras de la consola", only: ["Xbox Series X"] }
  ];

  const STATE = { 
    name: "", console: "", design_url: "", combo_id: "", extra_control: false, is_custom: false, base_price: 0,
    custom_uploads: []
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
        <option value="Xbox Series S">Xbox Series S</option>
        <option value="Xbox Series X">Xbox Series X</option>
      </select>
      <button class="btn" onclick="handleConsole()">Ver Diseños</button>
    `);
  }

  window.handleConsole = () => {
    const c = document.getElementById("selConsole").value;
    if(!c) return showError("Selecciona una consola");
    STATE.console = c;
    addBubble(c, "user");
    showGallery(1);
  };

  // 2. GALERÍAS
  window.showGallery = (batch) => {
    const imgs = batch === 1 ? DESIGN_BATCH_1 : DESIGN_BATCH_2;
    addBubble(batch === 1 ? "Tanda 1 de diseños:" : "Tanda 2 de diseños:");
    
    let html = `<div class="grid">`;
    imgs.forEach((u, i) => {
      html += `<div class="card" onclick="selectDesign(this, '${u}')"><img src="${u}" loading="lazy"><div class="cap">Diseño ${(batch-1)*10 + i + 1}</div></div>`;
    });
    html += `</div>`;
    addBubble(html, "bot", true);

    if (batch === 1) {
      setControls(`<button class="btn secondary" onclick="showGallery(2)">Ver más diseños</button>`);
    } else {
      setControls(`
        <button class="btn secondary" onclick="startCustom()">🎨 Quiero Personalizado</button>
        <a href="https://wa.me/${BUSINESS_WA}" target="_blank" class="btn whatsapp">💬 Contactar WhatsApp</a>
      `);
    }
    addBubble("Toca una imagen para ver los precios.", "bot");
  };

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

  // 4. DATOS DE ENVÍO + CARGA
  window.askShippingData = () => {
    const t = (STATE.base_price || 0) + (STATE.extra_control ? 16000 : 0);
    const combo = COMBOS.find(c => c.id === STATE.combo_id);
    addBubble(`Elegí: ${combo.title}. Total: $${t.toLocaleString()}`, "user");
    
    if (STATE.is_custom) {
        renderCustomUploadForm(combo);
    } else {
        renderShippingForm();
    }
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
              uploads.push({
                  file: fileIn.files[0],
                  detail: descIn.value.trim() || "Sin detalles específicos"
              });
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

      // VALIDACIONES RIGUROSAS
      if (rec.length < 5 || !rec.includes(" ")) {
         return showError("Por favor escribe tu Nombre y Apellido completos.");
      }

      // Valida email simple (texto@texto.texto)
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailRegex.test(email)) {
         return showError("El correo electrónico no es válido.");
      }

      // Valida celular: solo números, min 7 dígitos
      const waClean = wa.replace(/[^0-9]/g, '');
      if (waClean.length < 7) {
         return showError("El número de WhatsApp no es válido.");
      }

      if (!city || !bar || !addr) return showError("Falta ciudad, barrio o dirección.");

      STATE.receiver = rec;
      STATE.whatsapp = wa;
      STATE.email = email;
      STATE.city = city;
      STATE.barrio = bar;
      STATE.address = addr;

      addBubble("Enviando pedido... ⏳", "bot");
      
      const fd = new FormData();
      fd.append("name", STATE.name);
      fd.append("receiver_name", STATE.receiver);
      fd.append("whatsapp", STATE.whatsapp);
      fd.append("email", STATE.email); // Se envía al backend
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
             addBubble("Te enviaremos la propuesta de diseño por WhatsApp y Correo en un plazo máximo de 3 días.", "bot");
          } else {
             addBubble("✅ ¡Pedido Confirmado!", "bot");
             addBubble("Te contactaremos al WhatsApp para coordinar el despacho.", "bot");
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

@app.post("/submit")
async def submit(
    name: str = Form(...),
    receiver_name: str = Form(""),
    whatsapp: str = Form(...),
    email: str = Form(...), # Ahora es obligatorio
    city: str = Form(""),
    neighborhood: str = Form(""),
    address: str = Form(""),
    console: str = Form(...),
    design_choice: str = Form(""),
    has_design: str = Form("false"),
    images: Optional[List[UploadFile]] = File(None),
    image_details: Optional[List[str]] = Form(None)
):
    lead_data = {
        "name": name,
        "whatsapp": whatsapp,
        "console": console,
        "email": email,
        "design_choice": design_choice
    }
    
    lead = await _insert_lead(lead_data)
    lead_id = lead.get("id", "temp")

    img_report = []
    if not image_details: image_details = []

    if _truthy(has_design) and images:
        for i, file in enumerate(images):
            content = await file.read()
            if len(content) > MAX_IMAGE_BYTES: continue
            
            path = f"{lead_id}/{uuid.uuid4().hex}_{file.filename}"
            await _upload_to_storage(SUPABASE_BUCKET, path, content, file.content_type)
            url = _make_public_url(SUPABASE_BUCKET, path)
            
            detail_text = image_details[i] if i < len(image_details) else "Sin detalle"
            img_report.append(f"- URL: {url}\n  Nota: {detail_text}")
            
            await _insert_lead_image({
                "lead_id": lead_id,
                "storage_bucket": SUPABASE_BUCKET,
                "storage_path": path,
                "public_url": url,
                "original_filename": file.filename,
                "size_bytes": len(content),
                "details": detail_text 
            })

    email_body = f"""
    NUEVO PEDIDO SKINS
    ==================
    Cliente: {name}
    Recibe: {receiver_name}
    WhatsApp: {whatsapp}
    Email: {email}
    
    DATOS DE ENVÍO:
    ---------------
    Ciudad: {city}
    Barrio: {neighborhood}
    Dirección: {address}
    
    PEDIDO:
    -------
    Consola: {console}
    Detalle: {design_choice}
    """
    
    if img_report:
        email_body += "\n\nIMÁGENES ADJUNTAS (" + str(len(img_report)) + "):\n" + "\n".join(img_report)
    
    try:
        await run_in_threadpool(_send_email_sync, f"Pedido Skins: {name} ({city})", email_body)
    except: pass

    return JSONResponse({"ok": True})