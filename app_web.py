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
                return str(valor)
    except Exception:
        pass
    return os.getenv(nombre, default)

MP_ACCESS_TOKEN = obtener_config("MP_ACCESS_TOKEN")
MP_NOTIFICATION_URL = obtener_config("MP_NOTIFICATION_URL")
MP_RETURN_URL = obtener_config("MP_RETURN_URL")
MP_CURRENCY_ID = obtener_config("MP_CURRENCY_ID", "MXN")

# Agrega credenciales - Inicializar biblioteca de Mercado Pago Server-Side
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

# -----------------------------
# Estilos CSS
# -----------------------------
CSS_CUSTOM = """
<style>
    /* Ajustes para la cuadrícula de botones nativos */
    [data-testid="column"] {
        padding: 0 4px !important;
    }
    [data-testid="stButton"] button {
        width: 100%;
        height: 55px;
        padding: 0;
        font-weight: 700;
        font-size: 14px;
        transition: all 0.2s;
    }
    [data-testid="stButton"] button:hover {
        transform: scale(1.05);
        border-color: #20C997;
    }
    /* Estilos para las tarjetas de métricas */
    .metric-container {
        display: flex; justify-content: space-between; gap: 10px; margin-bottom: 20px;
    }
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
    nombre_archivo = datos_boleto.get("Comprobante", f"Boleto_{datos_boleto['ID_Boleto']}.pdf")
    doc = SimpleDocTemplate(nombre_archivo, pagesize=letter, rightMargin=32, leftMargin=32, topMargin=40, bottomMargin=32)
    story = []
    styles = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle("Titulo", parent=styles["Heading1"], fontSize=19, textColor=colors.HexColor("#0A2540"), alignment=1)
    estilo_normal = ParagraphStyle("Texto", parent=styles["Normal"], fontSize=10.5, leading=13, textColor=colors.HexColor("#334155"))
    
    story.append(Paragraph("BOLETO OFICIAL", estilo_titulo))
    story.append(Spacer(1, 16))

    data = [
        [Paragraph("<b>ID de Boleto:</b>", estilo_normal), Paragraph(str(datos_boleto["ID_Boleto"]), estilo_normal)],
        [Paragraph("<b>Nombre:</b>", estilo_normal), Paragraph(datos_boleto["Nombre"], estilo_normal)],
        [Paragraph("<b>N° de Boleto:</b>", estilo_normal), Paragraph(str(datos_boleto["Numero_Boleto"]), estilo_normal)],
        [Paragraph("<b>Precio Pagado:</b>", estilo_normal), Paragraph(f"${datos_boleto['Precio']:.2f} MXN", estilo_normal)],
    ]
    t = Table(data, colWidths=[165, 300])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")), ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#E2E8F0"))]))
    story.append(t)
    doc.build(story, onFirstPage=dibujar_fondo_autenticidad, onLaterPages=dibujar_fondo_autenticidad)
    return nombre_archivo

def mp_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {MP_ACCESS_TOKEN}", "Content-Type": "application/json"}

# Adaptación para soportar Deep Linking si se consume desde una App Móvil nativa
def crear_preferencia_mercado_pago(nombre, correo, telefono, numero_boleto, monto, external_reference, custom_return_scheme: Optional[str] = None):
    url_retorno = custom_return_scheme if custom_return_scheme else MP_RETURN_URL
    
    preference_data = {
        "items": [
            {
                "title": f"Rifa celular - Boleto {numero_boleto}",
                "quantity": 1,
                "unit_price": float(monto),
                "currency_id": MP_CURRENCY_ID
            }
        ],
        "payer": {
            "name": nombre,
            "email": correo
        },
        "external_reference": external_reference,
        "back_urls": {
            "success": url_retorno,
            "pending": url_retorno,
            "failure": url_retorno
        },
        "notification_url": MP_NOTIFICATION_URL, # Se adjunta la URL donde Mercado Pago enviará el webhook
        "auto_return": "approved",
    }
    
    preference_response = sdk.preference().create(preference_data)
    preference = preference_response["response"]
    return preference.get("id", ""), preference.get("init_point", "")

def obtener_pago_mercado_pago(payment_id: str) -> Dict[str, Any]:
    payment_response = sdk.payment().get(payment_id)
    return payment_response["response"]

# -----------------------------
# Google Sheets y Estados
# -----------------------------
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

def obtener_estado_boletos(df_ventas: pd.DataFrame, df_reservas: pd.DataFrame) -> dict:
    estados = {}
    
    # 1. Checar Reservas activas
    if not df_reservas.empty and "Numero_Boleto" in df_reservas.columns:
        for _, row in df_reservas.iterrows():
            if str(row.get("Estado_Reserva", "")).strip().upper() == "PENDIENTE":
                expira_str = row.get("Expira_En")
                try: expira = pd.to_datetime(str(expira_str)).to_pydatetime()
                except: expira = None
                
                if expira is None or datetime.now() <= expira:
                    num = parse_ticket_number(row["Numero_Boleto"])
                    if num: estados[num] = "reservado"

    # 2. Checar Ventas (Toma prioridad sobre reservas)
    if not df_ventas.empty and "Numero_Boleto" in df_ventas.columns:
        for _, row in df_ventas.iterrows():
            estado_pago = str(row.get("Estado_Pago", "")).strip().upper()
            if estado_pago not in ["RECHAZADO", "CANCELADO", "REEMBOLSADO", "PENDIENTE"]:
                num = parse_ticket_number(row["Numero_Boleto"])
                if num: estados[num] = "vendido"
                
    return estados

def registrar_reserva_cobro(conn: GSheetsConnection, orden: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        try: 
            df_r = conn.read(worksheet="Reservas", ttl=0)
            if df_r.empty:
                df_r = pd.DataFrame(columns=columnas_reservas())
        except Exception: 
            df_r = pd.DataFrame(columns=columnas_reservas())
        
        df_r = asegurar_columnas(df_r.dropna(how="all"), columnas_reservas())
        df_nuevo = asegurar_columnas(pd.DataFrame([orden]), columnas_reservas())
        
        df_actualizado = pd.concat([df_r, df_nuevo], ignore_index=True)
        conn.update(worksheet="Reservas", data=df_actualizado)
        
        return True, "Éxito"
    except Exception as e:
        return False, str(e)

# -------------------------------------------------------------
# Motor de Notificaciones (Webhooks / IPN) Mercado Pago
# -------------------------------------------------------------
def actualizar_pago_en_hojas(conn: GSheetsConnection, payment_info: Dict[str, Any]) -> bool:
    """Actualiza Google Sheets convirtiendo una reserva pendiente en venta confirmada."""
    try:
        ext_ref = payment_info.get("external_reference", "")
        estado_pago = payment_info.get("status", "")
        pago_id = str(payment_info.get("id", ""))
        
        # 1. Leer hojas actuales
        try: df_r = conn.read(worksheet="Reservas", ttl=0).dropna(how="all")
        except: df_r = pd.DataFrame(columns=columnas_reservas())
        try: df_v = conn.read(worksheet="Ventas", ttl=0).dropna(how="all")
        except: df_v = pd.DataFrame(columns=columnas_ventas())
        
        df_r = asegurar_columnas(df_r, columnas_reservas())
        df_v = asegurar_columnas(df_v, columnas_ventas())
        
        # 2. Si el pago fue aprobado, buscar la reserva asociada y crear la venta
        if estado_pago == "approved" and not df_r.empty:
            filtro_reserva = df_r["External_Reference"] == ext_ref
            if filtro_reserva.any():
                reserva = df_r[filtro_reserva].iloc[0]
                
                # Actualizar el estado en la hoja de Reservas
                df_r.loc[filtro_reserva, "Estado_Reserva"] = "PAGADO"
                df_r.loc[filtro_reserva, "MercadoPago_Payment_ID"] = pago_id
                conn.update(worksheet="Reservas", data=df_r)
                
                # Crear el registro en la hoja de Ventas para que el boleto cambie a color Rojo
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
                return True
        return False
    except Exception as e:
        st.error(f"Error procesando el registro en hojas: {e}")
        return False

def procesar_notificacion_mercadopago(payload: Dict[str, Any], conn: GSheetsConnection) -> Optional[Dict[str, Any]]:
    """
    Procesa las notificaciones webhooks siguiendo exactamente la estructura oficial del SDK.
    """
    try:
        # Estructura de extracción compatible con payloads directos o encapsulados en 'data'
        data = payload if "type" in payload else payload.get("data", {})
        notification_type = payload.get("type", data.get("type", ""))
        
        # Estructura condicional exacta solicitada para el SDK
        if notification_type == "payment":
            payment_id = payload.get("data", {}).get("id") or payload.get("id")
            if not payment_id:
                return None
            payment = sdk.payment().get(payment_id)
            payment_data = payment.get("response", {})
            
            # Si el pago está aprobado en MP, actualizar Google Sheets
            if payment_data.get("status") == "approved":
                actualizar_pago_en_hojas(conn, payment_data)
            return payment_data
            
        elif notification_type == "plan":
            plan = sdk.preapproval().get(payload["data"]["id"])
            return plan.get("response", {})
            
        elif notification_type == "subscription":
            subscription = sdk.preapproval().get(payload["data"]["id"])
            return subscription.get("response", {})
            
        elif notification_type == "invoice":
            invoice = sdk.invoice().get(payload["data"]["id"])
            return invoice.get("response", {})
            
        elif notification_type == "point_integration_wh":
            # Contiene la información relacionada a la notificación.
            return payload
        else:
            return None
    except Exception as e:
        return None


# -----------------------------
# Dashboard en Vivo y Clicable
# -----------------------------
@st.fragment(run_every=5)
def renderizar_mapa_interactivo():
    conn = st.connection("gsheets", type=GSheetsConnection)
    try:
        df_v = conn.read(worksheet="Ventas", ttl=5).dropna(how="all")
        try:
            df_r = conn.read(worksheet="Reservas", ttl=5).dropna(how="all")
        except:
            df_r = pd.DataFrame(columns=columnas_reservas())
    except:
        df_v, df_r = pd.DataFrame(columns=columnas_ventas()), pd.DataFrame(columns=columnas_reservas())

    estados = obtener_estado_boletos(df_v, df_r)

    vendidos = sum(1 for v in estados.values() if v == "vendido")
    reservados = sum(1 for v in estados.values() if v == "reservado")
    disponibles = 100 - vendidos - reservados

    html_metrics = f"""
    <div class="metric-container">
        <div class="metric-box m-green"><h2>🟢 {disponibles}</h2><p>Disponibles</p></div>
        <div class="metric-box m-yellow"><h2>🟡 {reservados}</h2><p>Reservados</p></div>
        <div class="metric-box m-red"><h2>🔴 {vendidos}</h2><p>Vendidos</p></div>
    </div>
    """
    st.markdown(html_metrics, unsafe_allow_html=True)
    st.markdown("Haz clic en un boleto 🟢 **Verde** para seleccionarlo.")

    for fila in range(10):
        cols = st.columns(10)
        for col_idx in range(10):
            num = f"{(fila * 10 + col_idx):03d}"
            estado = estados.get(num, "disponible")

            with cols[col_idx]:
                if estado == "vendido":
                    st.button(f"🔴\n{num}", disabled=True, key=f"btn_{num}", help="Vendido")
                elif estado == "reservado":
                    st.button(f"🟡\n{num}", disabled=True, key=f"btn_{num}", help="Reservado")
                else:
                    is_selected = (st.session_state.get("selected_ticket") == num)
                    etiqueta = f"✅\n{num}" if is_selected else f"🟢\n{num}"
                    tipo = "primary" if is_selected else "secondary"
                    
                    if st.button(etiqueta, key=f"btn_{num}", type=tipo, help="Clic para seleccionar"):
                        st.session_state.selected_ticket = num
                        st.rerun()


# -----------------------------
# Interfaz Principal
# -----------------------------
def main():
    st.set_page_config(page_title="Rifa de Celular", page_icon="🎟️", layout="wide")
    st.markdown(CSS_CUSTOM, unsafe_allow_html=True)

    if "selected_ticket" not in st.session_state: st.session_state.selected_ticket = None

    conn = st.connection("gsheets", type=GSheetsConnection)

    # 1. Lógica de escucha automática para Webhooks e IPN que lleguen por URL (Query Params)
    qp = st.query_params
    if ("type" in qp or "topic" in qp) and ("id" in qp or "data.id" in qp):
        notif_type = qp.get("type", qp.get("topic"))
        notif_id = qp.get("id", qp.get("data.id"))
        payload_notif = {"type": notif_type, "data": {"id": notif_id}}
        
        with st.spinner("Procesando notificación de Mercado Pago..."):
            res_notif = procesar_notificacion_mercadopago(payload_notif, conn)
            if res_notif and res_notif.get("status") == "approved":
                st.success(f"⚡ ¡WebHook procesado! El pago {notif_id} fue aprobado y el boleto ya está registrado en Ventas.")
                st.query_params.clear()

    # 2. Lógica tradicional de retorno de pago web (URL de éxito)
    if "payment_id" in qp and "status" in qp and qp["status"] == "approved":
        # Aseguramos por si el Webhook no llegó más rápido que el usuario al redirigirse
        procesar_notificacion_mercadopago({"type": "payment", "data": {"id": qp["payment_id"]}}, conn)
        st.success(f"✅ ¡Pago detectado y confirmado! (ID: {qp['payment_id']})")
        st.query_params.clear()

    st.title("📱 Plataforma de Boletos")

    col_mapa, col_form = st.columns([1.5, 1], gap="large")

    with col_mapa:
        st.subheader("🎟️ Mapa de Disponibilidad")
        renderizar_mapa_interactivo()

    with col_form:
        st.subheader("🛒 Finalizar Compra")
        precio_base = 100.00
        
        ticket = st.session_state.selected_ticket
        
        with st.container(border=True):
            if not ticket:
                st.info("👆 Selecciona un número de boleto disponible en el mapa de la izquierda para comenzar.")
            else:
                st.success(f"🎫 **Boleto seleccionado: {ticket}**")
                
                nombre = st.text_input("Nombre completo:")
                correo = st.text_input("Correo electrónico:")
                telefono = st.text_input("Número de WhatsApp / Celular:")
                
                is_mobile_app = st.checkbox("📲 ¿Es una orden desde App Móvil? (Deep Link)", value=False)
                custom_scheme = st.text_input("Esquema de la app (ej: miapp://checkout):", value="rifaapp://pago") if is_mobile_app else None
                
                st.write(f"**Total:** ${precio_base:.2f} MXN")

                if st.button("💳 Reservar y Pagar", type="primary", use_container_width=True):
                    if not nombre or not correo or not telefono:
                        st.error("⚠️ Completa tus datos para continuar.")
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

                        exito, mensaje_error = registrar_reserva_cobro(conn, reserva)
                        
                        if exito:
                            try:
                                pref_id, url_pago = crear_preferencia_mercado_pago(nombre, correo, telefono, ticket, precio_base, ref_externa, custom_scheme)
                                st.success(f"✅ ¡Boleto bloqueado! Tienes {TIEMPO_RESERVA_MINUTOS} minutos para pagar.")
                                
                                tab_web, tab_movil = st.tabs(["🌐 Pago Web (Navegador)", "📲 Integración App Móvil"])
                                
                                with tab_web:
                                    st.write("Si estás en un navegador tradicional, haz clic en el siguiente botón:")
                                    st.link_button("Ir a Mercado Pago ➔", url_pago, type="primary", use_container_width=True)
                                    
                                with tab_movil:
                                    st.markdown("### Configuración para Frontend Móvil")
                                    st.write("En tu aplicación (Flutter, React Native, iOS, Android), inicia el SDK nativo enviando este **ID de Preferencia**:")
                                    st.code(pref_id, language="text")
                                    
                            except Exception as e:
                                st.error(f"⚠️ Error creando el pago en Mercado Pago: {e}")
                        else:
                            st.error(f"⚠️ No se pudo guardar en Google Sheets. DETALLE DEL ERROR: {mensaje_error}")

        # Herramienta oculta/colapsable para simular el Webhook si estás haciendo pruebas locales
        with st.expander("🛠️ Simulador de Webhook / Notificación (Para Pruebas)"):
            st.caption("Usa esta herramienta si estás desarrollando localmente y quieres simular que Mercado Pago te envió una notificación de pago aprobado.")
            test_payment_id = st.text_input("ID de Pago en Mercado Pago para simular:")
            if st.button("Simular WebHook de Pago", type="secondary"):
                if test_payment_id:
                    res_test = procesar_notificacion_mercadopago({"type": "payment", "data": {"id": test_payment_id}}, conn)
                    if res_test:
                        st.json(res_test)
                        st.rerun()
                else:
                    st.warning("Ingresa un ID de pago válido.")

if __name__ == "__main__":
    main()
