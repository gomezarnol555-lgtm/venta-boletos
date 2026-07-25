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

# SDK de Mercado Pago
import mercadopago

# -----------------------------
# Configuración del Sistema
# -----------------------------
TIEMPO_RESERVA_MINUTOS = 15

def obtener_config(nombre: str, default: str = "") -> str:
    try:
        if hasattr(st, "secrets") and nombre in st.secrets:
            valor = st.secrets[nombre]
            if valor is not None:
                return str(valor).strip()
    except Exception:
        pass
    env_valor = os.getenv(nombre)
    if env_valor is not None:
        return str(env_valor).strip()
    return default

MP_ACCESS_TOKEN = obtener_config("MP_ACCESS_TOKEN")

if not MP_ACCESS_TOKEN:
    st.error("🚨 Error Crítico: No se detectó 'MP_ACCESS_TOKEN'.")
    st.stop()

MP_NOTIFICATION_URL = obtener_config("MP_NOTIFICATION_URL")
MP_RETURN_URL = obtener_config("MP_RETURN_URL")
MP_CURRENCY_ID = obtener_config("MP_CURRENCY_ID", "MXN")

sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

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
        transform: scale(1.05); border-color: #20C997;
    }
    .metric-container { display: flex; justify-content: space-between; gap: 10px; margin-bottom: 20px; }
    .metric-box {
        flex: 1; background: white; padding: 15px; border-radius: 8px; text-align: center;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05); border-top: 4px solid;
    }
    .metric-box h2 { margin: 0; font-size: 24px; font-weight: 800; color: #0A2540; }
    .metric-box p { margin: 0; font-size: 13px; color: #64748B; font-weight: 600; }
    .m-green { border-color: #20C997; }
    .m-yellow { border-color: #F59E0B; }
    .m-red { border-color: #EF4444; }
</style>
"""

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

def generar_pdf_boleto(datos_boleto: Dict[str, Any]) -> str:
    nombre_archivo = f"Boleto_Oficial_{datos_boleto['ID_Boleto']}.pdf"
    doc = SimpleDocTemplate(nombre_archivo, pagesize=letter, rightMargin=32, leftMargin=32, topMargin=40, bottomMargin=32)
    story = []
    styles = getSampleStyleSheet()
    
    estilo_titulo = ParagraphStyle("Titulo", parent=styles["Heading1"], fontSize=19, textColor=colors.HexColor("#0A2540"), alignment=1)
    estilo_normal = ParagraphStyle("Texto", parent=styles["Normal"], fontSize=10.5, leading=13, textColor=colors.HexColor("#334155"))
    
    story.append(Paragraph("BOLETO OFICIAL DE COMPRA", estilo_titulo))
    story.append(Spacer(1, 16))

    precio_float = float(datos_boleto.get('Precio', 0))

    data = [
        [Paragraph("<b>ID de Boleto:</b>", estilo_normal), Paragraph(str(datos_boleto["ID_Boleto"]), estilo_normal)],
        [Paragraph("<b>Nombre:</b>", estilo_normal), Paragraph(str(datos_boleto["Nombre"]), estilo_normal)],
        [Paragraph("<b>N° de Boleto:</b>", estilo_normal), Paragraph(str(datos_boleto["Numero_Boleto"]), estilo_normal)],
        [Paragraph("<b>Precio Pagado:</b>", estilo_normal), Paragraph(f"${precio_float:.2f} {MP_CURRENCY_ID}", estilo_normal)],
        [Paragraph("<b>ID de Pago (MP):</b>", estilo_normal), Paragraph(str(datos_boleto.get("MercadoPago_Payment_ID", "N/A")), estilo_normal)],
        [Paragraph("<b>Fecha:</b>", estilo_normal), Paragraph(str(datos_boleto.get("Fecha_Compra", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))), estilo_normal)]
    ]
    
    t = Table(data, colWidths=[165, 300])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))
    
    story.append(t)
    doc.build(story, onFirstPage=dibujar_fondo_autenticidad, onLaterPages=dibujar_fondo_autenticidad)
    return nombre_archivo

def crear_preferencia_mercado_pago(nombre, correo, telefono, numero_boleto, monto, external_reference, custom_return_scheme: Optional[str] = None):
    url_retorno = custom_return_scheme if custom_return_scheme else MP_RETURN_URL
    
    partes_nombre = nombre.strip().split(" ", 1)
    payer_name = partes_nombre[0]
    payer_surname = partes_nombre[1] if len(partes_nombre) > 1 else "Sin Apellido"
    
    preference_data = {
        "items": [{
            "title": f"Rifa celular - Boleto {numero_boleto}",
            "quantity": 1,
            "unit_price": float(monto),
            "currency_id": MP_CURRENCY_ID
        }],
        "payer": {
            "name": payer_name,
            "surname": payer_surname,
            "email": correo,
            "phone": {"area_code": "52", "number": telefono}
        },
        "external_reference": external_reference,
        "payment_methods": {"excluded_payment_methods": [], "excluded_payment_types": [], "installments": 1},
        "statement_descriptor": "RIFA CELULAR"
    }
    
    if url_retorno:
        preference_data["back_urls"] = {"success": url_retorno, "pending": url_retorno, "failure": url_retorno}
        preference_data["auto_return"] = "approved"
        
    preference_response = sdk.preference().create(preference_data)
    preference = preference_response.get("response", {})
    
    if "id" not in preference:
        raise Exception(f"Rechazado por MP: {preference.get('message', 'Error')}")
        
    url_pago = preference.get("init_point") or preference.get("sandbox_init_point", "")
    return preference.get("id", ""), url_pago

# -----------------------------
# Consultar APIs de MP (NUEVA FUNCIÓN EXPERTA)
# -----------------------------
def buscar_pago_en_mercadopago(external_reference: str) -> Optional[Dict]:
    """Consulta la API de Mercado Pago buscando si una referencia específica ya fue pagada."""
    try:
        filtros = {"external_reference": external_reference}
        resultado = sdk.payment().search(filtros)
        pagos = resultado.get("response", {}).get("results", [])
        
        # Filtrar solo el que esté aprobado
        for pago in pagos:
            if pago.get("status") == "approved":
                return pago
        return None
    except Exception as e:
        print(f"Error consultando API MP: {e}")
        return None

# -----------------------------
# Google Sheets y Estados
# -----------------------------
def columnas_ventas() -> list: 
    return ["ID_Boleto", "Nombre", "Correo", "Evento", "Numero_Boleto", "Precio", "Metodo_Pago", "Codigo_Pago", "Fecha_Compra", "Numero_Telefonico", "Estado_Pago", "Referencia_Pago", "MercadoPago_Payment_ID", "MercadoPago_Preference_ID"]

def columnas_reservas() -> list: 
    return ["External_Reference", "MercadoPago_Preference_ID", "MercadoPago_Payment_ID", "Numero_Boleto", "Nombre", "Correo", "Numero_Telefonico", "Monto", "Estado_Reserva", "Fecha_Creacion", "Expira_En", "Fecha_Actualizacion"]

def asegurar_columnas(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    for col in cols:
        if col not in df.columns: df[col] = ""
    return df[cols]

def parse_ticket_number(val: Any) -> str:
    if pd.isna(val) or str(val).strip() == "": return ""
    try: return f"{int(float(val)):03d}"
    except: return str(val).strip().zfill(3)

def obtener_estado_boletos(df_ventas: pd.DataFrame, df_reservas: pd.DataFrame) -> dict:
    estados = {}
    
    # Primero vemos los reservados
    if not df_reservas.empty and "Numero_Boleto" in df_reservas.columns:
        for _, row in df_reservas.iterrows():
            estado_res = str(row.get("Estado_Reserva", "")).strip().upper()
            if estado_res == "PENDIENTE":
                expira_str = row.get("Expira_En")
                try: expira = pd.to_datetime(str(expira_str)).to_pydatetime()
                except: expira = None
                
                if expira is None or datetime.now() <= expira:
                    num = parse_ticket_number(row["Numero_Boleto"])
                    if num: estados[num] = "reservado"

    # Los vendidos SOBREESCRIBEN a los reservados
    if not df_ventas.empty and "Numero_Boleto" in df_ventas.columns:
        for _, row in df_ventas.iterrows():
            estado_pago = str(row.get("Estado_Pago", "")).strip().upper()
            if estado_pago in ["APROBADO", "VENDIDO"]:
                num = parse_ticket_number(row["Numero_Boleto"])
                if num: estados[num] = "vendido"
                
    return estados

def registrar_reserva_cobro(conn: GSheetsConnection, orden: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        try: df_r = conn.read(worksheet="Reservas", ttl=0)
        except: df_r = pd.DataFrame(columns=columnas_reservas())
        
        df_r = asegurar_columnas(df_r.dropna(how="all"), columnas_reservas())
        df_nuevo = asegurar_columnas(pd.DataFrame([orden]), columnas_reservas())
        
        df_actualizado = pd.concat([df_r, df_nuevo], ignore_index=True)
        conn.update(worksheet="Reservas", data=df_actualizado)
        return True, "Éxito"
    except Exception as e:
        return False, str(e)

def actualizar_pago_en_hojas(conn: GSheetsConnection, payment_info: Dict[str, Any]) -> Dict[str, Any]:
    """Registra la venta en Sheets de forma segura y devuelve los datos del boleto."""
    ext_ref = payment_info.get("external_reference", "")
    pago_id = str(payment_info.get("id", ""))
    
    try: df_r = conn.read(worksheet="Reservas", ttl=0).dropna(how="all")
    except: df_r = pd.DataFrame(columns=columnas_reservas())
        
    try: df_v = conn.read(worksheet="Ventas", ttl=0).dropna(how="all")
    except: df_v = pd.DataFrame(columns=columnas_ventas())
    
    df_r = asegurar_columnas(df_r, columnas_reservas())
    df_v = asegurar_columnas(df_v, columnas_ventas())
    
    # Si ya existe en ventas, solo lo devolvemos para imprimir el PDF
    filtro_existente = df_v["MercadoPago_Payment_ID"].astype(str) == str(pago_id)
    if filtro_existente.any():
        return df_v[filtro_existente].iloc[0].to_dict()
            
    # Si no existe, buscamos la reserva y la convertimos en Venta
    filtro_reserva = df_r["External_Reference"] == ext_ref
    if filtro_reserva.any():
        reserva = df_r[filtro_reserva].iloc[0]
        
        df_r.loc[filtro_reserva, "Estado_Reserva"] = "PAGADO"
        df_r.loc[filtro_reserva, "MercadoPago_Payment_ID"] = pago_id
        conn.update(worksheet="Reservas", data=df_r)
        
        nueva_venta = {
            "ID_Boleto": f"BOL-{random.randint(10000, 99999)}",
            "Nombre": reserva["Nombre"],
            "Correo": reserva["Correo"],
            "Evento": "Rifa de Celular",
            "Numero_Boleto": reserva["Numero_Boleto"],
            "Precio": reserva["Monto"],
            "Metodo_Pago": payment_info.get("payment_type_id", "mercadopago"),
            "Codigo_Pago": pago_id,
            "Fecha_Compra": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Numero_Telefonico": reserva["Numero_Telefonico"],
            "Estado_Pago": "APROBADO",
            "Referencia_Pago": ext_ref,
            "MercadoPago_Payment_ID": pago_id,
            "MercadoPago_Preference_ID": reserva["MercadoPago_Preference_ID"]
        }
        df_v_actualizado = pd.concat([df_v, pd.DataFrame([nueva_venta])], ignore_index=True)
        conn.update(worksheet="Ventas", data=df_v_actualizado)
        return nueva_venta
    
    return {}

# -----------------------------
# Componentes UI
# -----------------------------
@st.fragment(run_every=5)
def renderizar_mapa_interactivo():
    conn = st.connection("gsheets", type=GSheetsConnection)
    try:
        df_v = conn.read(worksheet="Ventas", ttl=5).dropna(how="all")
        df_r = conn.read(worksheet="Reservas", ttl=5).dropna(how="all")
    except:
        df_v, df_r = pd.DataFrame(columns=columnas_ventas()), pd.DataFrame(columns=columnas_reservas())

    estados = obtener_estado_boletos(df_v, df_r)
    vendidos = sum(1 for v in estados.values() if v == "vendido")
    reservados = sum(1 for v in estados.values() if v == "reservado")
    disponibles = 100 - vendidos - reservados

    st.markdown(f"""
    <div class="metric-container">
        <div class="metric-box m-green"><h2>🟢 {disponibles}</h2><p>Disponibles</p></div>
        <div class="metric-box m-yellow"><h2>🟡 {reservados}</h2><p>Reservados (Por Pagar)</p></div>
        <div class="metric-box m-red"><h2>🔴 {vendidos}</h2><p>Vendidos</p></div>
    </div>
    <p>Haz clic en un boleto 🟢 <b>Verde</b> para seleccionarlo.</p>
    """, unsafe_allow_html=True)

    for fila in range(10):
        cols = st.columns(10)
        for col_idx in range(10):
            num = f"{(fila * 10 + col_idx):03d}"
            estado = estados.get(num, "disponible")

            with cols[col_idx]:
                if estado == "vendido":
                    st.button(f"🔴\n{num}", disabled=True, key=f"btn_{num}")
                elif estado == "reservado":
                    st.button(f"🟡\n{num}", disabled=True, key=f"btn_{num}")
                else:
                    is_selected = (st.session_state.get("selected_ticket") == num)
                    etiqueta = f"✅\n{num}" if is_selected else f"🟢\n{num}"
                    if st.button(etiqueta, key=f"btn_{num}", type="primary" if is_selected else "secondary"):
                        st.session_state.selected_ticket = num
                        st.rerun()

def procesar_descarga_pdf(datos_boleto: dict):
    archivo_pdf = generar_pdf_boleto(datos_boleto)
    with open(archivo_pdf, "rb") as pdf_file:
        pdf_bytes = pdf_file.read()
    st.download_button(
        label="⬇️ Descargar mi Boleto Oficial (PDF)",
        data=pdf_bytes,
        file_name=archivo_pdf,
        mime="application/pdf",
        type="primary",
        use_container_width=True
    )

def main():
    st.set_page_config(page_title="Rifa de Celular", page_icon="🎟️", layout="wide")
    st.markdown(CSS_CUSTOM, unsafe_allow_html=True)

    if "selected_ticket" not in st.session_state: st.session_state.selected_ticket = None
    if "payment_success_id" not in st.session_state: st.session_state.payment_success_id = None
    if "pago_generado_url" not in st.session_state: st.session_state.pago_generado_url = None

    conn = st.connection("gsheets", type=GSheetsConnection)
    qp = st.query_params

    # Detección de retorno automático (Tarjetas)
    if "payment_id" in qp and "status" in qp and qp["status"] == "approved":
        st.session_state.payment_success_id = qp["payment_id"]
        st.session_state.pago_generado_url = None
        st.query_params.clear()
        st.rerun()

    st.title("📱 Plataforma de Boletos - Gran Rifa")
    
    # SISTEMA DE PESTAÑAS (Aquí resolvemos el problema de SPEI/OXXO)
    tab1, tab2 = st.tabs(["🛒 Comprar Boleto", "🔍 Buscar mi Boleto / Verificar Pago Transferencia"])

    # --- PESTAÑA 2: VERIFICACIÓN Y DESCARGA PARA PAGOS ASÍNCRONOS (SPEI) ---
    with tab2:
        st.markdown("### ¿Pagaste por Transferencia (SPEI) u OXXO y cerraste la ventana?")
        st.write("No te preocupes. Escribe tus datos, el sistema verificará tu pago y te entregará tu PDF.")
        
        col_b1, col_b2 = st.columns(2)
        with col_b1:
            buscar_num = st.text_input("Ingresa tu número de boleto (ej. 005):")
        with col_b2:
            buscar_correo = st.text_input("Ingresa tu correo electrónico:")
            
        if st.button("🔍 Verificar Pago y Descargar PDF", type="primary"):
            if not buscar_num or not buscar_correo:
                st.warning("Por favor, llena ambos campos.")
            else:
                with st.spinner("Conectando de forma segura con Mercado Pago y el Banco..."):
                    try:
                        df_r = conn.read(worksheet="Reservas", ttl=0)
                        filtro = (df_r["Numero_Boleto"].astype(str).str.zfill(3) == str(buscar_num).zfill(3)) & (df_r["Correo"].str.lower() == buscar_correo.lower())
                        reservas_encontradas = df_r[filtro]
                        
                        if reservas_encontradas.empty:
                            st.error("No encontramos una reserva con esos datos. Verifica que el correo esté bien escrito.")
                        else:
                            # Tomamos la reserva más reciente
                            reserva = reservas_encontradas.iloc[-1]
                            ext_ref = reserva["External_Reference"]
                            
                            # Magia: Consultamos directo a la API de MP usando su referencia única
                            pago_mp = buscar_pago_en_mercadopago(ext_ref)
                            
                            if pago_mp:
                                # El pago ya pasó el SPEI/OXXO. Lo registramos y generamos PDF.
                                st.success("✅ ¡Pago encontrado y aprobado! Tu boleto está confirmado.")
                                st.balloons()
                                datos_boleto = actualizar_pago_en_hojas(conn, pago_mp)
                                if datos_boleto:
                                    procesar_descarga_pdf(datos_boleto)
                            else:
                                st.warning("⏳ Tu reserva existe, pero Mercado Pago aún reporta el pago como PENDIENTE. Si transferiste hace menos de 5 minutos, intenta de nuevo en un momento.")
                    except Exception as e:
                        st.error(f"Error de conexión al verificar: {e}")

    # --- PESTAÑA 1: FLUJO NORMAL DE COMPRA ---
    with tab1:
        if st.session_state.payment_success_id:
            pago_id = st.session_state.payment_success_id
            st.balloons()
            st.success(f"🎉 ¡Compra Confirmada mediante Tarjeta! Su pago (ID: {pago_id}) fue aprobado.")
            
            with st.spinner("Generando tu comprobante en PDF..."):
                try:
                    # Obtenemos los datos desde MP directo usando el SDK para la compra con tarjeta
                    payment_info = sdk.payment().get(pago_id).get("response", {})
                    datos_boleto = actualizar_pago_en_hojas(conn, payment_info)
                    
                    if datos_boleto:
                        procesar_descarga_pdf(datos_boleto)
                    else:
                        st.error("Hubo un problema sincronizando el boleto.")
                except Exception as e:
                    st.error(f"Error al generar el PDF: {e}")
            
            st.write("---")
            if st.button("⬅️ Comprar otro boleto", use_container_width=True):
                st.session_state.payment_success_id = None
                st.session_state.selected_ticket = None
                st.rerun()
            st.stop()

        col_mapa, col_form = st.columns([1.5, 1], gap="large")

        with col_mapa:
            st.subheader("🎟️ Mapa de Disponibilidad")
            renderizar_mapa_interactivo()

        with col_form:
            st.subheader("🛒 Finalizar Compra")
            precio_base = 15.00 
            ticket = st.session_state.selected_ticket
            
            with st.container(border=True):
                if not ticket:
                    st.info("👆 Selecciona un boleto disponible a la izquierda.")
                    st.session_state.pago_generado_url = None
                else:
                    st.success(f"🎫 **Boleto seleccionado: {ticket}**")
                    
                    if st.session_state.pago_generado_url:
                        st.info("Serás redirigido a Mercado Pago para elegir: Tarjeta, Transferencia o Efectivo.")
                        st.link_button("💳 Pagar con Mercado Pago ➔", url=st.session_state.pago_generado_url, type="primary", use_container_width=True)
                        st.caption("🚨 Si pagas por Transferencia y cierras esta ventana, vuelve a entrar luego y usa la pestaña **'Buscar mi Boleto'** para descargar tu PDF.")
                        
                        st.write("---")
                        if st.button("Cancelar reserva actual"):
                            st.session_state.pago_generado_url = None
                            st.rerun()
                    else:
                        nombre = st.text_input("Nombre completo:")
                        correo = st.text_input("Correo electrónico:")
                        telefono = st.text_input("Número de WhatsApp / Celular:")
                        st.write(f"**Total a Pagar:** ${precio_base:.2f} MXN")

                        if st.button("Reservar Boleto", type="primary", use_container_width=True):
                            if not nombre or not correo or not telefono:
                                st.error("⚠️ Completa tus datos para proceder.")
                            else:
                                ref_externa = f"RIFA-{datetime.now().strftime('%Y%m%d%H%M%S')}-{ticket}"
                                fecha = datetime.now()
                                reserva = {
                                    "External_Reference": ref_externa,
                                    "MercadoPago_Preference_ID": "",
                                    "MercadoPago_Payment_ID": "",
                                    "Numero_Boleto": str(ticket),
                                    "Nombre": nombre,
                                    "Correo": correo,
                                    "Numero_Telefonico": telefono,
                                    "Monto": float(precio_base),
                                    "Estado_Reserva": "PENDIENTE",
                                    "Fecha_Creacion": fecha.strftime("%Y-%m-%d %H:%M:%S"),
                                    "Expira_En": (fecha + timedelta(minutes=TIEMPO_RESERVA_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S"),
                                    "Fecha_Actualizacion": fecha.strftime("%Y-%m-%d %H:%M:%S"),
                                }

                                with st.spinner("Conectando con Mercado Pago..."):
                                    exito, msj = registrar_reserva_cobro(conn, reserva)
                                    if exito:
                                        try:
                                            pref_id, url_pago = crear_preferencia_mercado_pago(nombre, correo, telefono, ticket, precio_base, ref_externa, None)
                                            if url_pago:
                                                st.session_state.pago_generado_url = url_pago
                                                st.rerun()
                                        except Exception as e:
                                            st.error(f"⚠️ Error creando pago: {e}")
                                    else:
                                        st.error(f"⚠️ Error guardando en Sheets: {msj}")

if __name__ == "__main__":
    main()
