import os
import random
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import requests
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from streamlit_gsheets import GSheetsConnection

# -----------------------------
# Configuración del Sistema
# -----------------------------
TIEMPO_RESERVA_MINUTOS = 15  # Tiempo que un boleto queda bloqueado esperando pago

def obtener_config(nombre: str, default: str = "") -> str:
    try:
        if hasattr(st, "secrets") and nombre in st.secrets:
            valor = st.secrets[nombre]
            if valor is not None:
                return str(valor)
    except Exception:
        pass
    return os.getenv(nombre, default)


MP_ACCESS_TOKEN = obtener_config("MP_ACCESS_TOKEN")
MP_NOTIFICATION_URL = obtener_config("MP_NOTIFICATION_URL")
MP_RETURN_URL = obtener_config("MP_RETURN_URL")
MP_CURRENCY_ID = obtener_config("MP_CURRENCY_ID", "MXN")


# -----------------------------
# Estilos CSS Corporativos (Azul, Blanco, Verde)
# -----------------------------
CSS_CUSTOM = """
<style>
    /* Variables de marca corporativa */
    :root {
        --primary-blue: #0A2540;
        --accent-green: #20C997;
        --bg-white: #FFFFFF;
    }
    
    /* Contenedor del mapa de boletos */
    .ticket-container {
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
        justify-content: center;
        padding: 20px 0;
    }
    
    /* Tarjeta de boleto (Ticketmaster style) */
    .ticket-card {
        width: 65px;
        height: 85px;
        border-radius: 8px;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        font-weight: 800;
        font-size: 18px;
        color: white;
        transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: 0 4px 6px rgba(0,0,0,0.07);
        border: 2px dashed rgba(255,255,255,0.4);
    }
    
    /* Efecto Hover interactivo */
    .ticket-card:hover {
        transform: translateY(-5px) scale(1.08);
        box-shadow: 0 10px 15px rgba(0,0,0,0.15);
        border: 2px solid white;
        cursor: crosshair;
    }
    
    /* Estados de los boletos */
    .ticket-available { 
        background: linear-gradient(135deg, #20C997, #0CA678); 
    }
    .ticket-reserved { 
        background: linear-gradient(135deg, #F59E0B, #D97706); 
    }
    .ticket-sold { 
        background: linear-gradient(135deg, #EF4444, #B91C1C); 
        opacity: 0.6; 
        pointer-events: none; /* Deshabilita el hover en vendidos */
    }
    
    .ticket-card small { 
        font-size: 10px; 
        margin-top: 6px; 
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }

    /* Tarjetas de Métricas */
    .metric-container {
        display: flex;
        justify-content: space-between;
        gap: 15px;
        margin-bottom: 20px;
    }
    .metric-box {
        flex: 1;
        background: white;
        padding: 15px;
        border-radius: 10px;
        text-align: center;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        border-top: 4px solid;
    }
    .metric-box h2 { margin: 0; font-size: 28px; font-weight: 800; color: #0A2540; }
    .metric-box p { margin: 0; font-size: 14px; color: #64748B; font-weight: 600; }
    .m-green { border-color: #20C997; }
    .m-yellow { border-color: #F59E0B; }
    .m-red { border-color: #EF4444; }
</style>
"""

# -----------------------------
# PDF del boleto (Lógica mantenida)
# -----------------------------
def dibujar_fondo_autenticidad(canvas, doc):
    width, height = doc.pagesize
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#F8FAFC"))
    canvas.rect(0, 0, width, height, fill=1, stroke=0)
    canvas.setStrokeColor(colors.HexColor("#0A2540"))
    canvas.setLineWidth(1)
    canvas.rect(24, 24, width - 48, height - 48, fill=0, stroke=1)
    
    canvas.setStrokeColor(colors.HexColor("#E2E8F0"))
    canvas.setLineWidth(0.35)
    step = 34
    limite = int(width + height)
    for x in range(-int(height), limite, step):
        canvas.line(x, 24, x + height, height - 24)

    sello_x, sello_y = width - 78, height - 82
    canvas.setStrokeColor(colors.HexColor("#20C997"))
    canvas.circle(sello_x, sello_y, 30, stroke=1, fill=0)
    canvas.circle(sello_x, sello_y, 22, stroke=1, fill=0)
    canvas.setFont("Helvetica-Bold", 6.5)
    canvas.setFillColor(colors.HexColor("#0A2540"))
    canvas.drawCentredString(sello_x, sello_y + 4, "BOLETO")
    canvas.drawCentredString(sello_x, sello_y - 4, "AUTÉNTICO")

    canvas.saveState()
    canvas.translate(width * 0.55, height * 0.52)
    canvas.rotate(28)
    canvas.setFillColor(colors.HexColor("#E5E7EB"))
    canvas.setFont("Helvetica-Bold", 34)
    canvas.drawCentredString(0, 0, "AUTENTICIDAD")
    canvas.restoreState()
    canvas.restoreState()

def generar_pdf_boleto(datos_boleto: Dict[str, Any]) -> str:
    nombre_archivo = datos_boleto.get("Comprobante", f"Boleto_{datos_boleto['ID_Boleto']}.pdf")
    doc = SimpleDocTemplate(nombre_archivo, pagesize=letter, rightMargin=32, leftMargin=32, topMargin=40, bottomMargin=32)
    story = []
    styles = getSampleStyleSheet()
    
    estilo_titulo = ParagraphStyle("TituloBoleto", parent=styles["Heading1"], fontSize=19, leading=23, textColor=colors.HexColor("#0A2540"), alignment=1, spaceAfter=8)
    estilo_normal = ParagraphStyle("TextoBoleto", parent=styles["Normal"], fontSize=10.5, leading=13, textColor=colors.HexColor("#334155"))
    estilo_pequeno = ParagraphStyle("TextoPequeno", parent=styles["Normal"], fontSize=8.5, leading=10, textColor=colors.HexColor("#64748B"), alignment=1)

    story.append(Spacer(1, 14))
    story.append(Paragraph("BOLETO OFICIAL", estilo_titulo))
    story.append(Spacer(1, 16))

    data = [
        [Paragraph("<b>ID de Boleto:</b>", estilo_normal), Paragraph(str(datos_boleto["ID_Boleto"]), estilo_normal)],
        [Paragraph("<b>Nombre:</b>", estilo_normal), Paragraph(datos_boleto["Nombre"], estilo_normal)],
        [Paragraph("<b>Correo:</b>", estilo_normal), Paragraph(datos_boleto["Correo"], estilo_normal)],
        [Paragraph("<b>Número telefónico:</b>", estilo_normal), Paragraph(str(datos_boleto["Numero_Telefonico"]), estilo_normal)],
        [Paragraph("<b>Evento:</b>", estilo_normal), Paragraph(datos_boleto["Evento"], estilo_normal)],
        [Paragraph("<b>N° de Boleto:</b>", estilo_normal), Paragraph(str(datos_boleto["Numero_Boleto"]), estilo_normal)],
        [Paragraph("<b>Precio Pagado:</b>", estilo_normal), Paragraph(f"${datos_boleto['Precio']:.2f} MXN", estilo_normal)],
        [Paragraph("<b>Método de Pago:</b>", estilo_normal), Paragraph(str(datos_boleto["Metodo_Pago"]), estilo_normal)],
        [Paragraph("<b>ID de Pago Mercado Pago:</b>", estilo_normal), Paragraph(str(datos_boleto.get("MercadoPago_Payment_ID", "")), estilo_normal)],
        [Paragraph("<b>Fecha de Emisión:</b>", estilo_normal), Paragraph(str(datos_boleto["Fecha_Compra"]), estilo_normal)],
    ]

    t = Table(data, colWidths=[165, 300])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFFFFF")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#FFFFFF"), colors.HexColor("#F8FAFC")]),
    ]))

    story.append(t)
    story.append(Spacer(1, 18))
    story.append(Paragraph("<b>Verificación:</b> Este comprobante corresponde a un registro único y oficial. Conserva este archivo para cualquier validación.", estilo_normal))
    story.append(Spacer(1, 16))
    story.append(Paragraph("¡Gracias por tu compra y mucha suerte!", estilo_pequeno))

    doc.build(story, onFirstPage=dibujar_fondo_autenticidad, onLaterPages=dibujar_fondo_autenticidad)
    return nombre_archivo


# -----------------------------
# Google Sheets (Lógica mantenida)
# -----------------------------
def columnas_ventas() -> list:
    return ["ID_Boleto", "Nombre", "Correo", "Evento", "Numero_Boleto", "Precio", "Metodo_Pago", "Codigo_Pago", "Fecha_Compra", "Numero_Telefonico", "Estado_Pago", "Referencia_Pago", "MercadoPago_Payment_ID", "MercadoPago_Preference_ID"]

def columnas_reservas() -> list:
    return ["External_Reference", "MercadoPago_Preference_ID", "MercadoPago_Payment_ID", "Numero_Boleto", "Nombre", "Correo", "Numero_Telefonico", "Monto", "Estado_Reserva", "Fecha_Creacion", "Expira_En", "Fecha_Actualizacion"]

def asegurar_columnas(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    for col in cols:
        if col not in df.columns:
            df[col] = ""
    return df[cols]

def obtener_datos_gsheets() -> Tuple[Optional[GSheetsConnection], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        try:
            df_ventas = conn.read(worksheet="Ventas", ttl=5)
            df_ventas = asegurar_columnas(df_ventas.dropna(how="all"), columnas_ventas()) if df_ventas is not None and not df_ventas.empty else pd.DataFrame(columns=columnas_ventas())
        except Exception:
            df_ventas = pd.DataFrame(columns=columnas_ventas())

        try:
            df_reservas = conn.read(worksheet="Reservas", ttl=5)
            df_reservas = asegurar_columnas(df_reservas.dropna(how="all"), columnas_reservas()) if df_reservas is not None and not df_reservas.empty else pd.DataFrame(columns=columnas_reservas())
        except Exception:
            df_reservas = pd.DataFrame(columns=columnas_reservas())

        return conn, df_ventas, df_reservas
    except Exception as e:
        st.error(f"⚠️ Error al conectar con Google Sheets: {e}")
        return None, None, None


# -----------------------------
# Mercado Pago (Lógica mantenida)
# -----------------------------
def mp_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {MP_ACCESS_TOKEN}", "Content-Type": "application/json"}

def crear_preferencia_mercado_pago(*, nombre: str, correo: str, telefono: str, numero_boleto: str, monto: float, external_reference: str) -> Tuple[str, str]:
    payload = {
        "items": [{"title": f"Rifa de celular - Boleto {numero_boleto}", "quantity": 1, "unit_price": float(monto), "currency_id": MP_CURRENCY_ID}],
        "payer": {"name": nombre, "email": correo},
        "external_reference": external_reference,
        "back_urls": {"success": MP_RETURN_URL, "pending": MP_RETURN_URL, "failure": MP_RETURN_URL},
        "auto_return": "approved",
        "statement_descriptor": "RIFA CELULAR",
        "metadata": {"telefono": telefono, "numero_boleto": numero_boleto},
        "binary_mode": True,
    }
    if MP_NOTIFICATION_URL: payload["notification_url"] = MP_NOTIFICATION_URL

    respuesta = requests.post("https://api.mercadopago.com/checkout/preferences", headers=mp_headers(), json=payload, timeout=30)
    data = respuesta.json()
    return data.get("id", ""), data.get("init_point") or data.get("sandbox_init_point") or ""

def obtener_pago_mercado_pago(payment_id: str) -> Dict[str, Any]:
    respuesta = requests.get(f"https://api.mercadopago.com/v1/payments/{payment_id}", headers=mp_headers(), timeout=30)
    return respuesta.json()


# -----------------------------
# Lógica de Estados y Reservas
# -----------------------------
def parse_datetime(valor: Any) -> Optional[datetime]:
    if pd.isna(valor) or not str(valor).strip(): return None
    try: return pd.to_datetime(str(valor).strip()).to_pydatetime()
    except: return None

def reserva_activa(row: pd.Series) -> bool:
    if str(row.get("Estado_Reserva", "")).strip().upper() != "PENDIENTE": return False
    expira = parse_datetime(row.get("Expira_En"))
    return True if expira is None else datetime.now() <= expira

def obtener_estado_boletos(df_ventas: pd.DataFrame, df_reservas: pd.DataFrame) -> dict:
    """Retorna un diccionario con el estado de cada boleto ('vendido', 'reservado')."""
    estados = {}
    
    # 1. Marcar los reservados activos
    if not df_reservas.empty and "Numero_Boleto" in df_reservas.columns:
        for _, row in df_reservas.iterrows():
            if reserva_activa(row):
                num = str(row["Numero_Boleto"]).strip().zfill(3)
                estados[num] = "reservado"

    # 2. Marcar los vendidos (Sobrescribe reservas si el pago se completó)
    if not df_ventas.empty and "Numero_Boleto" in df_ventas.columns:
        df_aprobadas = df_ventas[df_ventas["Estado_Pago"].astype(str).str.upper() == "APROBADO"] if "Estado_Pago" in df_ventas.columns else df_ventas
        for x in df_aprobadas["Numero_Boleto"].dropna().values:
            num = str(x).strip().zfill(3)
            estados[num] = "vendido"
            
    return estados


def registrar_reserva_cobro(conn: GSheetsConnection, orden: Dict[str, Any]) -> bool:
    try:
        df_reservas = conn.read(worksheet="Reservas", ttl=0)
        df_reservas = asegurar_columnas(df_reservas.dropna(how="all"), columnas_reservas()) if df_reservas is not None else pd.DataFrame(columns=columnas_reservas())
        df_actualizado = pd.concat([df_reservas, asegurar_columnas(pd.DataFrame([orden]), columnas_reservas())], ignore_index=True)
        conn.update(worksheet="Reservas", data=df_actualizado)
        return True
    except: return False

def actualizar_reserva(conn: GSheetsConnection, external_reference: str, updates: Dict[str, Any]):
    try:
        df_reservas = asegurar_columnas(conn.read(worksheet="Reservas", ttl=0).dropna(how="all"), columnas_reservas())
        mask = df_reservas["External_Reference"].astype(str).str.strip() == str(external_reference).strip()
        for key, value in updates.items():
            if key in df_reservas.columns: df_reservas.loc[mask, key] = value
        conn.update(worksheet="Reservas", data=df_reservas)
    except: pass


# -----------------------------
# Confirmación de Pago Automática
# -----------------------------
def procesar_retorno_pago(conn, df_ventas, df_reservas):
    qp = st.query_params
    payment_id = qp.get("payment_id") or qp.get("collection_id") or qp.get("id")
    if not payment_id: return df_ventas, df_reservas

    status = (qp.get("status") or "").strip().lower()
    external_reference = qp.get("external_reference") or qp.get("preference_id") or ""
    
    try: pago = obtener_pago_mercado_pago(str(payment_id))
    except: return df_ventas, df_reservas

    mp_status, mp_external_reference = str(pago.get("status", "")).strip().lower(), str(pago.get("external_reference", external_reference)).strip()
    
    if mp_status == "approved" and not (df_ventas["MercadoPago_Payment_ID"].astype(str).eq(str(payment_id)).any() if not df_ventas.empty else False):
        mask = df_reservas["External_Reference"].astype(str).str.strip() == mp_external_reference if not df_reservas.empty else []
        pending_match = df_reservas[mask].iloc[0].to_dict() if any(mask) else {}

        if pending_match:
            datos_boleto = {
                "ID_Boleto": f"RIFA-{int(datetime.now().timestamp())}",
                "Nombre": pending_match.get("Nombre", ""),
                "Correo": pending_match.get("Correo", ""),
                "Evento": "Rifa de celular",
                "Numero_Boleto": str(pending_match.get("Numero_Boleto", "")),
                "Precio": float(pago.get("transaction_amount", pending_match.get("Monto", 0))),
                "Metodo_Pago": "Mercado Pago",
                "Codigo_Pago": f"MP-{random.randint(10000, 99999)}",
                "Fecha_Compra": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Numero_Telefonico": pending_match.get("Numero_Telefonico", ""),
                "Estado_Pago": "APROBADO",
                "Referencia_Pago": mp_external_reference,
                "MercadoPago_Payment_ID": str(payment_id),
                "MercadoPago_Preference_ID": str(pago.get("order", {}).get("id", pending_match.get("MercadoPago_Preference_ID", ""))),
                "Comprobante": f"Boleto_RIFA-{int(datetime.now().timestamp())}.pdf",
            }
            
            # Registrar en Ventas
            cols = columnas_ventas()
            df_ventas = pd.concat([df_ventas, asegurar_columnas(pd.DataFrame([datos_boleto]), cols)], ignore_index=True) if not df_ventas.empty else asegurar_columnas(pd.DataFrame([datos_boleto]), cols)
            conn.update(worksheet="Ventas", data=df_ventas)
            
            # Actualizar Reserva
            actualizar_reserva(conn, mp_external_reference, {"MercadoPago_Payment_ID": str(payment_id), "Estado_Reserva": "APROBADA", "Fecha_Actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

            archivo_pdf = generar_pdf_boleto(datos_boleto)
            with open(archivo_pdf, "rb") as pdf_file: st.session_state.generated_pdf_bytes = pdf_file.read()
            st.session_state.generated_pdf_name, st.session_state.success_message = archivo_pdf, f"✅ Boleto generado con éxito. ID Pago: {payment_id}"
            
            st.query_params.clear()
            st.rerun()

    return df_ventas, df_reservas


# -----------------------------
# Fragmento del Dashboard en Vivo
# -----------------------------
@st.fragment(run_every=5)
def dashboard_plataforma():
    """Este fragmento consulta la base de datos y dibuja la interfaz visual cada 5 segundos sin reiniciar el formulario"""
    conn = st.connection("gsheets", type=GSheetsConnection)
    try:
        df_v = conn.read(worksheet="Ventas", ttl=5).dropna(how="all")
        df_r = conn.read(worksheet="Reservas", ttl=5).dropna(how="all")
    except:
        df_v = pd.DataFrame(columns=columnas_ventas())
        df_r = pd.DataFrame(columns=columnas_reservas())

    estados = obtener_estado_boletos(df_v, df_r)

    vendidos = sum(1 for v in estados.values() if v == "vendido")
    reservados = sum(1 for v in estados.values() if v == "reservado")
    disponibles = 100 - vendidos - reservados

    # Renderizar Métricas
    html_metrics = f"""
    <div class="metric-container">
        <div class="metric-box m-green">
            <h2>🟢 {disponibles}</h2>
            <p>Disponibles</p>
        </div>
        <div class="metric-box m-yellow">
            <h2>🟡 {reservados}</h2>
            <p>Reservados ({TIEMPO_RESERVA_MINUTOS} min)</p>
        </div>
        <div class="metric-box m-red">
            <h2>🔴 {vendidos}</h2>
            <p>Vendidos</p>
        </div>
    </div>
    """
    st.markdown(html_metrics, unsafe_allow_html=True)

    # Renderizar Grid de Boletos (Estilo Ticketmaster)
    grid_html = '<div class="ticket-container">'
    for i in range(100):
        num = f"{i:03d}"
        estado = estados.get(num, "disponible")

        if estado == "vendido":
            clase, label = "ticket-sold", "Vendido"
        elif estado == "reservado":
            clase, label = "ticket-reserved", "Reservado"
        else:
            clase, label = "ticket-available", "Disponible"

        grid_html += f'''
        <div class="ticket-card {clase}" title="Boleto {num} - {label}">
            <span>{num}</span>
            <small>{label}</small>
        </div>
        '''
    grid_html += '</div>'
    st.markdown(grid_html, unsafe_allow_html=True)


# -----------------------------
# Interfaz Principal
# -----------------------------
def main():
    st.set_page_config(page_title="Plataforma de Boletos", page_icon="🎟️", layout="wide", initial_sidebar_state="collapsed")
    st.markdown(CSS_CUSTOM, unsafe_allow_html=True)

    # Inicializar Session State
    for key in ["generated_pdf_bytes", "generated_pdf_name", "success_message"]:
        if key not in st.session_state: st.session_state[key] = None

    conn, df_ventas, df_reservas = obtener_datos_gsheets()
    if conn is None: return

    # Procesar retorno exitoso de pasarela de pago
    df_ventas, df_reservas = procesar_retorno_pago(conn, df_ventas, df_reservas)

    # Diseño a 2 columnas
    col_vis, col_form = st.columns([1.5, 1], gap="large")

    with col_vis:
        st.markdown(f"<h2 style='color: var(--primary-blue);'>🎟️ Selector de Boletos Interactivos</h2>", unsafe_allow_html=True)
        st.write("El mapa se actualiza en tiempo real automáticamente.")
        
        # Llamamos al fragmento auto-actualizable
        dashboard_plataforma()

    with col_form:
        st.markdown(f"<h2 style='color: var(--primary-blue);'>🛒 Finaliza tu Compra</h2>", unsafe_allow_html=True)
        
        # Mostrar Comprobante si la compra fue exitosa
        if st.session_state.success_message:
            st.success(st.session_state.success_message)
            st.download_button("📥 Descargar Comprobante Oficial (PDF)", st.session_state.generated_pdf_bytes, file_name=st.session_state.generated_pdf_name, mime="application/pdf", type="primary")
            if st.button("✖️ Nueva Compra"):
                st.session_state.generated_pdf_bytes, st.session_state.generated_pdf_name, st.session_state.success_message = None, None, None
                st.rerun()
            st.markdown("---")

        # Configuración del formulario
        precio_base = 100.00
        estados_actuales = obtener_estado_boletos(df_ventas, df_reservas)
        boletos_disponibles = [f"{i:03d}" for i in range(100) if estados_actuales.get(f"{i:03d}") not in ["vendido", "reservado"]]

        if not boletos_disponibles:
            st.error("⚠️ Sold Out: Todos los boletos han sido vendidos o están temporalmente reservados.")
            return

        with st.container(border=True):
            st.markdown("#### 1. Selecciona tu boleto")
            # Buscador Inteligente
            boleto_seleccionado = st.selectbox(
                "🔍 Busca o selecciona un número disponible:",
                options=[""] + boletos_disponibles,
                format_func=lambda x: f"Boleto N° {x}" if x else "Escribe el número aquí...",
            )

            st.markdown("#### 2. Datos del Titular")
            nombre = st.text_input("Nombre completo:")
            correo = st.text_input("Correo electrónico:")
            telefono = st.text_input("Número de celular:")
            
            st.write(f"**Total a pagar:** <span style='color: #20C997; font-size: 20px; font-weight: bold;'>${precio_base:.2f} MXN</span>", unsafe_allow_html=True)

            if st.button("💳 Pagar de Forma Segura", type="primary", use_container_width=True):
                if not boleto_seleccionado:
                    st.error("⚠️ Debes seleccionar un número de boleto del buscador.")
                elif not nombre or not correo or not telefono:
                    st.error("⚠️ Completa tu información de contacto para recibir el comprobante.")
                else:
                    external_reference = f"RIFA-{datetime.now().strftime('%Y%m%d%H%M%S')}-{boleto_seleccionado}"
                    fecha_creacion = datetime.now()
                    expira = fecha_creacion + timedelta(minutes=TIEMPO_RESERVA_MINUTOS)

                    reserva = {
                        "External_Reference": external_reference,
                        "MercadoPago_Preference_ID": "",
                        "MercadoPago_Payment_ID": "",
                        "Numero_Boleto": str(boleto_seleccionado),
                        "Nombre": nombre,
                        "Correo": correo,
                        "Numero_Telefonico": telefono,
                        "Monto": float(precio_base),
                        "Estado_Reserva": "PENDIENTE",
                        "Fecha_Creacion": fecha_creacion.strftime("%Y-%m-%d %H:%M:%S"),
                        "Expira_En": expira.strftime("%Y-%m-%d %H:%M:%S"),
                        "Fecha_Actualizacion": fecha_creacion.strftime("%Y-%m-%d %H:%M:%S"),
                    }

                    if registrar_reserva_cobro(conn, reserva):
                        try:
                            pref_id, checkout_url = crear_preferencia_mercado_pago(
                                nombre=nombre, correo=correo, telefono=telefono, 
                                numero_boleto=str(boleto_seleccionado), monto=precio_base, external_reference=external_reference
                            )
                            actualizar_reserva(conn, external_reference, {"MercadoPago_Preference_ID": pref_id, "Fecha_Actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                            
                            st.success(f"✅ ¡Excelente! Tu boleto **{boleto_seleccionado}** está reservado por {TIEMPO_RESERVA_MINUTOS} minutos.")
                            st.link_button("Ir a Pagar en Mercado Pago ➔", checkout_url, type="primary", use_container_width=True)
                            
                        except Exception as e:
                            st.error(f"⚠️ Error en la pasarela de pagos: {e}")
                    else:
                        st.error("⚠️ No se pudo reservar el boleto. Inténtalo de nuevo.")

if __name__ == "__main__":
    main()
    
