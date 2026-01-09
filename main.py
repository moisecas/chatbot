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

ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

# -----------------------------
# Helpers & Database
# -----------------------------
def _sb_headers() -> dict:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Faltan credenciales de Supabase.")
    return {"apikey": SUPABASE_SERVICE_ROLE_KEY, "authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"}

def _basic_email_ok(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip(), re.I))

def _clean_phone(s: str) -> str:
    return re.sub(r"[^\d\+\s\(\)]", "", s.strip())

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
  <title>Skins - Combos y Diseños</title>
  <style>
    :root {
      --bg: #0b1220;
      --bubble-bot: #1e293b; 
      --bubble-user: #2563eb;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --accent: #3b82f6;
      --danger: #ef4444;
      --ok: #22c55e;
      --input-bg: #f8fafc;
      --input-text: #0f172a;
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
    header .title { font-weight: 700; }
    
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
      background: var(--input-bg); color: var(--input-text); font-size: 16px; outline: none;
    }
    .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .btn {
      flex: 1; border: none; border-radius: 10px; padding: 12px;
      background: var(--accent); color: white; font-weight: 600; cursor: pointer; text-align: center; text-decoration: none;
    }
    .btn.secondary { background: #334155; }
    .btn.whatsapp { background: #25D366; }
    .btn.ok { background: var(--ok); color: #000; }

    /* Grid Galería */
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
    .card {
      border-radius: 8px; overflow: hidden; background: #000; position: relative; border: 2px solid transparent; cursor: pointer;
    }
    .card.selected { border-color: var(--ok); }
    .card img { width: 100%; height: 110px; object-fit: cover; display: block; }
    .card .cap { font-size: 10px; padding: 4px; text-align: center; color: #ccc; }

    /* Tarjetas de Combos */
    .combo-card {
      background: #1e293b; border: 1px solid #334155; border-radius: 10px;
      padding: 12px; margin-bottom: 8px; cursor: pointer; transition: 0.2s;
    }
    .combo-card:hover { border-color: var(--accent); }
    .combo-card.active { border-color: var(--ok); background: rgba(34, 197, 94, 0.1); }
    .combo-head { display: flex; justify-content: space-between; font-weight: bold; margin-bottom: 4px; color: var(--ok); }
    .combo-desc { font-size: 13px; color: #cbd5e1; margin-bottom: 4px; }
    .combo-note { font-size: 11px; color: var(--muted); font-style: italic; }
    
    .check-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; padding: 8px; background: rgba(255,255,255,0.05); border-radius: 8px; }
    .check-row input { width: auto; margin: 0; }

    .tiny { font-size: 12px; color: var(--muted); margin-top: 4px; }
  </style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="title">Skins Colombia</div>
    <button class="btn secondary" style="flex:0; padding:6px 12px; font-size:13px;" onclick="window.location.reload()">Reiniciar</button>
  </header>
  <section class="chat" id="msgs"></section>
  <div class="controls" id="controls"></div>
</div>

<script>
  // -----------------------------
  // CONFIGURACIÓN DE DATOS
  // -----------------------------
  const BUSINESS_WA = "__BUSINESS_WA_NUMBER__";
  
  // 20 Diseños placeholder (10 y 10)
  const DESIGN_BATCH_1 = [
    "https://images.unsplash.com/photo-1534423861386-85a16f5d13fd?w=300",
    "https://images.unsplash.com/photo-1511512578047-dfb367046420?w=300",
    "https://images.unsplash.com/photo-1542751371-adc38448a05e?w=300",
    "https://images.unsplash.com/photo-1593118247619-e2d6f056869e?w=300",
    "https://images.unsplash.com/photo-1612287230202-1ff1d85d1bdf?w=300",
    "https://images.unsplash.com/photo-1560253023-3ec5d502959f?w=300",
    "https://images.unsplash.com/photo-1626379953822-baec19c3accd?w=300",
    "https://images.unsplash.com/photo-1579373903781-fd5c0c30c4cd?w=300",
    "https://images.unsplash.com/photo-1616588589676-60b30c3c1681?w=300",
    "https://images.unsplash.com/photo-1605901309584-818e25960b8f?w=300"
  ];

  const DESIGN_BATCH_2 = [
    "https://images.unsplash.com/photo-1600080972464-8cb882e6a9f0?w=300",
    "https://images.unsplash.com/photo-1552820728-8b83bb6b773f?w=300",
    "https://images.unsplash.com/photo-1518709268805-4e9042af9f23?w=300",
    "https://images.unsplash.com/photo-1493711662062-fa541adb3fc8?w=300",
    "https://images.unsplash.com/photo-1513542789411-b6a5d4f31634?w=300",
    "https://images.unsplash.com/photo-1605810230434-7631ac76ec81?w=300",
    "https://images.unsplash.com/photo-1550745165-9bc0b252726f?w=300",
    "https://images.unsplash.com/photo-1531297461136-82lw9f23?w=300",
    "https://images.unsplash.com/photo-1492684223066-81342ee5ff30?w=300",
    "https://images.unsplash.com/photo-1592155931584-901ac15763e3?w=300"
  ];

  const COMBOS = [
    { id: "c1", title: "Combo 1", price: 80000, desc: "2 controles + Arriba + Frontal + Abajo/Lados", note: "Envío y diseño gratis. Pago contra entrega (Efectivo)." },
    { id: "c2", title: "Combo 2", price: 65000, desc: "Arriba + Frontal + Abajo o Lados", note: "Envío y diseño gratis. Pago contra entrega (Efectivo)." },
    { id: "c3", title: "Combo 3", price: 55000, desc: "Arriba + Frontal", note: "Envío y diseño gratis. Pago contra entrega (Efectivo)." },
    { id: "c4", title: "Combo 4", price: 60000, desc: "Arriba + Frontal + 2 Mandos", note: "Envío y diseño gratis. Pago contra entrega (Efectivo)." },
    // Exclusivos
    { id: "c6", title: "Combo 6 (Solo PS5)", price: 40000, desc: "Solo Frontal", note: "Envío y diseño gratis. Pago contra entrega.", only: ["PS5 Fat", "PS5 Slim"] },
    { id: "c7", title: "Combo 7 (Solo Series X)", price: 60000, desc: "4 Caras de la consola", note: "Envío y diseño gratis. Pago contra entrega.", only: ["Xbox Series X"] }
  ];

  // Estado global
  const STATE = {
    name: "", console: "", design_url: "", combo_id: "", extra_control: false, is_custom: false
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
    
    addBubble(`Un gusto ${v}. ¿Qué consola tienes?`);
    setControls(`
      <div class="row">
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
          <option value="Other">Otra</option>
        </select>
        <button class="btn" onclick="handleConsole()">Ver Diseños</button>
      </div>
    `);
  };

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
    addBubble(batch === 1 ? "Aquí tienes los primeros 10 diseños:" : "Aquí hay otros 10 diseños diferentes:");
    
    let html = `<div class="grid">`;
    imgs.forEach((u, i) => {
      html += `<div class="card" onclick="selectDesign(this, '${u}')"><img src="${u}" loading="lazy"><div class="cap">Diseño ${(batch-1)*10 + i + 1}</div></div>`;
    });
    html += `</div>`;
    addBubble(html, "bot", true);

    if (batch === 1) {
      setControls(`<div class="row"><button class="btn secondary" onclick="showGallery(2)">Ver más diseños</button></div>`);
    } else {
      setControls(`
        <div class="row">
          <button class="btn secondary" onclick="startCustom()">🎨 Quiero Personalizado</button>
          <a href="https://wa.me/${BUSINESS_WA}" target="_blank" class="btn whatsapp">💬 Contactar WhatsApp</a>
        </div>
      `);
    }
    addBubble("Toca una imagen para ver los precios y combos.", "bot");
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
    addBubble("Vale, prefieres personalizado.", "user");
    addBubble("Para personalizado, por favor selecciona el combo primero y al final adjuntarás la imagen o la enviarás por WhatsApp.");
    showCombos();
  }

  // 3. COMBOS Y PRECIOS
  window.showCombos = () => {
    // Filtrar combos según consola
    const available = COMBOS.filter(c => {
      if (!c.only) return true; // Para todos
      return c.only.includes(STATE.console);
    });

    let html = `<div class="tiny">Selecciona un combo para continuar:</div>`;
    available.forEach(c => {
        html += `
        <div class="combo-card" onclick="selectCombo(this, '${c.id}', ${c.price})">
            <div class="combo-head"><span>${c.title}</span> <span>$${c.price.toLocaleString()}</span></div>
            <div class="combo-desc">${c.desc}</div>
            <div class="combo-note">${c.note}</div>
        </div>`;
    });

    // Control adicional checkbox
    html += `
      <div class="check-row">
        <input type="checkbox" id="chkExtra" onchange="toggleExtra(this)">
        <label for="chkExtra">Control adicional (+$16.000)</label>
      </div>
      <div class="row" style="margin-top:10px">
         <button class="btn ok" id="btnOrder" disabled onclick="askData()">Enviar Pedido</button>
         <button class="btn whatsapp" onclick="consultarCombo()">Dudas del combo</button>
      </div>
    `;
    
    addBubble("💰 Mira los combos disponibles para " + STATE.console + ":", "bot");
    addBubble(html, "bot", true);
    setControls(""); // Limpiamos controles flotantes, todo está en el chat ahora
    scrollBot();
  };

  window.selectCombo = (el, id, price) => {
    document.querySelectorAll(".combo-card").forEach(c => c.classList.remove("active"));
    el.classList.add("active");
    STATE.combo_id = id;
    STATE.base_price = price;
    document.getElementById("btnOrder").disabled = false;
    document.getElementById("btnOrder").textContent = `Pedir ($${calcTotal().toLocaleString()})`;
  };

  window.toggleExtra = (chk) => {
    STATE.extra_control = chk.checked;
    if (STATE.combo_id) {
        document.getElementById("btnOrder").textContent = `Pedir ($${calcTotal().toLocaleString()})`;
    }
  };

  function calcTotal() {
      return (STATE.base_price || 0) + (STATE.extra_control ? 16000 : 0);
  }

  window.consultarCombo = () => {
      const msg = `Hola, me interesa el combo para ${STATE.console}, pero tengo dudas.`;
      window.open(`https://wa.me/${BUSINESS_WA}?text=${encodeURIComponent(msg)}`, '_blank');
  };

  // 4. DATOS FINALES
  window.askData = () => {
      const total = calcTotal();
      const comboName = COMBOS.find(c => c.id === STATE.combo_id).title;
      const extraTxt = STATE.extra_control ? " + Control Adicional" : "";
      
      addBubble(`Has elegido: ${comboName}${extraTxt}. Total: $${total.toLocaleString()}`, "user");
      
      // Si es personalizado, pedimos subir foto. Si no, directo a contacto.
      if(STATE.is_custom) {
          addBubble("Como es personalizado, sube tu imagen de referencia:");
          setControls(`
            <div class="row">
              <input type="file" id="fileCustom" accept="image/*">
              <button class="btn" onclick="preSubmit(true)">Enviar</button>
            </div>
            <div class="tiny">Si es muy pesada, envíala luego por WhatsApp.</div>
          `);
      } else {
          addBubble("Perfecto. Dame tu WhatsApp para coordinar el envío y pago contra entrega.");
          setControls(`
             <div class="row">
               <input id="inWa" placeholder="Número WhatsApp..." type="tel">
               <button class="btn ok" onclick="preSubmit(false)">Finalizar Pedido</button>
             </div>
          `);
      }
  };

  async function preSubmit(hasFile) {
      let wa = "";
      let file = null;

      if(hasFile) {
         const fi = document.getElementById("fileCustom");
         if(fi.files.length > 0) file = fi.files[0];
         // Si es personalizado necesitamos el WA aunque sea en otro prompt, 
         // para simplificar lo pido aquí o asumo que ya lo tengo si lo pedí antes.
         // En este flujo simplificado, pedimos WA ahora si no está.
         addBubble("Y por último, tu WhatsApp:", "bot");
         setControls(`<div class="row"><input id="inWaFinal" placeholder="WhatsApp..."><button class="btn" onclick="finalSubmit('${file ? 'yes':'no'}')">Enviar Todo</button></div>`);
         if(file) STATE.customFile = file;
         return;
      }
      
      wa = document.getElementById("inWa").value;
      if(!wa) return showError("Falta el WhatsApp");
      STATE.whatsapp = wa;
      submitLead();
  }

  window.finalSubmit = (hasFileStr) => {
      const wa = document.getElementById("inWaFinal").value;
      if(!wa) return showError("WhatsApp obligatorio");
      STATE.whatsapp = wa;
      submitLead();
  };

  async function submitLead() {
      addBubble("Enviando pedido... ⏳", "bot");
      
      const fd = new FormData();
      fd.append("name", STATE.name);
      fd.append("whatsapp", STATE.whatsapp);
      fd.append("email", "pendiente@skins.com"); // Placeholder si no se pide email
      fd.append("console", STATE.console);
      
      const combo = COMBOS.find(c => c.id === STATE.combo_id);
      let detail = `Combo: ${combo.title}. Total: $${calcTotal()}. `;
      if(STATE.extra_control) detail += "Con Control Adicional. ";
      detail += STATE.is_custom ? "Diseño Personalizado." : `Diseño Galería: ${STATE.design_url}`;

      fd.append("design_choice", detail);
      fd.append("whatsapp_prefill", `Hola soy ${STATE.name}, quiero el ${combo.title} para ${STATE.console}. Total $${calcTotal()}.`);
      
      if(STATE.is_custom && STATE.customFile) {
          fd.append("has_design", "true");
          fd.append("images", STATE.customFile);
      }

      try {
          await fetch("/submit", { method: "POST", body: fd });
          addBubble("✅ ¡Pedido enviado! Te escribiremos al WhatsApp para confirmar.", "bot");
          
          const finalMsg = `Hola, acabo de pedir el ${combo.title} para ${STATE.console} en la web. Mi nombre es ${STATE.name}.`;
          setControls(`<a href="https://wa.me/${BUSINESS_WA}?text=${encodeURIComponent(finalMsg)}" class="btn whatsapp">Abrir WhatsApp</a>`);
      } catch(e) {
          addBubble("Error de conexión. Escríbenos por WhatsApp.", "bot");
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
    whatsapp: str = Form(...),
    console: str = Form(...),
    email: str = Form("no-email@provided.com"), # Opcional en este flujo
    design_choice: str = Form(""),
    has_design: str = Form("false"),
    whatsapp_prefill: str = Form(""),
    images: Optional[List[UploadFile]] = File(None),
):
    # Lógica de guardado simplificada
    lead_data = {
        "name": name,
        "email": email,
        "whatsapp": whatsapp,
        "console": console,
        "design_choice": design_choice,
        "whatsapp_prefill": whatsapp_prefill
    }
    
    lead = await _insert_lead(lead_data)
    lead_id = lead.get("id", "temp")

    if _truthy(has_design) and images:
        for file in images:
            content = await file.read()
            if len(content) > MAX_IMAGE_BYTES: continue # Skip si es gigante
            
            path = f"{lead_id}/{uuid.uuid4().hex}_{file.filename}"
            await _upload_to_storage(SUPABASE_BUCKET, path, content, file.content_type)
            
            # Guardar referencia
            await _insert_lead_image({
                "lead_id": lead_id,
                "storage_bucket": SUPABASE_BUCKET,
                "storage_path": path,
                "public_url": _make_public_url(SUPABASE_BUCKET, path),
                "original_filename": file.filename,
                "size_bytes": len(content)
            })

    # Notificación Email
    try:
        await run_in_threadpool(_send_email_sync, 
            f"Nuevo Pedido: {name} - {console}", 
            f"Detalles: {design_choice}\nWhatsApp: {whatsapp}"
        )
    except: pass

    return JSONResponse({"ok": True})