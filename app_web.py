import os
2
import random
3
import re
4
import uuid
5
from datetime import datetime, timedelta
6
from typing import Any, Dict, List, Optional, Tuple
7
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse
8
 
9
import pandas as pd
10
import streamlit as st
11
from reportlab.lib import colors
12
from reportlab.lib.pagesizes import letter
13
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
14
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak
15
from streamlit_gsheets import GSheetsConnection
16
 
17
import mercadopago
18
import stripe
19
 
20
 
21
# ============================================================
22
# CONFIGURACION
23
# ============================================================
24
TIEMPO_RESERVA_MINUTOS = 1440
25
TIEMPO_PRERESERVA_MINUTOS = 15
26
TOTAL_BOLETOS = 100
27
PRECIO_BOLETO = 15.00
28
 
29
 
30
def obtener_config(nombre: str, default: str = "") -> str:
31
try:
32
if hasattr(st, "secrets") and nombre in st.secrets:
33
return str(st.secrets[nombre]).strip()
34
except Exception:
35
pass
36
 
37
env_valor = os.getenv(nombre)
38
if env_valor is not None:
39
return str(env_valor).strip()
40
 
41
return default
42
 
43
 
44
MP_ACCESS_TOKEN = obtener_config("MP_ACCESS_TOKEN")
45
MP_NOTIFICATION_URL = obtener_config("MP_NOTIFICATION_URL")
46
MP_RETURN_URL = obtener_config("MP_RETURN_URL")
47
MP_CURRENCY_ID = obtener_config("MP_CURRENCY_ID", "MXN")
48
 
49
STRIPE_SECRET_KEY = obtener_config("STRIPE_SECRET_KEY")
50
STRIPE_RETURN_URL = obtener_config("STRIPE_RETURN_URL")
51
STRIPE_CURRENCY_ID = obtener_config("STRIPE_CURRENCY_ID", "mxn").lower()
52
 
53
DEBUG_PAGOS = obtener_config("DEBUG_PAGOS", "false").lower() in [
54
"1",
55
"true",
56
"si",
57
"sí",
58
"yes",
59
"on",
60
]
61
 
62
sdk = mercadopago.SDK(MP_ACCESS_TOKEN) if MP_ACCESS_TOKEN else None
63
 
64
if STRIPE_SECRET_KEY:
65
stripe.api_key = STRIPE_SECRET_KEY
66
 
67
 
68
# ============================================================
69
# UTILIDADES
70
# ============================================================
71
def normalizar_url(url: str) -> str:
72
url = str(url or "").strip()
73
 
74
if url and not url.startswith(("http://", "https://")):
75
url = f"https://{url}"
76
 
77
return url
78
 
79
 
80
def agregar_parametros_url(url: str, parametros: Dict[str, str]) -> str:
81
url = normalizar_url(url)
82
partes = urlparse(url)
83
query_actual = dict(parse_qsl(partes.query, keep_blank_values=True))
84
query_actual.update({k: str(v) for k, v in parametros.items() if v is not None})
85
nueva_query = urlencode(query_actual)
86
nueva_query = nueva_query.replace("%7BCHECKOUT_SESSION_ID%7D", "{CHECKOUT_SESSION_ID}")
87
 
88
return urlunparse(
89
(
90
partes.scheme,
91
partes.netloc,
92
partes.path,
93
partes.params,
94
nueva_query,
95
partes.fragment,
96
)
97
)
98
 
99
 
100
def qp_get(qp: Any, nombre: str, default: str = "") -> str:
101
try:
102
valor = qp.get(nombre, default)
103
 
104
if isinstance(valor, list):
105
return str(valor[0]) if valor else default
106
 
107
return str(valor)
108
except Exception:
109
return default
110
 
111
 
112
def parse_ticket_number(valor: Any) -> str:
113
if pd.isna(valor) or str(valor).strip() == "":
114
return ""
115
 
116
try:
117
return f"{int(float(valor)):03d}"
118
except Exception:
119
return str(valor).strip().zfill(3)
120
 
121
 
122
def enmascarar_clave(valor: str, visibles_inicio: int = 7, visibles_fin: int = 4) -> str:
123
valor = str(valor or "").strip()
124
 
125
if not valor:
126
return "NO CONFIGURADO"
127
 
128
if len(valor) <= visibles_inicio + visibles_fin:
129
return "*" * len(valor)
130
 
131
return f"{valor[:visibles_inicio]}...{valor[-visibles_fin:]}"
132
 
133
 
134
def diagnostico_configuracion_pagos() -> Dict[str, Any]:
135
return {
136
"MP_ACCESS_TOKEN_configurado": bool(MP_ACCESS_TOKEN),
137
"MP_ACCESS_TOKEN_mascara": enmascarar_clave(MP_ACCESS_TOKEN),
138
"MP_RETURN_URL": MP_RETURN_URL or "NO CONFIGURADO",
139
"MP_CURRENCY_ID": MP_CURRENCY_ID,
140
"STRIPE_SECRET_KEY_configurado": bool(STRIPE_SECRET_KEY),
141
"STRIPE_SECRET_KEY_mascara": enmascarar_clave(STRIPE_SECRET_KEY),
142
"STRIPE_SECRET_KEY_es_sk": str(STRIPE_SECRET_KEY).startswith("sk_"),
143
"STRIPE_SECRET_KEY_es_pk_error": str(STRIPE_SECRET_KEY).startswith("pk_"),
144
"STRIPE_RETURN_URL": STRIPE_RETURN_URL or "NO CONFIGURADO",
145
"STRIPE_RETURN_URL_es_http": normalizar_url(STRIPE_RETURN_URL).startswith(
146
("http://", "https://")
147
),
148
"STRIPE_CURRENCY_ID": STRIPE_CURRENCY_ID,
149
"DEBUG_PAGOS": DEBUG_PAGOS,
150
}
151
 
152
 
153
def mostrar_diagnostico_pagos():
154
if not DEBUG_PAGOS:
155
return
156
 
157
with st.expander("Diagnóstico técnico de pagos"):
158
st.json(diagnostico_configuracion_pagos())
159
 
160
if st.session_state.get("ultimo_error_pago"):
161
st.code(st.session_state.ultimo_error_pago)
162
 
163
if st.session_state.get("errores_proveedores"):
164
st.code("\n".join(st.session_state.errores_proveedores))
165
 
166
 
167
def normalizar_fecha_unix(fecha_txt: str, dias_antes: int = 2, dias_despues: int = 2) -> dict:
168
try:
169
fecha = pd.to_datetime(str(fecha_txt)).to_pydatetime()
170
except Exception:
171
fecha = datetime.now()
172
 
173
return {
174
"gte": int((fecha - timedelta(days=dias_antes)).timestamp()),
175
"lte": int((fecha + timedelta(days=dias_despues)).timestamp()),
176
}
177
 
178
 
179
# ============================================================
180
# CACHE DE PRE RESERVAS
181
# ============================================================
182
@st.cache_resource
183
def obtener_pre_reservas_globales() -> dict:
184
return {}
185
 
186
 
187
def limpiar_pre_reservas_expiradas(pre_reservas: dict):
188
ahora = datetime.now()
189
expirados = [k for k, v in list(pre_reservas.items()) if v["expires_at"] < ahora]
190
 
191
for k in expirados:
192
del pre_reservas[k]
193
 
194
 
195
def limpiar_carrito_local():
196
pre_reservas = obtener_pre_reservas_globales()
197
 
198
for t in list(st.session_state.get("selected_tickets", [])):
199
if t in pre_reservas and pre_reservas[t]["session_id"] == st.session_state.session_id:
200
del pre_reservas[t]
201
 
202
st.session_state.selected_tickets = []
203
st.session_state.pago_generado_url = None
204
st.session_state.stripe_pago_url = None
205
st.session_state.stripe_session_id = None
206
st.session_state.payment_provider = None
207
st.session_state.external_ref_activa = None
208
 
209
 
210
# ============================================================
211
# CSS
212
# ============================================================
213
CSS_CUSTOM = """
214
<style>
215
[data-testid="column"] { padding: 0 4px !important; }
216
 
217
[data-testid="stButton"] button {
218
width: 100%;
219
height: 55px;
220
padding: 0;
221
font-weight: 700;
222
font-size: 14px;
223
transition: all 0.2s;
224
}
225
 
226
[data-testid="stButton"] button:hover {
227
transform: scale(1.02);
228
border-color: #004481;
229
}
230
 
231
.metric-container {
232
display: flex;
233
justify-content: space-between;
234
gap: 10px;
235
margin-bottom: 20px;
236
flex-wrap: wrap;
237
}
238
 
239
.metric-box {
240
flex: 1;
241
min-width: 120px;
242
background: white;
243
padding: 10px;
244
border-radius: 8px;
245
text-align: center;
246
box-shadow: 0 2px 5px rgba(0,0,0,0.05);
247
border-top: 4px solid;
248
}
249
 
250
.metric-box h2 {
251
margin: 0;
252
font-size: 20px;
253
font-weight: 800;
254
color: #0A2540;
255
}
256
 
257
.metric-box p {
258
margin: 0;
259
font-size: 12px;
260
color: #64748B;
261
font-weight: 600;
262
}
263
 
264
.m-green { border-color: #20C997; }
265
.m-gray { border-color: #94A3B8; }
266
.m-yellow { border-color: #F59E0B; }
267
.m-red { border-color: #EF4444; }
268
</style>
269
"""
270
 
271
 
272
# ============================================================
273
# GOOGLE SHEETS
274
# ============================================================
275
def columnas_ventas() -> list:
276
return [
277
"ID_Boleto",
278
"Nombre",
279
"Correo",
280
"Evento",
281
"Numero_Boleto",
282
"Precio",
283
"Metodo_Pago",
284
"Codigo_Pago",
285
"Fecha_Compra",
286
"Numero_Telefonico",
287
"Estado_Pago",
288
"Referencia_Pago",
289
"MercadoPago_Payment_ID",
290
"MercadoPago_Preference_ID",
291
"Stripe_Payment_ID",
292
"Stripe_Session_ID",
293
"Proveedor_Pago",
294
]
295
 
296
 
297
def columnas_reservas() -> list:
298
return [
299
"External_Reference",
300
"MercadoPago_Preference_ID",
301
"MercadoPago_Payment_ID",
302
"Stripe_Session_ID",
303
"Stripe_Payment_ID",
304
"Numero_Boleto",
305
"Nombre",
306
"Correo",
307
"Numero_Telefonico",
308
"Monto",
309
"Estado_Reserva",
310
"Fecha_Creacion",
311
"Expira_En",
312
"Fecha_Actualizacion",
313
]
314
 
315
 
316
def asegurar_columnas(df: pd.DataFrame, cols: list) -> pd.DataFrame:
317
for col in cols:
318
if col not in df.columns:
319
df[col] = ""
320
 
321
return df[cols]
322
 
323
 
324
def leer_reservas(conn: GSheetsConnection) -> pd.DataFrame:
325
try:
326
return asegurar_columnas(
327
conn.read(worksheet="Reservas", ttl=0).dropna(how="all"),
328
columnas_reservas(),
329
)
330
except Exception:
331
return pd.DataFrame(columns=columnas_reservas())
332
 
333
 
334
def leer_ventas(conn: GSheetsConnection) -> pd.DataFrame:
335
try:
336
return asegurar_columnas(
337
conn.read(worksheet="Ventas", ttl=0).dropna(how="all"),
338
columnas_ventas(),
339
)
340
except Exception:
341
return pd.DataFrame(columns=columnas_ventas())
342
 
343
 
344
def registrar_reserva_cobro(conn: GSheetsConnection, ordenes: List[Dict[str, Any]]) -> Tuple[bool, str]:
345
try:
346
df_r = leer_reservas(conn)
347
 
348
df_actualizado = pd.concat(
349
[
350
asegurar_columnas(df_r.dropna(how="all"), columnas_reservas()),
351
asegurar_columnas(pd.DataFrame(ordenes), columnas_reservas()),
352
],
353
ignore_index=True,
354
)
355
 
356
conn.update(worksheet="Reservas", data=df_actualizado)
357
return True, "Éxito"
358
 
359
except Exception as e:
360
return False, str(e)
361
 
362
 
363
def actualizar_ids_proveedores_reserva(
364
conn: GSheetsConnection,
365
external_reference: str,
366
mercado_pago_preference_id: str = "",
367
stripe_session_id: str = "",
368
) -> Tuple[bool, str]:
369
try:
370
df_r = leer_reservas(conn)
371
filtro = df_r["External_Reference"].astype(str) == str(external_reference)
372
 
373
if not filtro.any():
374
return False, "No se encontró la reserva para actualizar."
375
 
376
if mercado_pago_preference_id:
377
df_r.loc[filtro, "MercadoPago_Preference_ID"] = mercado_pago_preference_id
378
 
379
if stripe_session_id:
380
df_r.loc[filtro, "Stripe_Session_ID"] = stripe_session_id
381
 
382
df_r.loc[filtro, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
383
conn.update(worksheet="Reservas", data=df_r)
384
 
385
return True, "Éxito"
386
 
387
except Exception as e:
388
return False, str(e)
389
 
390
 
391
def marcar_reserva_estado(conn: GSheetsConnection, external_reference: str, estado: str) -> Tuple[bool, str]:
392
try:
393
if not external_reference:
394
return False, "Sin referencia."
395
 
396
df_r = leer_reservas(conn)
397
filtro = df_r["External_Reference"].astype(str) == str(external_reference)
398
 
399
if not filtro.any():
400
return False, "No se encontró la reserva."
401
 
402
df_r.loc[filtro, "Estado_Reserva"] = estado
403
df_r.loc[filtro, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
404
conn.update(worksheet="Reservas", data=df_r)
405
 
406
return True, "Éxito"
407
 
408
except Exception as e:
409
return False, str(e)
410
 
411
 
412
def liberar_reserva_por_rechazo_o_cancelacion(
413
conn: GSheetsConnection,
414
external_reference: str,
415
motivo: str = "CANCELADO_PAGO",
416
) -> Tuple[bool, str]:
417
try:
418
if not external_reference:
419
return False, "No se recibió External_Reference para liberar la reserva."
420
 
421
df_r = leer_reservas(conn)
422
filtro = df_r["External_Reference"].astype(str) == str(external_reference)
423
 
424
if not filtro.any():
425
return True, "No había reservas por liberar."
426
 
427
df_r.loc[filtro, "Estado_Reserva"] = motivo
428
df_r.loc[filtro, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
429
conn.update(worksheet="Reservas", data=df_r)
430
 
431
return True, "Reserva liberada correctamente."
432
 
433
except Exception as e:
434
return False, str(e)
435
 
436
 
437
def actualizar_pago_en_hojas(conn: GSheetsConnection, payment_info: Dict[str, Any]) -> List[Dict[str, Any]]:
438
ext_ref = str(payment_info.get("external_reference", "")).strip()
439
pago_id = str(payment_info.get("id", "")).strip()
440
proveedor = str(payment_info.get("provider", "MERCADO_PAGO")).strip().upper()
441
metodo_pago = str(payment_info.get("payment_type_id", proveedor.lower())).strip()
442
provider_session_id = str(payment_info.get("provider_session_id", "")).strip()
443
 
444
if not ext_ref:
445
return []
446
 
447
df_r = leer_reservas(conn)
448
df_v = leer_ventas(conn)
449
 
450
ventas_ref = df_v[df_v["Referencia_Pago"].astype(str) == ext_ref]
451
 
452
if not ventas_ref.empty:
453
return ventas_ref.to_dict(orient="records")
454
 
455
filtro_reserva = df_r["External_Reference"].astype(str) == ext_ref
456
 
457
if not filtro_reserva.any():
458
return []
459
 
460
df_r.loc[filtro_reserva, "Estado_Reserva"] = "PAGADO"
461
df_r.loc[filtro_reserva, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
462
 
463
if proveedor == "STRIPE":
464
df_r.loc[filtro_reserva, "Stripe_Payment_ID"] = pago_id
465
 
466
if provider_session_id:
467
df_r.loc[filtro_reserva, "Stripe_Session_ID"] = provider_session_id
468
else:
469
df_r.loc[filtro_reserva, "MercadoPago_Payment_ID"] = pago_id
470
 
471
conn.update(worksheet="Reservas", data=df_r)
472
 
473
nuevas_ventas = []
474
 
475
for _, r in df_r[filtro_reserva].iterrows():
476
nuevas_ventas.append(
477
{
478
"ID_Boleto": f"BOL-{random.randint(10000, 99999)}",
479
"Nombre": r["Nombre"],
480
"Correo": r["Correo"],
481
"Evento": "Rifa de Celular",
482
"Numero_Boleto": parse_ticket_number(r["Numero_Boleto"]),
483
"Precio": r["Monto"],
484
"Metodo_Pago": metodo_pago,
485
"Codigo_Pago": pago_id,
486
"Fecha_Compra": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
487
"Numero_Telefonico": r["Numero_Telefonico"],
488
"Estado_Pago": "VENDIDO",
489
"Referencia_Pago": ext_ref,
490
"MercadoPago_Payment_ID": pago_id if proveedor != "STRIPE" else "",
491
"MercadoPago_Preference_ID": r.get("MercadoPago_Preference_ID", ""),
492
"Stripe_Payment_ID": pago_id if proveedor == "STRIPE" else "",
493
"Stripe_Session_ID": provider_session_id if proveedor == "STRIPE" else "",
494
"Proveedor_Pago": proveedor,
495
}
496
)
497
 
498
df_final = pd.concat(
499
[
500
df_v,
501
asegurar_columnas(pd.DataFrame(nuevas_ventas), columnas_ventas()),
502
],
503
ignore_index=True,
504
)
505
 
506
conn.update(worksheet="Ventas", data=df_final)
507
return nuevas_ventas
508
 
509
 
510
# ============================================================
511
# PDF
512
# ============================================================
513
def dibujar_fondo_autenticidad(canvas, doc):
514
width, height = doc.pagesize
515
canvas.saveState()
516
canvas.setFillColor(colors.HexColor("#F8FAFC"))
517
canvas.rect(0, 0, width, height, fill=1, stroke=0)
518
canvas.setStrokeColor(colors.HexColor("#0A2540"))
519
canvas.setLineWidth(1)
520
canvas.rect(24, 24, width - 48, height - 48, fill=0, stroke=1)
521
canvas.restoreState()
522
 
523
 
524
def generar_pdf_boleto(datos_boletos: List[Dict[str, Any]]) -> str:
525
codigo_pago = datos_boletos[0].get("Codigo_Pago", "Generico")
526
nombre_archivo = f"Boletos_Oficiales_{codigo_pago}.pdf"
527
 
528
doc = SimpleDocTemplate(
529
nombre_archivo,
530
pagesize=letter,
531
rightMargin=32,
532
leftMargin=32,
533
topMargin=40,
534
bottomMargin=32,
535
)
536
 
537
story = []
538
styles = getSampleStyleSheet()
539
 
540
estilo_titulo = ParagraphStyle(
541
"Titulo",
542
parent=styles["Heading1"],
543
fontSize=19,
544
textColor=colors.HexColor("#0A2540"),
545
alignment=1,
546
)
547
 
548
estilo_normal = ParagraphStyle(
549
"Texto",
550
parent=styles["Normal"],
551
fontSize=10.5,
552
leading=13,
553
textColor=colors.HexColor("#334155"),
554
)
555
 
556
for idx, boleto in enumerate(datos_boletos):
557
story.append(Paragraph("BOLETO OFICIAL DE COMPRA", estilo_titulo))
558
story.append(Spacer(1, 16))
559
 
560
precio_float = float(boleto.get("Precio", 0))
561
 
562
data = [
563
[Paragraph("<b>ID de Boleto:</b>", estilo_normal), Paragraph(str(boleto.get("ID_Boleto", "")), estilo_normal)],
564
[Paragraph("<b>Nombre:</b>", estilo_normal), Paragraph(str(boleto.get("Nombre", "")), estilo_normal)],
565
[Paragraph("<b>N° de Boleto:</b>", estilo_normal), Paragraph(str(boleto.get("Numero_Boleto", "")), estilo_normal)],
566
[Paragraph("<b>Precio Pagado:</b>", estilo_normal), Paragraph(f"${precio_float:.2f} {MP_CURRENCY_ID}", estilo_normal)],
567
[Paragraph("<b>Método de Pago:</b>", estilo_normal), Paragraph(str(boleto.get("Metodo_Pago", "Pago electrónico")).upper(), estilo_normal)],
568
[Paragraph("<b>Ref / ID Pago:</b>", estilo_normal), Paragraph(str(boleto.get("Codigo_Pago", "N/A")), estilo_normal)],
569
[Paragraph("<b>Fecha:</b>", estilo_normal), Paragraph(str(boleto.get("Fecha_Compra", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))), estilo_normal)],
570
]
571
 
572
tabla = Table(data, colWidths=[165, 300])
573
tabla.setStyle(
574
TableStyle(
575
[
576
("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
577
("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#E2E8F0")),
578
("TOPPADDING", (0, 0), (-1, -1), 8),
579
("BOTTOMPADDING", (0, 0), (-1, -1), 8),
580
]
581
)
582
)
583
 
584
story.append(tabla)
585
 
586
if idx < len(datos_boletos) - 1:
587
story.append(PageBreak())
588
 
589
doc.build(
590
story,
591
onFirstPage=dibujar_fondo_autenticidad,
592
onLaterPages=dibujar_fondo_autenticidad,
593
)
594
 
595
return nombre_archivo
596
 
597
 
598
def procesar_descarga_pdf(datos_boletos: List[dict]):
599
if not datos_boletos:
600
st.warning("No hay boletos disponibles para generar PDF.")
601
return
602
 
603
archivo_pdf = generar_pdf_boleto(datos_boletos)
604
 
605
with open(archivo_pdf, "rb") as pdf_file:
606
pdf_bytes = pdf_file.read()
607
 
608
label = (
609
"Descargar mis Boletos Oficiales (PDF)"
610
if len(datos_boletos) > 1
611
else "Descargar mi Boleto Oficial (PDF)"
612
)
613
 
614
st.download_button(
615
label=label,
616
data=pdf_bytes,
617
file_name=archivo_pdf,
618
mime="application/pdf",
619
type="primary",
620
use_container_width=True,
621
)
622
 
623
 
624
# ============================================================
625
# STRIPE
626
# ============================================================
627
def construir_pago_stripe_desde_session(
628
session: Any,
629
external_reference_reserva: str = "",
630
correo_reserva: str = "",
631
monto_esperado: Optional[float] = None,
632
) -> Optional[Dict[str, Any]]:
633
if session.get("payment_status") != "paid":
634
return None
635
 
636
metadata = dict(session.get("metadata") or {})
637
 
638
ref_stripe = str(
639
metadata.get("external_reference")
640
or session.get("client_reference_id")
641
or ""
642
).strip()
643
 
644
customer_details = session.get("customer_details") or {}
645
 
646
correo_stripe = str(
647
session.get("customer_email")
648
or customer_details.get("email")
649
or ""
650
).strip().lower()
651
 
652
external_reference_reserva = str(external_reference_reserva or "").strip()
653
correo_reserva = str(correo_reserva or "").strip().lower()
654
 
655
monto_ok = True
656
 
657
if monto_esperado is not None:
658
monto_recibido_centavos = int(session.get("amount_total") or 0)
659
monto_esperado_centavos = int(round(float(monto_esperado) * 100))
660
monto_ok = monto_recibido_centavos == monto_esperado_centavos
661
 
662
ref_ok = bool(external_reference_reserva and ref_stripe == external_reference_reserva)
663
correo_ok = bool(correo_reserva and correo_stripe == correo_reserva)
664
 
665
if external_reference_reserva and not (ref_ok or (correo_ok and monto_ok)):
666
return None
667
 
668
payment_intent = session.get("payment_intent")
669
 
670
if isinstance(payment_intent, str):
671
payment_intent_id = payment_intent
672
elif payment_intent:
673
payment_intent_id = str(payment_intent.get("id", ""))
674
else:
675
payment_intent_id = str(session.get("id", ""))
676
 
677
return {
678
"id": payment_intent_id,
679
"external_reference": ref_stripe or external_reference_reserva,
680
"payment_type_id": "stripe_card",
681
"status": "approved",
682
"provider": "STRIPE",
683
"provider_session_id": str(session.get("id", "")),
684
}
685
 
686
 
687
def obtener_pago_stripe(
688
stripe_session_id: str,
689
external_reference_esperada: Optional[str] = None,
690
monto_esperado: Optional[float] = None,
691
correo_reserva: str = "",
692
) -> Optional[Dict[str, Any]]:
693
if not STRIPE_SECRET_KEY or not stripe_session_id:
694
return None
695
 
696
try:
697
session = stripe.checkout.Session.retrieve(
698
stripe_session_id,
699
expand=["payment_intent"],
700
)
701
 
702
return construir_pago_stripe_desde_session(
703
session=session,
704
external_reference_reserva=external_reference_esperada or "",
705
correo_reserva=correo_reserva,
706
monto_esperado=monto_esperado,
707
)
708
 
709
except Exception as e:
710
st.session_state.ultimo_error_pago = f"Stripe retrieve: {e}"
711
return None
712
 
713
 
714
def buscar_pago_stripe_por_referencia_correo_fecha(
715
external_reference: str,
716
correo: str,
717
monto_esperado: Optional[float],
718
fecha_creacion_reserva: str,
719
) -> Optional[Dict[str, Any]]:
720
if not STRIPE_SECRET_KEY:
721
return None
722
 
723
external_reference = str(external_reference or "").strip()
724
correo = str(correo or "").strip().lower()
725
rango_fecha = normalizar_fecha_unix(fecha_creacion_reserva)
726
 
727
try:
728
parametros = {
729
"limit": 100,
730
"status": "complete",
731
"created": rango_fecha,
732
"customer_details": {"email": correo},
733
"expand": ["data.payment_intent"],
734
}
735
 
736
sesiones = stripe.checkout.Session.list(**parametros)
737
 
738
for session in sesiones.get("data", []):
739
pago = construir_pago_stripe_desde_session(
740
session=session,
741
external_reference_reserva=external_reference,
742
correo_reserva=correo,
743
monto_esperado=monto_esperado,
744
)
745
 
746
if pago:
747
return pago
748
 
749
starting_after = None
750
 
751
for _ in range(10):
752
params = {
753
"limit": 100,
754
"status": "complete",
755
"created": rango_fecha,
756
"expand": ["data.payment_intent"],
757
}
758
 
759
if starting_after:
760
params["starting_after"] = starting_after
761
 
762
sesiones = stripe.checkout.Session.list(**params)
763
data = sesiones.get("data", [])
764
 
765
for session in data:
766
pago = construir_pago_stripe_desde_session(
767
session=session,
768
external_reference_reserva=external_reference,
769
correo_reserva=correo,
770
monto_esperado=monto_esperado,
771
)
772
 
773
if pago:
774
return pago
775
 
776
if not sesiones.get("has_more") or not data:
777
break
778
 
779
starting_after = str(data[-1].get("id"))
780
 
781
return None
782
 
783
except Exception as e:
784
st.session_state.ultimo_error_pago = f"Stripe conciliación: {e}"
785
return None
786
 
787
 
788
def crear_sesion_stripe(
789
nombre: str,
790
apellidos: str,
791
correo: str,
792
numeros_boletos: List[str],
793
monto_unitario: float,
794
external_reference: str,
795
) -> Tuple[str, str]:
796
if not STRIPE_SECRET_KEY:
797
raise ValueError("STRIPE_SECRET_KEY no está configurado.")
798
 
799
if STRIPE_SECRET_KEY.startswith("pk_"):
800
raise ValueError("STRIPE_SECRET_KEY contiene una llave pública pk_. Usa sk_test_ o sk_live_.")
801
 
802
if not STRIPE_RETURN_URL:
803
raise ValueError("STRIPE_RETURN_URL no está configurado.")
804
 
805
url_base = normalizar_url(STRIPE_RETURN_URL)
806
 
807
success_url = agregar_parametros_url(
808
url_base,
809
{
810
"stripe_session_id": "{CHECKOUT_SESSION_ID}",
811
"external_reference": external_reference,
812
},
813
)
814
 
815
cancel_url = agregar_parametros_url(
816
url_base,
817
{
818
"stripe_cancelled": "true",
819
"external_reference": external_reference,
820
},
821
)
822
 
823
descripcion_boletos = ", ".join(numeros_boletos)
824
 
825
session = stripe.checkout.Session.create(
826
mode="payment",
827
customer_email=correo.strip().lower(),
828
client_reference_id=external_reference,
829
line_items=[
830
{
831
"price_data": {
832
"currency": STRIPE_CURRENCY_ID,
833
"unit_amount": int(round(float(monto_unitario) * 100)),
834
"product_data": {
835
"name": "Boletos Rifa de Celular",
836
"description": f"Boletos seleccionados: {descripcion_boletos}"[:500],
837
},
838
},
839
"quantity": len(numeros_boletos),
840
}
841
],
842
metadata={
843
"external_reference": external_reference,
844
"boletos": descripcion_boletos,
845
"nombre_cliente": f"{nombre.strip()} {apellidos.strip()}"[:500],
846
},
847
payment_intent_data={
848
"metadata": {
849
"external_reference": external_reference,
850
}
851
},
852
success_url=success_url,
853
cancel_url=cancel_url,
854
)
855
 
856
return str(session.id), str(session.url)
857
 
858
 
859
# ============================================================
860
# MERCADO PAGO
861
# ============================================================
862
def buscar_pago_mercadopago_por_referencia_correo_fecha(
863
external_reference: str,
864
correo: str,
865
monto_esperado: Optional[float],
866
fecha_creacion_reserva: str,
867
) -> Optional[Dict[str, Any]]:
868
if not sdk:
869
return None
870
 
871
external_reference = str(external_reference or "").strip()
872
correo = str(correo or "").strip().lower()
873
 
874
def monto_ok(pago: Dict[str, Any]) -> bool:
875
if monto_esperado is None:
876
return True
877
 
878
try:
879
return abs(float(pago.get("transaction_amount", 0)) - float(monto_esperado)) < 0.01
880
except Exception:
881
return False
882
 
883
try:
884
pagos = []
885
 
886
if external_reference:
887
respuesta = sdk.payment().search(
888
{
889
"external_reference": external_reference,
890
"status": "approved",
891
"sort": "date_created",
892
"criteria": "desc",
893
}
894
).get("response", {})
895
 
896
pagos = respuesta.get("results", [])
897
 
898
for pago in pagos:
899
if pago.get("status") == "approved" and monto_ok(pago):
900
pago["provider"] = "MERCADO_PAGO"
901
 
902
if not pago.get("external_reference"):
903
pago["external_reference"] = external_reference
904
 
905
return pago
906
 
907
for consulta in [{"payer.email": correo}, {"payer_email": correo}]:
908
try:
909
respuesta = sdk.payment().search(consulta).get("response", {})
910
pagos = respuesta.get("results", [])
911
except Exception:
912
pagos = []
913
 
914
for pago in pagos:
915
payer = pago.get("payer") or {}
916
correo_pago = str(payer.get("email") or "").strip().lower()
917
ref_pago = str(pago.get("external_reference") or "").strip()
918
 
919
if (
920
pago.get("status") == "approved"
921
and monto_ok(pago)
922
and (ref_pago == external_reference or correo_pago == correo)
923
):
924
pago["provider"] = "MERCADO_PAGO"
925
 
926
if not pago.get("external_reference"):
927
pago["external_reference"] = external_reference
928
 
929
return pago
930
 
931
return None
932
 
933
except Exception as e:
934
st.session_state.ultimo_error_pago = f"MP conciliación: {e}"
935
return None
936
 
937
 
938
def crear_preferencia_mercado_pago(
939
nombre,
940
apellidos,
941
correo,
942
telefono,
943
numeros_boletos: list,
944
monto_unitario,
945
external_reference,
946
custom_return_scheme: Optional[str] = None,
947
):
948
if not sdk:
949
return "", ""
950
 
951
url_retorno_base = normalizar_url(custom_return_scheme if custom_return_scheme else MP_RETURN_URL)
952
titulos_boletos = ", ".join(numeros_boletos)
953
 
954
preference_data = {
955
"items": [
956
{
957
"title": f"Rifa celular - Boletos: {titulos_boletos}",
958
"quantity": len(numeros_boletos),
959
"unit_price": float(monto_unitario),
960
"currency_id": MP_CURRENCY_ID,
961
}
962
],
963
"payer": {
964
"name": nombre.strip(),
965
"surname": apellidos.strip() or "Sin Apellido",
966
"email": correo,
967
"phone": {
968
"area_code": "52",
969
"number": telefono,
970
},
971
},
972
"external_reference": external_reference,
973
"payment_methods": {
974
"excluded_payment_methods": [],
975
"excluded_payment_types": [],
976
"installments": 1,
977
},
978
"statement_descriptor": "RIFA CELULAR",
979
}
980
 
981
if MP_NOTIFICATION_URL:
982
preference_data["notification_url"] = MP_NOTIFICATION_URL
983
 
984
if url_retorno_base:
985
preference_data["back_urls"] = {
986
"success": agregar_parametros_url(
987
url_retorno_base,
988
{
989
"mp_return": "success",
990
"external_reference": external_reference,
991
},
992
),
993
"pending": agregar_parametros_url(
994
url_retorno_base,
995
{
996
"mp_return": "pending",
997
"external_reference": external_reference,
998
},
999
),
1000
"failure": agregar_parametros_url(
1001
url_retorno_base,
1002
{
1003
"mp_return": "failure",
1004
"external_reference": external_reference,
1005
},
1006
),
1007
}
1008
preference_data["auto_return"] = "approved"
1009
 
1010
preference = sdk.preference().create(preference_data).get("response", {})
1011
 
1012
if "id" not in preference:
1013
raise Exception(f"Rechazado por MP: {preference.get('message', 'Error en credenciales o URL de retorno')}")
1014
 
1015
return preference.get("id", ""), preference.get("init_point") or preference.get("sandbox_init_point", "")
1016
 
1017
 
1018
# ============================================================
1019
# RECUPERAR BOLETOS
1020
# ============================================================
1021
def recuperar_boletos_por_reserva(conn: GSheetsConnection, numero_boleto: str, correo: str) -> List[Dict[str, Any]]:
1022
df_v = leer_ventas(conn)
1023
correo_limpio = correo.strip().lower()
1024
num = parse_ticket_number(numero_boleto)
1025
 
1026
if not df_v.empty:
1027
filtro_v = (
1028
(df_v["Numero_Boleto"].astype(str).apply(parse_ticket_number) == num)
1029
& (df_v["Correo"].astype(str).str.lower() == correo_limpio)
1030
)
1031
 
1032
if filtro_v.any():
1033
ref = str(df_v[filtro_v].iloc[-1].get("Referencia_Pago", ""))
1034
return df_v[df_v["Referencia_Pago"].astype(str) == ref].to_dict(orient="records")
1035
 
1036
df_r = leer_reservas(conn)
1037
 
1038
filtro_r = (
1039
(df_r["Numero_Boleto"].astype(str).apply(parse_ticket_number) == num)
1040
& (df_r["Correo"].astype(str).str.lower() == correo_limpio)
1041
)
1042
 
1043
reservas = df_r[filtro_r]
1044
 
1045
if reservas.empty:
1046
return []
1047
 
1048
reserva_base = reservas.iloc[-1]
1049
ext_ref = str(reserva_base.get("External_Reference", "")).strip()
1050
 
1051
if not ext_ref:
1052
return []
1053
 
1054
grupo = df_r[df_r["External_Reference"].astype(str) == ext_ref]
1055
total = float(grupo["Monto"].astype(float).sum()) if not grupo.empty else None
1056
fecha_creacion = str(reserva_base.get("Fecha_Creacion", ""))
1057
 
1058
pago = buscar_pago_mercadopago_por_referencia_correo_fecha(
1059
external_reference=ext_ref,
1060
correo=correo_limpio,
1061
monto_esperado=total,
1062
fecha_creacion_reserva=fecha_creacion,
1063
)
1064
 
1065
if not pago:
1066
stripe_session_id = ""
1067
 
1068
for _, r in grupo.iterrows():
1069
posible = str(r.get("Stripe_Session_ID", "")).strip()
1070
 
1071
if posible:
1072
stripe_session_id = posible
1073
break
1074
 
1075
if stripe_session_id:
1076
pago = obtener_pago_stripe(
1077
stripe_session_id=stripe_session_id,
1078
external_reference_esperada=ext_ref,
1079
monto_esperado=total,
1080
correo_reserva=correo_limpio,
1081
)
1082
 
1083
if not pago:
1084
pago = buscar_pago_stripe_por_referencia_correo_fecha(
1085
external_reference=ext_ref,
1086
correo=correo_limpio,
1087
monto_esperado=total,
1088
fecha_creacion_reserva=fecha_creacion,
1089
)
1090
 
1091
if pago:
1092
datos = actualizar_pago_en_hojas(conn, pago)
1093
 
1094
if datos:
1095
return datos
1096
 
1097
df_v = leer_ventas(conn)
1098
ventas_ref = df_v[df_v["Referencia_Pago"].astype(str) == ext_ref]
1099
 
1100
if not ventas_ref.empty:
1101
return ventas_ref.to_dict(orient="records")
1102
 
1103
return []
1104
 
1105
 
1106
# ============================================================
1107
# MAPA DE DISPONIBILIDAD
1108
# ============================================================
1109
def obtener_estado_boletos_bd(df_ventas: pd.DataFrame, df_reservas: pd.DataFrame) -> dict:
1110
estados = {}
1111
 
1112
estados_reserva_bloqueantes = [
1113
"PENDIENTE",
1114
"ERROR_CONFIRMACION_STRIPE",
1115
"ERROR_CONFIRMACION_MERCADO_PAGO",
1116
]
1117
 
1118
if not df_reservas.empty and "Numero_Boleto" in df_reservas.columns:
1119
for _, row in df_reservas.iterrows():
1120
if str(row.get("Estado_Reserva", "")).strip().upper() in estados_reserva_bloqueantes:
1121
try:
1122
expira = pd.to_datetime(str(row.get("Expira_En"))).to_pydatetime()
1123
except Exception:
1124
expira = None
1125
 
1126
if expira is None or datetime.now() <= expira:
1127
num = parse_ticket_number(row["Numero_Boleto"])
1128
 
1129
if num:
1130
estados[num] = "reservado_db"
1131
 
1132
if not df_ventas.empty and "Numero_Boleto" in df_ventas.columns:
1133
for _, row in df_ventas.iterrows():
1134
if str(row.get("Estado_Pago", "")).strip().upper() in ["APROBADO", "VENDIDO"]:
1135
num = parse_ticket_number(row["Numero_Boleto"])
1136
 
1137
if num:
1138
estados[num] = "vendido_db"
1139
 
1140
return estados
1141
 
1142
 
1143
@st.fragment(run_every=5)
1144
def renderizar_mapa_interactivo():
1145
mi_sesion = st.session_state.session_id
1146
pre_reservas = obtener_pre_reservas_globales()
1147
limpiar_pre_reservas_expiradas(pre_reservas)
1148
 
1149
if not (st.session_state.get("pago_generado_url") or st.session_state.get("stripe_pago_url")):
1150
st.session_state.selected_tickets = [
1151
t
1152
for t in st.session_state.selected_tickets
1153
if t in pre_reservas and pre_reservas[t]["session_id"] == mi_sesion
1154
]
1155
 
1156
conn = st.connection("gsheets", type=GSheetsConnection)
1157
 
1158
try:
1159
df_v = conn.read(worksheet="Ventas", ttl=5).dropna(how="all")
1160
df_r = conn.read(worksheet="Reservas", ttl=5).dropna(how="all")
1161
except Exception:
1162
df_v = pd.DataFrame(columns=columnas_ventas())
1163
df_r = pd.DataFrame(columns=columnas_reservas())
1164
 
1165
estados_bd = obtener_estado_boletos_bd(df_v, df_r)
1166
 
1167
estados_pantalla = {}
1168
vendidos = 0
1169
reservados_bd = 0
1170
pre_reservados_otros = 0
1171
 
1172
for i in range(TOTAL_BOLETOS):
1173
num = f"{i:03d}"
1174
 
1175
if num in estados_bd:
1176
estados_pantalla[num] = estados_bd[num]
1177
 
1178
if estados_bd[num] == "vendido_db":
1179
vendidos += 1
1180
elif estados_bd[num] == "reservado_db":
1181
reservados_bd += 1
1182
 
1183
elif num in pre_reservas:
1184
if pre_reservas[num]["session_id"] == mi_sesion:
1185
estados_pantalla[num] = "pre_reservado_mio"
1186
else:
1187
estados_pantalla[num] = "pre_reservado_otros"
1188
pre_reservados_otros += 1
1189
else:
1190
estados_pantalla[num] = "disponible"
1191
 
1192
disponibles = TOTAL_BOLETOS - vendidos - reservados_bd - pre_reservados_otros - len(st.session_state.selected_tickets)
1193
 
1194
st.markdown(
1195
f"""
1196
<div class="metric-container">
1197
<div class="metric-box m-green"><h2>🟢 {disponibles}</h2><p>Libres</p></div>
1198
<div class="metric-box m-gray"><h2>🔒 {pre_reservados_otros}</h2><p>En otro carrito</p></div>
1199
<div class="metric-box m-yellow"><h2>🟡 {reservados_bd}</h2><p>Por pagar / validando</p></div>
1200
<div class="metric-box m-red"><h2>🔴 {vendidos}</h2><p>Vendidos</p></div>
1201
</div>
1202
""",
1203
unsafe_allow_html=True,
1204
)
1205
 
1206
for fila in range(10):
1207
cols = st.columns(10)
1208
 
1209
for col_idx in range(10):
1210
num = f"{(fila * 10 + col_idx):03d}"
1211
estado = estados_pantalla[num]
1212
 
1213
with colsif estado == "vendido_db":
1214
st.button(f"🔴\n{num}", disabled=True, key=f"btn_{num}")
1215
elif estado == "reservado_db":
1216
st.button(f"🟡\n{num}", disabled=True, key=f"btn_{num}")
1217
elif estado == "pre_reservado_otros":
1218
st.button(f"🔒\n{num}", disabled=True, key=f"btn_{num}")
1219
else:
1220
seleccionado = estado == "pre_reservado_mio" or num in st.session_state.selected_tickets
1221
etiqueta = f"✅\n{num}" if seleccionado else f"🟢\n{num}"
1222
 
1223
if st.button(
1224
etiqueta,
1225
key=f"btn_{num}",
1226
type="primary" if seleccionado else "secondary",
1227
):
1228
if seleccionado:
1229
pre_reservas.pop(num, None)
1230
 
1231
if num in st.session_state.selected_tickets:
1232
st.session_state.selected_tickets.remove(num)
1233
else:
1234
pre_reservas[num] = {
1235
"session_id": mi_sesion,
1236
"expires_at": datetime.now() + timedelta(minutes=TIEMPO_PRERESERVA_MINUTOS),
1237
}
1238
 
1239
if num not in st.session_state.selected_tickets:
1240
st.session_state.selected_tickets.append(num)
1241
 
1242
st.rerun()
1243
 
1244
 
1245
# ============================================================
1246
# RETORNOS DE PAGO
1247
# ============================================================
1248
def procesar_retorno_pago(conn: GSheetsConnection):
1249
qp = st.query_params
1250
 
1251
mp_status = (qp_get(qp, "status", "") or qp_get(qp, "collection_status", "")).lower()
1252
mp_return = qp_get(qp, "mp_return", "").lower()
1253
payment_id = qp_get(qp, "payment_id", "") or qp_get(qp, "collection_id", "")
1254
ext_ref = qp_get(qp, "external_reference", "") or st.session_state.get("external_ref_activa", "")
1255
 
1256
if mp_return == "failure" or mp_status in ["rejected", "cancelled", "canceled", "failure", "failed"]:
1257
liberar_reserva_por_rechazo_o_cancelacion(conn, ext_ref, "CANCELADO_MERCADO_PAGO")
1258
limpiar_carrito_local()
1259
st.query_params.clear()
1260
st.warning("Pago cancelado o rechazado. La reserva fue liberada.")
1261
st.rerun()
1262
 
1263
if payment_id and mp_status == "approved":
1264
pago = None
1265
 
1266
try:
1267
respuesta = sdk.payment().get(payment_id).get("response", {}) if sdk else {}
1268
 
1269
if respuesta.get("status") == "approved":
1270
respuesta["provider"] = "MERCADO_PAGO"
1271
 
1272
if not respuesta.get("external_reference") and ext_ref:
1273
respuesta["external_reference"] = ext_ref
1274
 
1275
pago = respuesta
1276
 
1277
except Exception as e:
1278
st.session_state.ultimo_error_pago = f"MP return get: {e}"
1279
 
1280
if pago:
1281
datos = actualizar_pago_en_hojas(conn, pago)
1282
st.session_state.boletos_confirmados = datos
1283
st.session_state.payment_success_id = str(pago.get("id", payment_id))
1284
limpiar_carrito_local()
1285
st.session_state.boletos_confirmados = datos
1286
st.query_params.clear()
1287
st.rerun()
1288
else:
1289
marcar_reserva_estado(conn, ext_ref, "ERROR_CONFIRMACION_MERCADO_PAGO")
1290
st.query_params.clear()
1291
st.warning("El pago está en validación. Puedes recuperarlo en Buscar mis Boletos / Verificar Pago.")
1292
 
1293
if mp_return == "pending" or mp_status == "pending":
1294
st.query_params.clear()
1295
st.warning("Pago pendiente. La reserva se mantiene activa hasta confirmar el pago.")
1296
 
1297
if "stripe_cancelled" in qp:
1298
liberar_reserva_por_rechazo_o_cancelacion(conn, ext_ref, "CANCELADO_STRIPE")
1299
limpiar_carrito_local()
1300
st.query_params.clear()
1301
st.warning("Pago cancelado o rechazado. La reserva fue liberada.")
1302
st.rerun()
1303
 
1304
stripe_session_id = qp_get(qp, "stripe_session_id", "")
1305
 
1306
if stripe_session_id:
1307
pago = obtener_pago_stripe(
1308
stripe_session_id=stripe_session_id,
1309
external_reference_esperada=ext_ref if ext_ref else None,
1310
)
1311
 
1312
if pago:
1313
datos = actualizar_pago_en_hojas(conn, pago)
1314
st.session_state.boletos_confirmados = datos
1315
st.session_state.payment_success_id = str(pago.get("id", stripe_session_id))
1316
limpiar_carrito_local()
1317
st.session_state.boletos_confirmados = datos
1318
st.query_params.clear()
1319
st.rerun()
1320
else:
1321
marcar_reserva_estado(conn, ext_ref, "ERROR_CONFIRMACION_STRIPE")
1322
st.query_params.clear()
1323
st.warning("El pago está en validación. Puedes recuperarlo en Buscar mis Boletos / Verificar Pago.")
1324
 
1325
 
1326
# ============================================================
1327
# ESTADO INICIAL
1328
# ============================================================
1329
def inicializar_estado():
1330
defaults = {
1331
"session_id": str(uuid.uuid4()),
1332
"selected_tickets": [],
1333
"payment_success_id": None,
1334
"pago_generado_url": None,
1335
"stripe_pago_url": None,
1336
"stripe_session_id": None,
1337
"payment_provider": None,
1338
"errores_proveedores": [],
1339
"ultimo_error_pago": "",
1340
"external_ref_activa": None,
1341
"boletos_confirmados": [],
1342
}
1343
 
1344
for k, v in defaults.items():
1345
if k not in st.session_state:
1346
st.session_state[k] = v
1347
 
1348
 
1349
# ============================================================
1350
# MAIN APP
1351
# ============================================================
1352
def main():
1353
st.set_page_config(
1354
page_title="Rifa de Celular",
1355
page_icon="🎟️",
1356
layout="wide",
1357
)
1358
 
1359
st.markdown(CSS_CUSTOM, unsafe_allow_html=True)
1360
inicializar_estado()
1361
 
1362
if not MP_ACCESS_TOKEN:
1363
st.warning("Mercado Pago no está configurado.")
1364
 
1365
if not STRIPE_SECRET_KEY:
1366
st.warning("Stripe no está configurado.")
1367
 
1368
conn = st.connection("gsheets", type=GSheetsConnection)
1369
procesar_retorno_pago(conn)
1370
 
1371
st.title("Plataforma de Boletos - Gran Rifa")
1372
 
1373
tab1, tab2 = st.tabs(
1374
[
1375
"Comprar Boletos",
1376
"Buscar mis Boletos / Verificar Pago",
1377
]
1378
)
1379
 
1380
with tab2:
1381
st.markdown("### Consulta tus boletos")
1382
 
1383
col_b1, col_b2 = st.columns(2)
1384
 
1385
with col_b1:
1386
buscar_num = st.text_input("Número de boleto (ej. 005):")
1387
 
1388
with col_b2:
1389
buscar_correo = st.text_input("Correo asociado:")
1390
 
1391
if st.button("Verificar Pago y Descargar PDF", type="primary"):
1392
if not buscar_num or not buscar_correo:
1393
st.warning("Ingresa boleto y correo.")
1394
else:
1395
with st.spinner("Verificando pago y recuperando boletos..."):
1396
datos = recuperar_boletos_por_reserva(
1397
conn,
1398
buscar_num,
1399
buscar_correo,
1400
)
1401
 
1402
if datos:
1403
st.success("Boletos encontrados. Puedes descargar tu PDF.")
1404
procesar_descarga_pdf(datos)
1405
else:
1406
st.error("No encontramos boletos pagados con esos datos. Verifica correo y boleto o intenta nuevamente en unos segundos.")
1407
mostrar_diagnostico_pagos()
1408
 
1409
with tab1:
1410
if st.session_state.get("boletos_confirmados"):
1411
st.balloons()
1412
st.success(f"Compra confirmada. ID: {st.session_state.get('payment_success_id', 'N/A')}")
1413
procesar_descarga_pdf(st.session_state.boletos_confirmados)
1414
st.write("---")
1415
 
1416
if st.button("Realizar otra compra", use_container_width=True):
1417
st.session_state.boletos_confirmados = []
1418
st.session_state.payment_success_id = None
1419
limpiar_carrito_local()
1420
st.rerun()
1421
 
1422
st.stop()
1423
 
1424
col_mapa, col_form = st.columns([1.5, 1], gap="large")
1425
 
1426
with col_mapa:
1427
st.subheader("Mapa de Disponibilidad")
1428
renderizar_mapa_interactivo()
1429
 
1430
with col_form:
1431
st.subheader("Finalizar Compra")
1432
 
1433
boletos = st.session_state.selected_tickets
1434
 
1435
with st.container(border=True):
1436
if not boletos:
1437
st.info("Selecciona uno o más boletos disponibles.")
1438
st.session_state.pago_generado_url = None
1439
st.session_state.stripe_pago_url = None
1440
else:
1441
total_pagar = PRECIO_BOLETO * len(boletos)
1442
st.success(f"En tu carrito: {', '.join(boletos)}")
1443
 
1444
if st.session_state.pago_generado_url or st.session_state.stripe_pago_url:
1445
st.write(f"### Total a pagar: ${total_pagar:.2f} MXN")
1446
 
1447
opcion_pago = st.radio(
1448
"Elige tu método de pago seguro:",
1449
["Mercado Pago", "Stripe"],
1450
horizontal=True,
1451
)
1452
 
1453
if "Mercado Pago" in opcion_pago:
1454
if st.session_state.pago_generado_url:
1455
st.link_button(
1456
"Pagar en Mercado Pago",
1457
url=st.session_state.pago_generado_url,
1458
type="primary",
1459
use_container_width=True,
1460
)
1461
else:
1462
st.error("Mercado Pago no está disponible.")
1463
else:
1464
if st.session_state.stripe_pago_url:
1465
st.link_button(
1466
"Pagar con Stripe",
1467
url=st.session_state.stripe_pago_url,
1468
type="primary",
1469
use_container_width=True,
1470
)
1471
else:
1472
st.error("Stripe no está disponible.")
1473
 
1474
st.write("---")
1475
 
1476
if st.button("Cancelar reserva y vaciar carrito"):
1477
ext_ref = st.session_state.get("external_ref_activa", "")
1478
 
1479
if ext_ref:
1480
liberar_reserva_por_rechazo_o_cancelacion(
1481
conn,
1482
ext_ref,
1483
"CANCELADO_USUARIO",
1484
)
1485
 
1486
limpiar_carrito_local()
1487
st.rerun()
1488
 
1489
else:
1490
col_nom, col_ape = st.columns(2)
1491
 
1492
with col_nom:
1493
nombre = st.text_input("Nombre(s):")
1494
 
1495
with col_ape:
1496
apellidos = st.text_input("Apellidos:")
1497
 
1498
col_usr, col_dom = st.columns([3, 2.5])
1499
 
1500
with col_usr:
1501
correo_usuario = st.text_input(
1502
"Correo (sin @):",
1503
placeholder="ej. juanperez",
1504
)
1505
 
1506
with col_dom:
1507
dominio = st.selectbox(
1508
"Extensión:",
1509
[
1510
"@gmail.com",
1511
"@hotmail.com",
1512
"@outlook.com",
1513
"@yahoo.com",
1514
"Otro...",
1515
],
1516
)
1517
 
1518
if dominio == "Otro...":
1519
correo = st.text_input(
1520
"Correo completo:",
1521
placeholder="usuario@empresa.com",
1522
)
1523
else:
1524
correo = f"{correo_usuario.replace('@', '').strip()}{dominio}" if correo_usuario else ""
1525
 
1526
telefono = st.text_input("WhatsApp (10 dígitos):", max_chars=10)
1527
 
1528
st.write(f"**Total a Pagar:** ${total_pagar:.2f} MXN")
1529
 
1530
if st.button("Confirmar y Elegir Método de Pago", type="primary", use_container_width=True):
1531
pre_reservas = obtener_pre_reservas_globales()
1532
ahora = datetime.now()
1533
 
1534
siguen_validos = all(
1535
t in pre_reservas
1536
and pre_reservas[t]["session_id"] == st.session_state.session_id
1537
and pre_reservas[t]["expires_at"] > ahora
1538
for t in boletos
1539
)
1540
 
1541
correo_valido = re.match(
1542
r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}$",
1543
correo.strip().lower(),
1544
)
1545
 
1546
if not siguen_validos:
1547
st.error("El tiempo de carrito expiró. Selecciona nuevamente.")
1548
st.session_state.selected_tickets = []
1549
elif not nombre or not apellidos or not correo or not telefono:
1550
st.error("Completa todos los campos.")
1551
elif not correo_valido:
1552
st.error("El formato del correo NO es válido.")
1553
elif not (telefono.isdigit() and len(telefono) == 10):
1554
st.error("El número debe contener 10 dígitos numéricos.")
1555
else:
1556
ref = f"RIFA-{datetime.now().strftime('%Y%m%d%H%M%S')}-{random.randint(1000, 9999)}"
1557
st.session_state.external_ref_activa = ref
1558
 
1559
ordenes = []
1560
 
1561
for t in boletos:
1562
ordenes.append(
1563
{
1564
"External_Reference": ref,
1565
"MercadoPago_Preference_ID": "",
1566
"MercadoPago_Payment_ID": "",
1567
"Stripe_Session_ID": "",
1568
"Stripe_Payment_ID": "",
1569
"Numero_Boleto": str(t),
1570
"Nombre": f"{nombre.strip()} {apellidos.strip()}",
1571
"Correo": correo.strip().lower(),
1572
"Numero_Telefonico": telefono,
1573
"Monto": float(PRECIO_BOLETO),
1574
"Estado_Reserva": "PENDIENTE",
1575
"Fecha_Creacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
1576
"Expira_En": (datetime.now() + timedelta(minutes=TIEMPO_RESERVA_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S"),
1577
"Fecha_Actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
1578
}
1579
)
1580
 
1581
exito, msg = registrar_reserva_cobro(conn, ordenes)
1582
 
1583
if exito:
1584
errores = []
1585
pref_id = ""
1586
init_point = ""
1587
 
1588
try:
1589
pref_id, init_point = crear_preferencia_mercado_pago(
1590
nombre,
1591
apellidos,
1592
correo,
1593
telefono,
1594
boletos,
1595
PRECIO_BOLETO,
1596
ref,
1597
)
1598
except Exception as e:
1599
errores.append(f"Mercado Pago: {e}")
1600
 
1601
stripe_session_id = ""
1602
stripe_checkout_url = ""
1603
 
1604
try:
1605
stripe_session_id, stripe_checkout_url = crear_sesion_stripe(
1606
nombre,
1607
apellidos,
1608
correo,
1609
boletos,
1610
PRECIO_BOLETO,
1611
ref,
1612
)
1613
except Exception as e:
1614
errores.append(f"Stripe: {e}")
1615
 
1616
st.session_state.pago_generado_url = init_point
1617
st.session_state.stripe_session_id = stripe_session_id
1618
st.session_state.stripe_pago_url = stripe_checkout_url
1619
 
1620
actualizar_ids_proveedores_reserva(
1621
conn,
1622
ref,
1623
pref_id,
1624
stripe_session_id,
1625
)
1626
 
1627
if not init_point and not stripe_checkout_url:
1628
liberar_reserva_por_rechazo_o_cancelacion(
1629
conn,
1630
ref,
1631
"ERROR_GENERACION_PAGO",
1632
)
1633
limpiar_carrito_local()
1634
st.error("No fue posible generar enlaces de pago. " + " | ".join(errores))
1635
else:
1636
if errores:
1637
st.warning("Uno de los proveedores no estuvo disponible: " + " | ".join(errores))
1638
st.rerun()
1639
else:
1640
st.error(f"Error al registrar la reserva: {msg}")
1641
 
1642
 
1643
if __name__ == "__main__":
1644
main()
