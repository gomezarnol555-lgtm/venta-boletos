import random
from datetime import datetime
import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from streamlit_gsheets import GSheetsConnection

def generar_pdf_boleto(datos_boleto):
    nombre_archivo = f"Boleto_{datos_boleto['ID_Boleto']}.pdf"
    doc = SimpleDocTemplate(nombre_archivo, pagesize=letter)
    story = []
    
    styles = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle(
        'TituloBoleto', parent=styles['Heading1'], fontSize=20,
        textColor=colors.HexColor("#1A365D"), alignment=1
    )
    estilo_normal = ParagraphStyle(
        'TextoBoleto', parent=styles['Normal'], fontSize=12,
        textColor=colors.HexColor("#2D3748")
    )
    
    story.append(Paragraph("COMPROBANTE OFICIAL DE BOLETO", estilo_titulo))
    story.append(Spacer(1, 20))
    
    data = [
        [Paragraph("<b>ID de Boleto:</b>", estilo_normal), Paragraph(str(datos_boleto['ID_Boleto']), estilo_normal)],
        [Paragraph("<b>Asistente:</b>", estilo_normal), Paragraph(datos_boleto['Nombre'], estilo_normal)],
        [Paragraph("<b>Correo:</b>", estilo_normal), Paragraph(datos_boleto['Correo'], estilo_normal)],
        [Paragraph("<b>Evento:</b>", estilo_normal), Paragraph(datos_boleto['Evento'], estilo_normal)],
        [Paragraph("<b>Asiento/Boleto N°:</b>", estilo_normal), Paragraph(str(datos_boleto['Numero_Boleto']), estilo_normal)],
        [Paragraph("<b>Precio Pagado:</b>", estilo_normal), Paragraph(f"${datos_boleto['Precio']:.2f} MXN", estilo_normal)],
        [Paragraph("<b>Código de Pago:</b>", estilo_normal), Paragraph(datos_boleto['Codigo_Pago'], estilo_normal)],
        [Paragraph("<b>Fecha de Emisión:</b>", estilo_normal), Paragraph(str(datos_boleto['Fecha_Compra']), estilo_normal)]
    ]
    
    t = Table(data, colWidths=[150, 300])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F7FAFC")),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor("#E2E8F0")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 10),
    ]))
    
    story.append(t)
    story.append(Spacer(1, 30))
    story.append(Paragraph("<i>Presenta este comprobante en la entrada del evento. ¡Gracias por tu compra!</i>", ParagraphStyle('Footer', parent=estilo_normal, fontSize=10, alignment=1, textColor=colors.HexColor("#718096"))))
    
    doc.build(story)
    return nombre_archivo

def obtener_datos_gsheets():
    """Obtiene el inventario y ventas desde Google Sheets."""
    conn = st.connection("gsheets", type=GSheetsConnection)
    df_ventas = conn.read(worksheet="Ventas", usecols=list(range(7)), ttl=0)
    df_ventas = df_ventas.dropna(how="all")
    return conn, df_ventas

def main():
    st.set_page_config(page_title="Venta de Boletos y Asientos", page_icon="🎟️", layout="wide")
    st.title("🎟️ Sistema de Venta de Boletos y Control de Asistentes")
    
    try:
        conn, df_ventas = obtener_datos_gsheets()
    except Exception:
        st.error("⚠️ Error de conexión con Google Sheets. Asegúrate de configurar las credenciales secretas más adelante.")
        return

    # --- CONFIGURACIÓN INICIAL DE BOLETOS DISPONIBLES ---
    # Definimos 10 boletos disponibles por defecto con sus precios
    total_boletos_config = 10
    precio_base = 250.00
    
    lista_inventario = []
    boletos_vendidos = df_ventas["Numero_Boleto"].values if not df_ventas.empty and "Numero_Boleto" in df_ventas.columns else []

    for i in range(1, total_boletos_config + 1):
        estado = "Vendido ❌" if i in boletos_vendidos else "Disponible ✅"
        lista_inventario.append({
            "Boleto N°": i,
            "Precio": f"${precio_base:.2f} MXN",
            "Estado": estado
        })
    
    df_inventario = pd.DataFrame(lista_inventario)

    # Diseño en dos columnas en pantalla
    col1, col2 = st.columns([1.2, 1])

    with col1:
        st.subheader("📋 Tabla de Disponibilidad de Boletos")
        st.dataframe(df_inventario, use_container_width=True, hide_index=True)
        
        # Panel de Monitoreo de Ventas (Historial)
        st.markdown("---")
        st.subheader("📊 Historial de Monitoreo de Ventas")
        if not df_ventas.empty:
            total_recaudado = df_ventas["Precio"].sum() if "Precio" in df_ventas.columns else 0
            st.metric(label="Ingresos Totales Acumulados", value=f"${total_recaudado:.2f} MXN")
            st.dataframe(df_ventas, use_container_width=True, hide_index=True)
        else:
            st.info("Aún no hay ventas registradas en el historial.")

    with col2:
        st.subheader("🛒 Formulario de Compra")
        
        # Filtramos solo los boletos que están libres
        boletos_libres = [item["Boleto N°"] for item in lista_inventario if item["Estado"] == "Disponible ✅"]
        
        if not boletos_libres:
            st.warning("⚠️ ¡Lo sentimos! Todos los boletos están agotados.")
            return

        with st.form("form_compra"):
            nombre = st.text_input("Nombre completo:")
            correo = st.text_input("Correo electrónico:")
            evento = st.text_input("Nombre del evento:", value="Conferencia Principal 2026")
            boleto_seleccionado = st.selectbox("Selecciona tu número de boleto disponible:", boletos_libres)
            
            st.markdown("---")
            st.write(f"**Monto a pagar:** ${precio_base:.2f} MXN")
            metodo_pago = st.radio("Método de Pago Seguro:", ["Tarjeta de Crédito/Débito (Simulado)", "Transferencia Bancaria SPEI"])
            
            pago_realizado = st.checkbox("Confirmo que he realizado el pago correspondiente.")
            
            submit_compra = st.form_submit_button("💳 Procesar Pago y Generar Boleto")

        if submit_compra:
            if not nombre or not correo or not evento:
                st.error("⚠️ Por favor completa todos los campos del formulario.")
            elif not pago_realizado:
                st.warning("⚠️ Debes marcar la casilla confirmando que realizaste el pago para continuar.")
            else:
                with st.spinner('Validando pago y generando boleto oficial...'):
                    id_boleto = f"BOL-{int(datetime.now().timestamp())}"
                    codigo_pago = f"PAY-SECURE-{random.randint(10000, 99999)}"
                    fecha_compra = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    datos_nuevo_boleto = {
                        "ID_Boleto": id_boleto,
                        "Nombre": nombre,
                        "Correo": correo,
                        "Evento": evento,
                        "Numero_Boleto": int(boleto_seleccionado),
                        "Precio": float(precio_base),
                        "Codigo_Pago": codigo_pago,
                        "Fecha_Compra": fecha_compra
                    }
                    
                    # Guardar en Google Sheets (Historial y Registro)
                    df_actualizado = pd.concat([df_ventas, pd.DataFrame([datos_nuevo_boleto])], ignore_index=True)
                    conn.update(worksheet="Ventas", data=df_actualizado)
                    
                    # Generar PDF
                    archivo_pdf = generar_pdf_boleto(datos_nuevo_boleto)

                st.success("¡Pago procesado con éxito y registrado en el historial!")
                st.info(f"Tu código de confirmación de pago es: **{codigo_pago}**")
                
                with open(archivo_pdf, "rb") as pdf_file:
                    pdf_bytes = pdf_file.read()
                    
                st.download_button(
                    label="📥 Descargar mi Comprobante en PDF",
                    data=pdf_bytes,
                    file_name=archivo_pdf,
                    mime="application/pdf",
                    key="btn_descarga"
                )
                
                st.success("💡 *El formulario se ha procesado. Puedes actualizar la página o comprar otro boleto disponible.*")

if __name__ == "__main__":
    main()
