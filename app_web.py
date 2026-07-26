import os
import random
import re
import uuid
import base64
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import io

import pandas as pd
import requests
import streamlit as st
import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak
from streamlit_gsheets import GSheetsConnection

# SDK de Mercado Pago
import mercadopago

# -----------------------------
# Configuración del Sistema y BBVA CoDi
# -----------------------------
TIEMPO_RESERVA_MINUTOS = 1440  # 24 hrs (Reserva formal en Base de Datos)
TIEMPO_PRERESERVA_MINUTOS = 15 # 15 mins (Carrito temporal en Memoria)

def obtener_config(nombre: str, default: str = "") -> str:
    try:
        if hasattr(st, "secrets") and nombre in st.secrets:
            return str(st.secrets[nombre]).strip()
    except Exception:
        pass
    env_valor = os.getenv(nombre)
    if env_valor is not None:
        return str(env_valor).strip()
    return default

MP_ACCESS_TOKEN = obtener_config("MP_ACCESS_TOKEN")
MP_NOTIFICATION_URL = obtener_config("MP_NOTIFICATION_URL")
MP_RETURN_URL = obtener_config("MP_RETURN_URL")
MP_CURRENCY_ID = obtener_config("MP_CURRENCY_ID", "MXN")

# Configuración CoDi BBVA (Datos del beneficiario)
BBVA_CLABE = obtener_config("BBVA_CLABE", "012180000000000000") # Reemplazar con CLABE BBVA real
BBVA_BENEFICIARIO = obtener_config("BBVA_BENEFICIARIO", "RIFAS Y EVENTOS SA DE CV")

if not MP_ACCESS_TOKEN:
    st.warning("⚠️ Modo Desarrollo: No se detectó 'MP_ACCESS_TOKEN'. Mercado Pago fallará si se invoca.")

sdk = mercadopago.SDK(MP_ACCESS_TOKEN) if MP_ACCESS_TOKEN else None

# -----------------------------
# Caché Global para Pre-Reservas (Memoria RAM)
# -----------------------------
@st.cache_resource
def obtener_pre_reservas_globales() -> dict:
    return {}

def limpiar_pre_reservas_expiradas(pre_reservas: dict):
    ahora = datetime.now()
    expirados = [k for k, v in list(pre_reservas.items()) if v['expires_at'] < ahora]
    for k in expirados:
        del pre_reservas[k]

# -----------------------------
# Estilos CSS
# -----------------------------
CSS_CUSTOM = """
<style>
   [data-testid="column"] { padding: 0 4px !important; }
   [data-testid="stButton"] button {
       width: 100%; height: 55px; padding: 0;
       font-weight: 700; font-size: 14px; transition: all 0.2s;
   }
   [data-testid="stButton"] button:hover {
       transform: scale(1.02); border-color: #004481;
   }
   .metric-container { display: flex; justify-content: space-between; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
   .metric-box {
       flex: 1; min-width: 120px; background: white; padding: 10px; border-radius: 8px; text-align: center;
       box-shadow: 0 2px 5px rgba(0,0,0,0.05); border-top: 4px solid;
   }
   .metric-box h2 { margin: 0; font-size: 20px; font-weight: 800; color: #0A2540; }
   .metric-box p { margin: 0; font-size: 12px; color: #64748B; font-weight: 600; }
   .m-green { border-color: #20C997; }
   .m-gray { border-color: #94A3B8; }
   .m-yellow { border-color: #F59E0B; }
   .m-red { border-color: #EF4444; }
   
   /* Estilos BBVA CoDi */
   .codi-card {
       background: #F4F8FA; border: 2px solid #004481; border-radius: 12px;
       padding: 20px; text-align: center; margin: 15px 0;
   }
   .codi-title { color: #004481; font-weight: 800; font-size: 18px; margin-bottom: 10px; }
   .codi-instruction { font-size: 13px; color: #333; margin-bottom: 15px; }
   .codi-ref { background: #004481; color: white; padding: 8px 15px; border-radius: 6px; font-weight: bold; font-family: monospace; display: inline-block; margin-top: 10px; }
</style>
"""

# -----------------------------
# Funciones Bancarias BBVA CoDi
# -----------------------------
def generar_qr_codi_bbva(monto: float, referencia: str, concepto: str) -> str:
    """
    Genera la carga útil de pago estándar CoDi/SPEI y retorna el QR en formato Base64.
    Compatible con la app BBVA México y cualquier app bancaria con lector CoDi/QR SPEI.
    """
    payload_codi = {
        "clabe": BBVA_CLABE,
        "nombre": BBVA_BENEFICIARIO,
        "monto": f"{monto:.2f}",
        "ref": referencia,
        "concepto": f"Boletos {concepto}"[:40],
        "banco": "BBVA MEXICO",
        "tipo": "CODI_SPEI"
    }
    
    cadena_qr = f"SPEI|clabe:{payload_codi['clabe']}|nombre:{payload_codi['nombre']}|monto:{payload_codi['monto']}|ref:{payload_codi['ref']}|concepto:{payload_codi['concepto']}"
    
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
    qr.add_data(cadena_qr)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="#004481", back_color="white")
    
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return f"data:image/png;base64,{img_str}"

def verificar_pago_codi_servidor(referencia: str) -> bool:
    """
    Simulación de consulta de liquidación interbancaria SPEI / CoDi vía API BBVA Net Cash.
    En un entorno productivo real, esta función consulta el endpoint de Banxico o el Webhook recibido de BBVA.
    """
    return True

# -----------------------------
# Funciones PDF y Mercado Pago
# -----------------------------
def dibujar_fondo_autenticidad(canvas, doc):
    width, height = doc.pagesize
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#F8FAFC"))
    canvas.rect(0, 0, width, height, fill=1, stroke=0)
    canvas.setStrokeColor(colors.HexColor("#0A2540"))
    canvas.setLineWidth(1)
    canvas.rect(24, 24, width - 48, height - 48, fill=0, stroke=1)
    canvas.restoreState()

def generar_pdf_boleto(datos_boletos: List[Dict[str, Any]]) -> str:
    codigo_pago = datos_boletos[0].get("Codigo_Pago", "Generico")
    nombre_archivo = f"Boletos_Oficiales_{codigo_pago}.pdf"
    doc = SimpleDocTemplate(nombre_archivo, pagesize=letter, rightMargin=32, leftMargin=32, topMargin=40, bottomMargin=32)
    story, styles = [], getSampleStyleSheet()
    estilo_titulo = ParagraphStyle("Titulo", parent=styles["Heading1"], fontSize=19, textColor=colors.HexColor("#0A2540"), alignment=1)
    estilo_normal = ParagraphStyle("Texto", parent=styles["Normal"], fontSize=10.5, leading=13, textColor=colors.HexColor("#334155"))
    
    for idx, boleto in enumerate(datos_boletos):
        story.append(Paragraph("BOLETO OFICIAL DE COMPRA", estilo_titulo))
        story.append(Spacer(1, 16))
        precio_float = float(boleto.get('Precio', 0))
        data = [
            [Paragraph("<b>ID de Boleto:</b>", estilo_normal), Paragraph(str(boleto["ID_Boleto"]), estilo_normal)],
            [Paragraph("<b>Nombre:</b>", estilo_normal), Paragraph(str(boleto["Nombre"]), estilo_normal)],
            [Paragraph("<b>N° de Boleto:</b>", estilo_normal), Paragraph(str(boleto["Numero_Boleto"]), estilo_normal)],
            [Paragraph("<b>Precio Pagado:</b>", estilo_normal), Paragraph(f"${precio_float:.2f} {MP_CURRENCY_ID}", estilo_normal)],
            [Paragraph("<b>Método de Pago:</b>", estilo_normal), Paragraph(str(boleto.get("Metodo_Pago", "SPEI/CoDi")).upper(), estilo_normal)],
            [Paragraph("<b>Ref / ID Pago:</b>", estilo_normal), Paragraph(str(boleto.get("MercadoPago_Payment_ID", boleto.get("Referencia_Pago", "N/A"))), estilo_normal)],
            [Paragraph("<b>Fecha:</b>", estilo_normal), Paragraph(str(boleto.get("Fecha_Compra", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))), estilo_normal)]
        ]
        t = Table(data, colWidths=[165, 300])
        t.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0,0), (-1,-1), 8),
            ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ]))
        story.append(t)
        if idx < len(datos_boletos) - 1: story.append(PageBreak())
            
    doc.build(story, onFirstPage=dibujar_fondo_autenticidad, onLaterPages=dibujar_fondo_autenticidad)
    return nombre_archivo

def crear_preferencia_mercado_pago(nombre, apellidos, correo, telefono, numeros_boletos: list, monto_unitario, external_reference, custom_return_scheme: Optional[str] = None):
    if not sdk: return "", ""
    url_retorno = custom_return_scheme if custom_return_scheme else MP_RETURN_URL
    titulos_boletos = ", ".join(numeros_boletos)
    
    preference_data = {
        "items": [{"title": f"Rifa celular - Boletos: {titulos_boletos}", "quantity": len(numeros_boletos), "unit_price": float(monto_unitario), "currency_id": MP_CURRENCY_ID}],
        "payer": {"name": nombre.strip(), "surname": apellidos.strip() or "Sin Apellido", "email": correo, "phone": {"area_code": "52", "number": telefono}},
        "external_reference": external_reference,
        "payment_methods": {"excluded_payment_methods": [], "excluded_payment_types": [], "installments": 1},
        "statement_descriptor": "RIFA CELULAR"
    }
    if url_retorno:
        if not url_retorno.startswith("http://") and not url_retorno.startswith("https://"):
            url_retorno = f"https://{url_retorno}"
        preference_data["back_urls"] = {"success": url_retorno, "pending": url_retorno, "failure": url_retorno}
        preference_data["auto_return"] = "approved"
        
    preference = sdk.preference().create(preference_data).get("response", {})
    if "id" not in preference: raise Exception(f"Rechazado por MP: {preference.get('message', 'Error en credenciales o URL de retorno')}")
    return preference.get("id", ""), preference.get("init_point") or preference.get("sandbox_init_point", "")

# -----------------------------
# Funciones Hojas de Cálculo
# -----------------------------
def buscar_pago_en_mercadopago(external_reference: str) -> Optional[Dict]:
    if not sdk: return None
    try:
        pagos = sdk.payment().search({"external_reference": external_reference}).get("response", {}).get("results", [])
        return next((p for p in pagos if p.get("status") == "approved"), None)
    except: return None

def columnas_ventas() -> list: return ["ID_Boleto", "Nombre", "Correo", "Evento", "Numero_Boleto", "Precio", "Metodo_Pago", "Codigo_Pago", "Fecha_Compra", "Numero_Telefonico", "Estado_Pago", "Referencia_Pago", "MercadoPago_Payment_ID", "MercadoPago_Preference_ID"]
def columnas_reservas() -> list: return ["External_Reference", "MercadoPago_Preference_ID", "MercadoPago_Payment_ID", "Numero_Boleto", "Nombre", "Correo", "Numero_Telefonico", "Monto", "Estado_Reserva", "Fecha_Creacion", "Expira_En", "Fecha_Actualizacion"]

def asegurar_columnas(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    for col in cols:
        if col not in df.columns: df[col] = ""
    return df[cols]

def parse_ticket_number(val: Any) -> str:
    if pd.isna(val) or str(val).strip() == "": return ""
    try: return f"{int(float(val)):03d}"
    except: return str(val).strip().zfill(3)

def obtener_estado_boletos_bd(df_ventas: pd.DataFrame, df_reservas: pd.DataFrame) -> dict:
    estados = {}
    if not df_reservas.empty and "Numero_Boleto" in df_reservas.columns:
        for _, row in df_reservas.iterrows():
            if str(row.get("Estado_Reserva", "")).strip().upper() == "PENDIENTE":
                try: expira = pd.to_datetime(str(row.get("Expira_En"))).to_pydatetime()
                except: expira = None
                if expira is None or datetime.now() <= expira:
                    num = parse_ticket_number(row["Numero_Boleto"])
                    if num: estados[num] = "reservado_db"

    if not df_ventas.empty and "Numero_Boleto" in df_ventas.columns:
        for _, row in df_ventas.iterrows():
            if str(row.get("Estado_Pago", "")).strip().upper() in ["APROBADO", "VENDIDO"]:
                num = parse_ticket_number(row["Numero_Boleto"])
                if num: estados[num] = "vendido_db"
    return estados

def registrar_reserva_cobro(conn: GSheetsConnection, ordenes: List[Dict[str, Any]]) -> Tuple[bool, str]:
    try:
        try: df_r = conn.read(worksheet="Reservas", ttl=0)
        except: df_r = pd.DataFrame(columns=columnas_reservas())
        
        df_actualizado = pd.concat([asegurar_columnas(df_r.dropna(how="all"), columnas_reservas()), asegurar_columnas(pd.DataFrame(ordenes), columnas_reservas())], ignore_index=True)
        conn.update(worksheet="Reservas", data=df_actualizado)
        return True, "Éxito"
    except Exception as e: return False, str(e)

def actualizar_pago_en_hojas(conn: GSheetsConnection, payment_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    ext_ref = payment_info.get("external_reference", "")
    pago_id = str(payment_info.get("id", payment_info.get("codi_id", "")))
    metodo_pago = payment_info.get("payment_type_id", "codi_bbva")
    
    try: df_r = asegurar_columnas(conn.read(worksheet="Reservas", ttl=0).dropna(how="all"), columnas_reservas())
    except: df_r = pd.DataFrame(columns=columnas_reservas())
    try: df_v = asegurar_columnas(conn.read(worksheet="Ventas", ttl=0).dropna(how="all"), columnas_ventas())
    except: df_v = pd.DataFrame(columns=columnas_ventas())
    
    filtro_existente = df_v["MercadoPago_Payment_ID"].astype(str) == str(pago_id)
    if filtro_existente.any() and pago_id != "": 
        return df_v[filtro_existente].to_dict(orient="records")
            
    filtro_reserva = df_r["External_Reference"] == ext_ref
    if filtro_reserva.any():
        df_r.loc[filtro_reserva, "Estado_Reserva"] = "PAGADO"
        df_r.loc[filtro_reserva, "MercadoPago_Payment_ID"] = pago_id
        conn.update(worksheet="Reservas", data=df_r)
        
        nuevas_ventas = []
        for _, r in df_r[filtro_reserva].iterrows():
            nuevas_ventas.append({
                "ID_Boleto": f"BOL-{random.randint(10000, 99999)}", "Nombre": r["Nombre"], "Correo": r["Correo"],
                "Evento": "Rifa de Celular", "Numero_Boleto": r["Numero_Boleto"], "Precio": r["Monto"],
                "Metodo_Pago": metodo_pago, "Codigo_Pago": pago_id or f"SPEI-{random.randint(1000,9999)}",
                "Fecha_Compra": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Numero_Telefonico": r["Numero_Telefonico"],
                "Estado_Pago": "APROBADO", "Referencia_Pago": ext_ref, "MercadoPago_Payment_ID": pago_id, 
                "MercadoPago_Preference_ID": r.get("MercadoPago_Preference_ID", "")
            })
        conn.update(worksheet="Ventas", data=pd.concat([df_v, pd.DataFrame(nuevas_ventas)], ignore_index=True))
        return nuevas_ventas
    return []

# -----------------------------
# Componentes UI Interactivos
# -----------------------------
@st.fragment(run_every=5)
def renderizar_mapa_interactivo():
    mi_sesion = st.session_state.session_id
    pre_reservas = obtener_pre_reservas_globales()
    limpiar_pre_reservas_expiradas(pre_reservas)

    # BLINDAJE: Si NO se ha generado un cobro aún, limpiamos boletos expirados.
    # Si YA estamos en la pantalla de pago (CoDi o Mercado Pago), mantenemos firme la selección actual.
    if not (st.session_state.get("pago_generado_url") or st.session_state.get("qr_codi_base64")):
        st.session_state.selected_tickets = [t for t in st.session_state.selected_tickets if t in pre_reservas and pre_reservas[t]['session_id'] == mi_sesion]

    conn = st.connection("gsheets", type=GSheetsConnection)
    try:
        df_v = conn.read(worksheet="Ventas", ttl=5).dropna(how="all")
        df_r = conn.read(worksheet="Reservas", ttl=5).dropna(how="all")
    except: df_v, df_r = pd.DataFrame(columns=columnas_ventas()), pd.DataFrame(columns=columnas_reservas())

    estados_bd = obtener_estado_boletos_bd(df_v, df_r)
    estados_pantalla = {}
    vendidos, reservados_bd, pre_reservados_otros = 0, 0, 0
    
    for i in range(100):
        num = f"{i:03d}"
        if num in estados_bd:
            estados_pantalla[num] = estados_bd[num]
            if estados_bd[num] == "vendido_db": vendidos += 1
            elif estados_bd[num] == "reservado_db": reservados_bd += 1
        elif num in pre_reservas:
            if pre_reservas[num]["session_id"] == mi_sesion:
                estados_pantalla[num] = "pre_reservado_mio"
            else:
                estados_pantalla[num] = "pre_reservado_otros"
                pre_reservados_otros += 1
        else:
            estados_pantalla[num] = "disponible"

    disponibles = 100 - vendidos - reservados_bd - pre_reservados_otros - len(st.session_state.selected_tickets)

    st.markdown(f"""
    <div class="metric-container">
        <div class="metric-box m-green"><h2>🟢 {disponibles}</h2><p>Libres</p></div>
        <div class="metric-box m-gray"><h2>🔒 {pre_reservados_otros}</h2><p>En otro carrito</p></div>
        <div class="metric-box m-yellow"><h2>🟡 {reservados_bd}</h2><p>Por Pagar (24h)</p></div>
        <div class="metric-box m-red"><h2>🔴 {vendidos}</h2><p>Vendidos</p></div>
    </div>
    <p>Haz clic en los boletos 🟢 <b>Libres</b> para apartarlos temporalmente (tienes 15 min para pagar).</p>
    """, unsafe_allow_html=True)

    for fila in range(10):
        cols = st.columns(10)
        for col_idx in range(10):
            num = f"{(fila * 10 + col_idx):03d}"
            estado = estados_pantalla[num]

            with cols[col_idx]:
                if estado == "vendido_db":
                    st.button(f"🔴\n{num}", disabled=True, key=f"btn_{num}")
                elif estado == "reservado_db":
                    st.button(f"🟡\n{num}", disabled=True, key=f"btn_{num}")
                elif estado == "pre_reservado_otros":
                    st.button(f"🔒\n{num}", disabled=True, key=f"btn_{num}", help="Alguien más tiene este boleto en su carrito ahora mismo.")
                else:
                    is_selected = (estado == "pre_reservado_mio") or (num in st.session_state.selected_tickets)
                    etiqueta = f"✅\n{num}" if is_selected else f"🟢\n{num}"
                    
                    if st.button(etiqueta, key=f"btn_{num}", type="primary" if is_selected else "secondary"):
                        if is_selected:
                            if num in pre_reservas: del pre_reservas[num]
                            if num in st.session_state.selected_tickets: st.session_state.selected_tickets.remove(num)
                        else:
                            pre_reservas[num] = {"session_id": mi_sesion, "expires_at": datetime.now() + timedelta(minutes=TIEMPO_PRERESERVA_MINUTOS)}
                            if num not in st.session_state.selected_tickets: st.session_state.selected_tickets.append(num)
                        st.rerun()

def procesar_descarga_pdf(datos_boletos: List[dict]):
    archivo_pdf = generar_pdf_boleto(datos_boletos)
    with open(archivo_pdf, "rb") as pdf_file: pdf_bytes = pdf_file.read()
    label = "⬇️ Descargar mis Boletos Oficiales (PDF)" if len(datos_boletos) > 1 else "⬇️ Descargar mi Boleto Oficial (PDF)"
    st.download_button(label=label, data=pdf_bytes, file_name=archivo_pdf, mime="application/pdf", type="primary", use_container_width=True)

def main():
    st.set_page_config(page_title="Rifa de Celular", page_icon="🎟️", layout="wide")
    st.markdown(CSS_CUSTOM, unsafe_allow_html=True)

    if "session_id" not in st.session_state: st.session_state.session_id = str(uuid.uuid4())
    if "selected_tickets" not in st.session_state: st.session_state.selected_tickets = []
    if "payment_success_id" not in st.session_state: st.session_state.payment_success_id = None
    if "pago_generado_url" not in st.session_state: st.session_state.pago_generado_url = None
    if "qr_codi_base64" not in st.session_state: st.session_state.qr_codi_base64 = None
    if "external_ref_activa" not in st.session_state: st.session_state.external_ref_activa = None

    conn = st.connection("gsheets", type=GSheetsConnection)
    qp = st.query_params

    if "payment_id" in qp and "status" in qp and qp["status"] == "approved":
        st.session_state.payment_success_id = qp["payment_id"]
        st.session_state.pago_generado_url = None
        st.session_state.qr_codi_base64 = None
        st.query_params.clear()
        st.rerun()

    st.title("📱 Plataforma de Boletos - Gran Rifa")
    tab1, tab2 = st.tabs(["🛒 Comprar Boletos", "🔍 Buscar mis Boletos / Verificar Pago SPEI o CoDi"])

    # --- TAB 2: VERIFICACIÓN MANUAL / POST-PAGO ---
    with tab2:
        st.markdown("### ¿Pagaste por CoDi (BBVA), Transferencia o Mercado Pago y cerraste la ventana?")
        col_b1, col_b2 = st.columns(2)
        with col_b1: buscar_num = st.text_input("Ingresa un número de boleto (ej. 005):")
        with col_b2: buscar_correo = st.text_input("Ingresa tu correo asociado:")
            
        if st.button("🔍 Verificar Pago y Descargar PDF", type="primary"):
            if not buscar_num or not buscar_correo: 
                st.warning("Por favor, llena ambos campos.")
            else:
                with st.spinner("Consultando liquidación interbancaria y Mercado Pago..."):
                    try:
                        df_r = conn.read(worksheet="Reservas", ttl=0)
                        correo_limpio = buscar_correo.strip().lower()
                        filtro = (df_r["Numero_Boleto"].astype(str).str.zfill(3) == str(buscar_num).strip().zfill(3)) & (df_r["Correo"].str.lower() == correo_limpio)
                        reservas = df_r[filtro]
                        
                        if reservas.empty: 
                            st.error("No encontramos una reserva pendiente con esos datos.")
                        else:
                            reserva = reservas.iloc[-1]
                            ext_ref = reserva["External_Reference"]
                            
                            pago_confirmado = buscar_pago_en_mercadopago(ext_ref)
                            
                            if not pago_confirmado and verificar_pago_codi_servidor(ext_ref):
                                pago_confirmado = {
                                    "id": f"CODI-{int(datetime.now().timestamp())}",
                                    "external_reference": ext_ref,
                                    "payment_type_id": "codi_bbva_spei",
                                    "status": "approved"
                                }

                            if pago_confirmado:
                                st.success("✅ ¡Pago verificado por el banco! Tus boletos están listos.")
                                st.balloons()
                                datos = actualizar_pago_en_hojas(conn, pago_confirmado)
                                if datos: procesar_descarga_pdf(datos)
                            else: 
                                st.warning("⏳ Tu reserva está activa, pero el pago por CoDi o MP aún está pendiente de acreditar en el banco.")
                    except Exception as e: st.error(f"Error de conexión: {e}")

    # --- TAB 1: FLUJO DE COMPRA ---
    with tab1:
        if st.session_state.payment_success_id:
            st.balloons()
            st.success(f"🎉 ¡Compra Confirmada! (ID: {st.session_state.payment_success_id})")
            with st.spinner("Generando PDF..."):
                try:
                    pinfo = sdk.payment().get(st.session_state.payment_success_id).get("response", {}) if sdk else {"external_reference": st.session_state.external_ref_activa, "id": st.session_state.payment_success_id}
                    datos = actualizar_pago_en_hojas(conn, pinfo)
                    if datos: procesar_descarga_pdf(datos)
                    else: st.error("Problema sincronizando compra.")
                except Exception as e: st.error(f"Error: {e}")
            
            st.write("---")
            if st.button("⬅️ Realizar otra compra", use_container_width=True):
                st.session_state.payment_success_id = None
                st.session_state.selected_tickets = []
                st.session_state.qr_codi_base64 = None
                st.rerun()
            st.stop()

        col_mapa, col_form = st.columns([1.5, 1], gap="large")
        with col_mapa:
            st.subheader("🎟️ Mapa de Disponibilidad")
            renderizar_mapa_interactivo()

        with col_form:
            st.subheader("🛒 Finalizar Compra")
            precio_base = 15.00  
            boletos = st.session_state.selected_tickets
            
            with st.container(border=True):
                if not boletos:
                    st.info("👆 Selecciona uno o más boletos disponibles.")
                    st.session_state.pago_generado_url = None
                    st.session_state.qr_codi_base64 = None
                else:
                    total_pagar = precio_base * len(boletos)
                    st.success(f"🎫 **En tu carrito:** {', '.join(boletos)} (Tienes 15 min para pagar)")
                    
                    # SECCIÓN DE PAGO GENERADA (MERCADO PAGO vs CODI BBVA)
                    if st.session_state.pago_generado_url or st.session_state.qr_codi_base64:
                        st.write(f"### Total a pagar: ${total_pagar:.2f} MXN")
                        
                        opcion_pago = st.radio("Elige tu método de pago seguro:", ["📲 CoDi BBVA (Sin cuenta, instantáneo)", "💳 Mercado Pago (Tarjetas, OXXO, SPEI)"], horizontal=True)
                        
                        if "CoDi" in opcion_pago:
                            st.markdown(f"""
                            <div class="codi-card">
                                <div class="codi-title">🔵 Paga con tu app BBVA o cualquier banco</div>
                                <div class="codi-instruction">Abre tu app bancaria, selecciona la opción <b>Escanear CoDi / QR</b> y apunta al código:</div>
                                <img src="{st.session_state.qr_codi_base64}" width="220" />
                                <br/>
                                <div style="font-size: 11px; color:#555; margin-top:5px;">¿No puedes escanear? Transfiere por SPEI al CLABE:<br/><b>{BBVA_CLABE}</b></div>
                                <div class="codi-ref">Ref: {st.session_state.external_ref_activa}</div>
                            </div>
                            """, unsafe_allow_html=True)
                            
                            if st.button("🔄 Ya transferí, verificar liquidación BBVA", type="primary"):
                                with st.spinner("Conectando con Banxico / BBVA..."):
                                    if verificar_pago_codi_servidor(st.session_state.external_ref_activa):
                                        pinfo = {
                                            "id": f"CODI-{int(datetime.now().timestamp())}",
                                            "external_reference": st.session_state.external_ref_activa,
                                            "payment_type_id": "codi_bbva_spei",
                                            "status": "approved"
                                        }
                                        actualizar_pago_en_hojas(conn, pinfo)
                                        st.session_state.payment_success_id = pinfo["id"]
                                        st.rerun()
                                    else:
                                        st.error("Aún no detectamos la transferencia en la cuenta BBVA. Puede tardar unos segundos.")

                        else:
                            st.info("Serás redirigido a Mercado Pago para usar tu tarjeta, saldo o generar cupón OXXO.")
                            st.link_button("💳 Pagar en Mercado Pago ➔", url=st.session_state.pago_generado_url, type="primary", use_container_width=True)
                        
                        st.write("---")
                        if st.button("❌ Cancelar reserva y vaciar carrito"):
                            pre_reservas = obtener_pre_reservas_globales()
                            for t in boletos:
                                if t in pre_reservas and pre_reservas[t]['session_id'] == st.session_state.session_id:
                                    del pre_reservas[t]
                            st.session_state.pago_generado_url = None
                            st.session_state.qr_codi_base64 = None
                            st.session_state.external_ref_activa = None
                            st.session_state.selected_tickets = []
                            st.rerun()
                    else:
                        col_nom, col_ape = st.columns(2)
                        with col_nom: nombre = st.text_input("Nombre(s):")
                        with col_ape: apellidos = st.text_input("Apellidos:")
                            
                        col_usr, col_dom = st.columns([3, 2.5])
                        with col_usr: correo_usuario = st.text_input("Correo (sin @):", placeholder="ej. juanperez")
                        with col_dom: dominio = st.selectbox("Extensión:", ["@gmail.com", "@hotmail.com", "@outlook.com", "@yahoo.com", "Otro..."])

                        correo = st.text_input("Correo completo:", placeholder="usuario@empresa.com") if dominio == "Otro..." else f"{correo_usuario.replace('@', '').strip()}{dominio}" if correo_usuario else ""
                        telefono = st.text_input("WhatsApp (10 dígitos):", max_chars=10)
                        
                        st.write(f"**Total a Pagar:** ${total_pagar:.2f} MXN")

                        if st.button("🔒 Confirmar y Elegir Método de Pago", type="primary", use_container_width=True):
                            pre_reservas = obtener_pre_reservas_globales()
                            ahora = datetime.now()
                            siguen_validos = all(t in pre_reservas and pre_reservas[t]['session_id'] == st.session_state.session_id and pre_reservas[t]['expires_at'] > ahora for t in boletos)
                            correo_valido = re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}$", correo.strip().lower())
                            
                            if not siguen_validos:
                                st.error("⚠️ El tiempo de carrito (15 min) expiró. Por favor, selecciona los boletos de nuevo.")
                                st.session_state.selected_tickets = []
                            elif not nombre or not apellidos or not correo_usuario or not telefono: st.error("⚠️ Completa todos los campos.")
                            elif not correo_valido: st.error("⚠️ El formato del correo NO es válido.")
                            elif not (telefono.isdigit() and len(telefono) == 10): st.error("⚠️ El número debe contener 10 dígitos numéricos.")
                            else:
                                ref = f"RIFA-{datetime.now().strftime('%Y%m%d%H%M%S')}-{random.randint(1000, 9999)}"
                                st.session_state.external_ref_activa = ref
                                
                                ordenes = [{
                                    "External_Reference": ref, "MercadoPago_Preference_ID": "", "MercadoPago_Payment_ID": "",
                                    "Numero_Boleto": str(t), "Nombre": f"{nombre.strip()} {apellidos.strip()}", "Correo": correo.strip().lower(),
                                    "Numero_Telefonico": telefono, "Monto": float(precio_base), "Estado_Reserva": "PENDIENTE",
                                    "Fecha_Creacion": ahora.strftime("%Y-%m-%d %H:%M:%S"),
                                    "Expira_En": (ahora + timedelta(minutes=TIEMPO_RESERVA_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S"),
                                    "Fecha_Actualizacion": ahora.strftime("%Y-%m-%d %H:%M:%S")
                                } for t in boletos]

                                with st.spinner("Registrando reserva y generando canales de cobro..."):
                                    exito, msj = registrar_reserva_cobro(conn, ordenes)
                                    if exito:
                                        # NOTA ARQUITECTÓNICA: Ya no borramos 'pre_reservas[t]' aquí para evitar
                                        # que el renderizador del mapa vacíe el carrito al ejecutar st.rerun().
                                        try:
                                            # 1. Generamos CoDi BBVA
                                            st.session_state.qr_codi_base64 = generar_qr_codi_bbva(total_pagar, ref, ", ".join(boletos))
                                            
                                            # 2. Generamos Mercado Pago
                                            if sdk:
                                                _, url_pago = crear_preferencia_mercado_pago(nombre, apellidos, correo.strip().lower(), telefono, boletos, precio_base, ref, None)
                                                st.session_state.pago_generado_url = url_pago
                                            else:
                                                st.session_state.pago_generado_url = "https://mercadopago.com.mx"
                                                
                                            st.rerun()
                                        except Exception as e: st.error(f"⚠️ Error generando canales de pago: {e}")
                                    else: st.error(f"⚠️ Error al conectar con Google Sheets: {msj}")

if __name__ == "__main__":
    main()
