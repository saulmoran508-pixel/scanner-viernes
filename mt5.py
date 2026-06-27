import MetaTrader5 as mt5
import asyncio
import websockets
import json
import joblib
import numpy as np
import datetime
import os

# Archivo para persistir señales localmente (JSON Lines)
SIGNALS_FILE = "signals.jsonl"

# ─────────────────────────────────────────────────────────────
# CARGA DE LOS 5 CEREBROS DE IA (ACTUALIZADO)
# ─────────────────────────────────────────────────────────────
try:
    IA_CLASS = joblib.load("bot_model_class.pkl")
    IA_RET = joblib.load("bot_model_retroceso.pkl")
    IA_VELAS = joblib.load("bot_model_velas.pkl")
    IA_TP = joblib.load("bot_model_tp.pkl")
    IA_SL = joblib.load("bot_model_sl.pkl")
    print("🧠 Los 5 Cerebros de IA Cargados Correctamente para el Escáner Avanzado")
except Exception as e:
    print(f"⚠️ Faltan modelos de IA o hubo un error al cargar. Error: {e}")
    IA_CLASS, IA_RET, IA_VELAS, IA_TP, IA_SL = None, None, None, None, None

SYMBOL = "XAUUSD"
CLIENT_TF = "2m"

TIMEFRAMES = {
    "1m": mt5.TIMEFRAME_M1,  "2m": mt5.TIMEFRAME_M2,  "3m": mt5.TIMEFRAME_M3,
    "5m": mt5.TIMEFRAME_M5,  "10m": mt5.TIMEFRAME_M10, "15m": mt5.TIMEFRAME_M15,
    "30m": mt5.TIMEFRAME_M30,"1h": mt5.TIMEFRAME_H1,   "2h": mt5.TIMEFRAME_H2,
    "4h": mt5.TIMEFRAME_H4,  "8h": mt5.TIMEFRAME_H8,   "1d": mt5.TIMEFRAME_D1
}

HISTORY_COUNTS = {
    "1m": 7200,
    "2m": 5400,
    "3m": 4800,
    "5m": 2880,
    "10m": 1440,
    "15m": 1000,
    "30m": 720,
    "1h": 500,
    "2h": 360,
    "4h": 250,
    "8h": 120,
    "1d": 365
}

OFFSET_ENTRADA = 0.20
MINIMO_TP_PUNTOS = 1000.0
MAX_LOSS_FACTOR = 0.5
MAX_BARS_ALIVE = 6
IA_PROB_THRESHOLD = 55.0

TIMEFRAME_SECONDS = {
    "1m": 60,  "2m": 120, "3m": 180, "5m": 300,
    "10m": 600, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400,
    "8h": 28800, "1d": 86400
}

FRACTALES = {"altos": [], "bajos": []}
SIGNAL_HISTORY = []
SIGNAL_COUNTER = 0
MODEL_STATS_FILE = "model_stats.json"
MODEL_ACCURACY = None


# Cargar historial previo desde disco (si existe)
def _load_signal_history_from_disk():
    global SIGNAL_HISTORY, SIGNAL_COUNTER
    if not os.path.exists(SIGNALS_FILE):
        return
    try:
        with open(SIGNALS_FILE, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
        # Mantener solo las últimas 500 señales en memoria
        loaded = [json.loads(l) for l in lines if l.strip()]
        SIGNAL_HISTORY = loaded[-500:][::-1]  # invertimos para tener la más reciente primero
        if SIGNAL_HISTORY:
            # Recuperar contador a partir del último id
            try:
                last_id = SIGNAL_HISTORY[0].get('id', '')
                if last_id.startswith('signal_'):
                    SIGNAL_COUNTER = int(last_id.split('_')[1])
            except Exception:
                SIGNAL_COUNTER = len(SIGNAL_HISTORY)
    except Exception as e:
        print(f"⚠️ Error cargando historial de señales: {e}")


def _load_model_stats():
    global MODEL_ACCURACY
    if not os.path.exists(MODEL_STATS_FILE):
        return
    try:
        with open(MODEL_STATS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            accuracy_pct = data.get('accuracy_pct')
            if accuracy_pct is not None:
                MODEL_ACCURACY = f"{float(accuracy_pct):.1f}%"
    except Exception as e:
        print(f"⚠️ No se pudo cargar accuracy de IA: {e}")


# Guardar una señal en disco (append JSONL)
def _append_signal_to_disk(registro):
    try:
        with open(SIGNALS_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"⚠️ Error escribiendo señal en disco: {e}")

ESTADO = {
    "en_curso":             False,
    "tipo":                 None,
    "precio_entrada":       0.0,
    "precio_objetivo":      0.0,
    "precio_stoploss":      0.0,
    "liquidez_barrida":     "",
    "nivel_barrido_precio": 0.0,
    "motivo":               "",
    "time_entrada":         0,
    "puntos_proy":          0,
    "ia_prob":              "N/A",
    "retroceso_esperado":   "N/A",
    "velas_esperar":        0
}

# ═════════════════════════════════════════════════════════════
# BLOQUE 1 — FRACTALES HORIZONTALES (SEGUNDO PLANO)
# ═════════════════════════════════════════════════════════════

def actualizar_fractales(velas):
    global FRACTALES
    altos, bajos = [], []
    n = len(velas)
    if n < 15:
        return

    for i in range(3, n - 3):
        h, l, t = float(velas[i]['high']), float(velas[i]['low']), int(velas[i]['time'])

        if (h > float(velas[i-1]['high']) and h > float(velas[i-2]['high']) and
            h > float(velas[i+1]['high']) and h > float(velas[i+2]['high'])):
            altos.append({"time": t, "price": h})

        if (l < float(velas[i-1]['low']) and l < float(velas[i-2]['low']) and
            l < float(velas[i+1]['low']) and l < float(velas[i+2]['low'])):
            bajos.append({"time": t, "price": l})

    FRACTALES["altos"] = altos[-15:]
    FRACTALES["bajos"] = bajos[-15:]

# ═════════════════════════════════════════════════════════════
# BLOQUE 2 — LÓGICA DE ENTRADAS Y TARGET CON MÍNIMO ESTRICTO
# ═════════════════════════════════════════════════════════════

def evaluar_puntos_operativos(velas):
    global ESTADO

    if len(velas) < 15:
        return None, [], [], []

    actualizar_fractales(velas)

    if ESTADO["en_curso"]:
        vela_live = velas[-1]
        high = float(vela_live['high'])
        low = float(vela_live['low'])

        if ESTADO["tipo"] == "COMPRA":
            if high >= ESTADO["precio_objetivo"]:
                _cerrar_trade(True)
            elif low <= ESTADO["precio_stoploss"]:
                _cerrar_trade(False)
        elif ESTADO["tipo"] == "VENTA":
            if low <= ESTADO["precio_objetivo"]:
                _cerrar_trade(True)
            elif high >= ESTADO["precio_stoploss"]:
                _cerrar_trade(False)

        # ⛔ TEMPORIZADOR DESACTIVADO: Se comenta este bloque para que la operación 
        # jamás se cierre sola por "vieja", sino únicamente cuando toque TP o SL.
        # current_bar = velas[-1]
        # if ESTADO["en_curso"] and current_bar and ESTADO["time_entrada"]:
        #     edad_op = int(current_bar['time']) - int(ESTADO["time_entrada"])
        #     segundos_tiempo = TIMEFRAME_SECONDS.get(CLIENT_TF, 120)
        #     if edad_op >= MAX_BARS_ALIVE * segundos_tiempo:
        #         _cerrar_trade(False)

    lineas, bloques, tendencias = [], [], []
    alerta = None

    if ESTADO["en_curso"]:
        _agregar_lineas_activas(lineas, bloques)
        alerta = {"msg": f"🔄 Operación activa ({ESTADO['tipo']}) → TP ${ESTADO['precio_objetivo']:.2f} | SL ${ESTADO['precio_stoploss']:.2f}"}
        return alerta, lineas, bloques, tendencias

    v = velas[-2]
    time_vela = int(v['time'])
    o, h, l, c = float(v['open']), float(v['high']), float(v['low']), float(v['close'])

    total_size = h - l
    if total_size <= 0:
        return None, lineas, bloques, tendencias

    upper_wick, lower_wick = h - max(o, c), min(o, c) - l

    hay_indicio_venta = (c < o) and (upper_wick >= (total_size * 0.40))
    hay_indicio_compra = (c > o) and (lower_wick >= (total_size * 0.40))

    altos, bajos = FRACTALES["altos"], FRACTALES["bajos"]

    if len(bajos) >= 3 and not ESTADO["en_curso"]:
        seq_bajos = [bajos[-1]]
        for i in range(len(bajos)-2, -1, -1):
            if bajos[i]['price'] < seq_bajos[-1]['price']:
                seq_bajos.append(bajos[i])
            else:
                break

        if len(seq_bajos) >= 3:
            seq_bajos.reverse()
            l_first, l_last = seq_bajos[0], seq_bajos[-1]
            tendencias.append({"p1": l_first, "p2": l_last, "color": "#00e676", "tipo": "TL Alcista"})

            if l < l_last['price'] and c > l_last['price'] and hay_indicio_compra:
                _activar_trade("COMPRA", l_last['price'], f"TL Soporte (${l_last['price']:.2f})", f"Trampa TL ({len(seq_bajos)} toques)", time_vela)

    if len(altos) >= 3 and not ESTADO["en_curso"]:
        seq_altos = [altos[-1]]
        for i in range(len(altos)-2, -1, -1):
            if altos[i]['price'] > seq_altos[-1]['price']:
                seq_altos.append(altos[i])
            else:
                break

        if len(seq_altos) >= 3:
            seq_altos.reverse()
            h_first, h_last = seq_altos[0], seq_altos[-1]
            tendencias.append({"p1": h_first, "p2": h_last, "color": "#ff1744", "tipo": "TL Bajista"})

            if h > h_last['price'] and c < h_last['price'] and hay_indicio_venta:
                _activar_trade("VENTA", h_last['price'], f"TL Resistencia (${h_last['price']:.2f})", f"Trampa TL ({len(seq_altos)} toques)", time_vela)

    if not ESTADO["en_curso"] and hay_indicio_venta:
        for f_alto in sorted(altos, key=lambda x: x['price'], reverse=True):
            alto_precio = f_alto['price']
            if h > alto_precio and c < alto_precio:
                _activar_trade("VENTA", alto_precio, f"Alto Horizontal (${alto_precio:.2f})", "Trampa Horizontal VENTA", time_vela)
                break

    if not ESTADO["en_curso"] and hay_indicio_compra:
        for f_bajo in sorted(bajos, key=lambda x: x['price']):
            bajo_precio = f_bajo['price']
            if l < bajo_precio and c > bajo_precio:
                _activar_trade("COMPRA", bajo_precio, f"Bajo Horizontal (${bajo_precio:.2f})", "Trampa Horizontal COMPRA", time_vela)
                break

    if ESTADO["en_curso"]:
        _agregar_lineas_activas(lineas, bloques)
        dir_emoji = "📉" if ESTADO["tipo"] == "VENTA" else "📈"
        alerta = {"msg": f"🚨 {ESTADO['motivo'].upper()} {dir_emoji} Nivel: ${ESTADO['nivel_barrido_precio']:.2f} → {ESTADO['tipo']} en ${ESTADO['precio_entrada']:.2f}"}

    return alerta, lineas, bloques, tendencias


def _activar_trade(tipo, precio_barrido, liquidez_txt, motivo, time_vela):
    global ESTADO
    entrada_base = precio_barrido - OFFSET_ENTRADA if tipo == "VENTA" else precio_barrido + OFFSET_ENTRADA
    distancia_minima_precio = MINIMO_TP_PUNTOS / 100.0

    if tipo == "VENTA":
        bajos = [f['price'] for f in FRACTALES["bajos"]]
        if bajos:
            ultimo_bajo = bajos[-1]
            impulso = precio_barrido - ultimo_bajo
            if impulso > 0:
                objetivo_base = precio_barrido - (impulso * 0.75)
            else:
                objetivo_base = entrada_base - distancia_minima_precio
        else:
            objetivo_base = entrada_base - distancia_minima_precio

        if (entrada_base - objetivo_base) < distancia_minima_precio:
            objetivo_base = entrada_base - distancia_minima_precio
    else:
        altos = [f['price'] for f in FRACTALES["altos"]]
        if altos:
            ultimo_alto = altos[-1]
            impulso = ultimo_alto - precio_barrido
            if impulso > 0:
                objetivo_base = precio_barrido + (impulso * 0.75)
            else:
                objetivo_base = entrada_base + distancia_minima_precio
        else:
            objetivo_base = entrada_base + distancia_minima_precio

        if (objetivo_base - entrada_base) < distancia_minima_precio:
            objetivo_base = entrada_base + distancia_minima_precio

    # 🚀 LÓGICA EXTENDIDA CON LOS 5 MODELOS DE IA SIMULTÁNEOS
    if IA_CLASS is not None and IA_SL is not None:
        datos_ia = construir_feature_vector(tipo, precio_barrido, entrada_base, objetivo_base)
        if datos_ia is not None:
            try:
                prob = float(IA_CLASS.predict_proba(datos_ia)[0][1]) * 100
                
                # Si la probabilidad no pasa el filtro básico de confianza, se rechaza
                if prob < IA_PROB_THRESHOLD:
                    sl_distancia = abs(entrada_base - objetivo_base) * MAX_LOSS_FACTOR
                    precio_stoploss_def = round(entrada_base + sl_distancia, 2) if tipo == "VENTA" else round(entrada_base - sl_distancia, 2)
                    _registrar_signal("rechazo", {
                        "tipo": tipo,
                        "entrada": round(entrada_base, 2),
                        "target": round(objetivo_base, 2),
                        "stoploss": precio_stoploss_def,
                        "motivo": motivo,
                        "puntos": round(abs(entrada_base - objetivo_base) * 100, 1),
                        "probabilidad": f"{prob:.1f}%",
                        "resultado": "rechazada",
                        "color": "#ff1744"
                    })
                    return

                # Si es aceptada, extraemos las predicciones francotirador de los otros 4 modelos
                p_ret = float(IA_RET.predict(datos_ia)[0])
                p_velas = int(round(float(IA_VELAS.predict(datos_ia)[0])))
                p_tp = float(IA_TP.predict(datos_ia)[0])
                p_sl = float(IA_SL.predict(datos_ia)[0])

                # Ajustamos los puntos operativos según lo que la IA predice que se va a estirar el precio
                entrada = (entrada_base + p_ret) if tipo == "VENTA" else (entrada_base - p_ret)
                precio_stoploss = (entrada + p_sl) if tipo == "VENTA" else (entrada - p_sl)
                objetivo = (entrada - p_tp) if tipo == "VENTA" else (entrada + p_tp)
                
                prob_str = f"{prob:.1f}%"
                retroceso_str = f"{p_ret:.2f} USD"
                velas_val = p_velas
            except Exception as e:
                print(f"⚠️ Error prediciendo con IA extendida: {e}")
                prob_str, retroceso_str, velas_val = "N/A", "N/A", 0
                entrada, objetivo = entrada_base, objetivo_base
                sl_distancia = abs(entrada - objetivo) * MAX_LOSS_FACTOR
                precio_stoploss = round(entrada + sl_distancia, 2) if tipo == "VENTA" else round(entrada - sl_distancia, 2)
        else:
            prob_str, retroceso_str, velas_val = "N/A", "N/A", 0
            entrada, objetivo = entrada_base, objetivo_base
            sl_distancia = abs(entrada - objetivo) * MAX_LOSS_FACTOR
            precio_stoploss = round(entrada + sl_distancia, 2) if tipo == "VENTA" else round(entrada - sl_distancia, 2)
    else:
        prob_str, retroceso_str, velas_val = "N/A", "N/A", 0
        entrada, objetivo = entrada_base, objetivo_base
        sl_distancia = abs(entrada - objetivo) * MAX_LOSS_FACTOR
        precio_stoploss = round(entrada + sl_distancia, 2) if tipo == "VENTA" else round(entrada - sl_distancia, 2)

    puntos_distancia = abs(entrada - objetivo) * 100

    ESTADO = {
        "en_curso":             True,
        "tipo":                 tipo,
        "precio_entrada":       round(entrada, 2),
        "precio_objetivo":      round(objetivo, 2),
        "precio_stoploss":      round(precio_stoploss, 2),
        "liquidez_barrida":     liquidez_txt,
        "nivel_barrido_precio": round(precio_barrido, 2),
        "motivo":               motivo,
        "time_entrada":         time_vela,
        "puntos_proy":          round(puntos_distancia, 1),
        "ia_prob":              prob_str,
        "retroceso_esperado":   retroceso_str,
        "velas_esperar":        velas_val
    }

    _registrar_signal("entrada", {
        "tipo": tipo,
        "entrada": round(entrada, 2),
        "target": round(objetivo, 2),
        "stoploss": round(precio_stoploss, 2),
        "motivo": motivo,
        "puntos": round(puntos_distancia, 1),
        "probabilidad": prob_str,
        "retroceso_esperado": retroceso_str,
        "velas_esperar": velas_val,
        "resultado": "abierta",
        "color": "#00e676"
    })


def _agregar_lineas_activas(lineas, bloques):
    lineas.append({"precio": ESTADO["nivel_barrido_precio"], "label": f"❌ BARRIDO {ESTADO['liquidez_barrida']}"})
    lineas.append({"precio": ESTADO["precio_entrada"], "label": f"🔹 ENTRADA IA ${ESTADO['precio_entrada']:.2f}"})
    lineas.append({"precio": ESTADO["precio_objetivo"], "label": f"🎯 TP IA ${ESTADO['precio_objetivo']:.2f}"})
    lineas.append({"precio": ESTADO["precio_stoploss"], "label": f"⛔ STOP IA ${ESTADO['precio_stoploss']:.2f}"})
    bloques.append({
        "id":        "trade_activo",
        "tipo":      ESTADO["tipo"],
        "eliminado": ESTADO["liquidez_barrida"],
        "entrada":   ESTADO["precio_entrada"],
        "target":    ESTADO["precio_objetivo"],
        "stoploss":  ESTADO["precio_stoploss"],
        "puntos":    ESTADO.get("puntos_proy", 0),
        "motivo":    ESTADO.get("motivo", "")
    })


def evaluar_viabilidad_trade(tipo, precio_barrido, precio_entrada, precio_objetivo):
    if IA_CLASS is None:
        return {"prob": "N/A", "aceptada": True}

    datos = construir_feature_vector(tipo, precio_barrido, precio_entrada, precio_objetivo)
    if datos is None:
        return {"prob": "Cargando...", "aceptada": True}

    try:
        prob = float(IA_CLASS.predict_proba(datos)[0][1]) * 100
        aceptada = prob >= IA_PROB_THRESHOLD
        return {"prob": f"{prob:.1f}%", "aceptada": aceptada}
    except Exception:
        return {"prob": "N/A", "aceptada": True}


def construir_feature_vector(tipo, precio_barrido, precio_entrada, precio_objetivo):
    velas = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M2, 0, 30)
    if velas is None or len(velas) < 30:
        return None

    datos_secuencia = []
    for v in velas:
        datos_secuencia.extend([v['open'], v['high'], v['low'], v['close'], v['tick_volume']])

    v_actual = velas[-1]
    t_actual = int(v_actual['time'])
    dt = datetime.datetime.fromtimestamp(t_actual)
    hora = dt.hour
    sesion = 1 if 0 <= hora < 8 else (2 if 8 <= hora < 13 else 3)

    total_size = v_actual['high'] - v_actual['low']
    fuerza_desplazamiento = abs(v_actual['close'] - v_actual['open']) / total_size if total_size > 0 else 0
    direccion_num = 1 if tipo == "COMPRA" else -1
    tamano_barrido = abs(precio_entrada - precio_barrido)
    dist_int_inter = abs(precio_entrada - precio_objetivo)

    datos_secuencia.extend([
        hora,
        sesion,
        dist_int_inter,
        fuerza_desplazamiento,
        tamano_barrido,
        direccion_num,
        1,
        0
    ])

    return np.array(datos_secuencia).reshape(1, -1)


def _registrar_signal(event_type, data):
    global SIGNAL_COUNTER
    SIGNAL_COUNTER += 1
    registro = {
        "id": f"signal_{SIGNAL_COUNTER}",
        "time": datetime.datetime.utcnow().isoformat() + "Z",
        "event": event_type,
        **data
    }
    SIGNAL_HISTORY.insert(0, registro)
    # Mantener en memoria un tamaño razonable
    if len(SIGNAL_HISTORY) > 500:
        SIGNAL_HISTORY.pop()

    # Persistir en disco (append)
    _append_signal_to_disk(registro)


def _cerrar_trade(resultado):
    global ESTADO
    if not ESTADO["en_curso"]:
        return

    registro = {
        "tipo": ESTADO["tipo"],
        "entrada": ESTADO["precio_entrada"],
        "target": ESTADO["precio_objetivo"],
        "stoploss": ESTADO["precio_stoploss"],
        "motivo": ESTADO["motivo"],
        "puntos": ESTADO["puntos_proy"],
        "resultado": "ganador" if resultado else "perdedor",
        "color": "#00e676" if resultado else "#ff1744",
        "probabilidad": ESTADO.get("ia_prob", "N/A"),
        "retroceso_esperado": ESTADO.get("retroceso_esperado", "N/A"),
        "velas_esperar": ESTADO.get("velas_esperar", 0)
    }
    _registrar_signal("cierre", registro)
    ESTADO["en_curso"] = False
    ESTADO["tipo"] = None
    ESTADO["precio_entrada"] = 0.0
    ESTADO["precio_objetivo"] = 0.0
    ESTADO["precio_stoploss"] = 0.0
    ESTADO["liquidez_barrida"] = ""
    ESTADO["nivel_barrido_precio"] = 0.0
    ESTADO["motivo"] = ""
    ESTADO["time_entrada"] = 0
    ESTADO["puntos_proy"] = 0
    ESTADO["ia_prob"] = "N/A"
    ESTADO["retroceso_esperado"] = "N/A"
    ESTADO["velas_esperar"] = 0


def calcular_probabilidad_ia(simbolo, estado_actual):
    """Calcula éxito, TP sugerido, retroceso esperado y SL sugerido usando los 5 cerebros de IA en tiempo real"""
    if IA_CLASS is None or not estado_actual["en_curso"]:
        return {"prob": "N/A", "tp_sugerido": "N/A", "retroceso": "N/A", "velas": 0, "sl_sugerido": "N/A"}

    velas = mt5.copy_rates_from_pos(simbolo, mt5.TIMEFRAME_M2, 0, 30)
    if velas is None or len(velas) < 30:
        return {"prob": "Cargando...", "tp_sugerido": "Cargando...", "retroceso": "Cargando...", "velas": 0, "sl_sugerido": "Cargando..."}

    datos_secuencia = []
    for v in velas:
        datos_secuencia.extend([v['open'], v['high'], v['low'], v['close'], v['tick_volume']])

    v_actual = velas[-1]
    t_actual = int(v_actual['time'])
    dt = datetime.datetime.fromtimestamp(t_actual)
    hora = dt.hour
    sesion = 1 if 0 <= hora < 8 else (2 if 8 <= hora < 13 else 3)

    total_size = v_actual['high'] - v_actual['low']
    fuerza_desplazamiento = abs(v_actual['close'] - v_actual['open']) / total_size if total_size > 0 else 0
    direccion_num = 1 if estado_actual["tipo"] == "COMPRA" else -1

    tamano_barrido = abs(estado_actual["precio_entrada"] - estado_actual["nivel_barrido_precio"])
    dist_int_inter = abs(estado_actual["precio_entrada"] - estado_actual["precio_objetivo"])

    datos_secuencia.extend([
        hora,
        sesion,
        dist_int_inter,
        fuerza_desplazamiento,
        tamano_barrido,
        direccion_num,
        1,
        0
    ])

    datos_ia = np.array(datos_secuencia).reshape(1, -1)
    
    probabilidad = float(IA_CLASS.predict_proba(datos_ia)[0][1]) * 100
    p_ret = float(IA_RET.predict(datos_ia)[0])
    p_velas = int(round(float(IA_VELAS.predict(datos_ia)[0])))
    p_tp = float(IA_TP.predict(datos_ia)[0])
    p_sl = float(IA_SL.predict(datos_ia)[0])

    return {
        "prob": f"{probabilidad:.1f}%",
        "retroceso": f"{p_ret:.2f} USD",
        "velas": p_velas,
        "tp_sugerido": f"{p_tp:.2f} USD",
        "sl_sugerido": f"{p_sl:.2f} USD"
    }

# ═════════════════════════════════════════════════════════════
# BLOQUE 3 — WEBSOCKET SERVER
# ═════════════════════════════════════════════════════════════

async def enviar_ticks(ws):
    try:
        while True:
            tick = mt5.symbol_info_tick(SYMBOL)
            if tick is not None:
                precio = float(tick.last if tick.last != 0 else tick.bid)
                tick_point = {
                    "time": int(datetime.datetime.now().timestamp()),
                    "open": precio,
                    "high": precio,
                    "low": precio,
                    "close": precio
                }
                await ws.send(json.dumps({"action": "ticks", "data": [tick_point]}))
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass


async def auto_refresh(ws):
    global CLIENT_TF
    try:
        while True:
            count = HISTORY_COUNTS.get(CLIENT_TF, 600)
            try:
                velas = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAMES.get(CLIENT_TF, mt5.TIMEFRAME_M2), 0, count)
            except Exception as e:
                print(f"⚠️ Error al obtener velas de MT5: {e}")
                velas = None

            if velas is not None and len(velas) > 0:
                datos = [{"time": int(v['time']), "open": float(v['open']), "high": float(v['high']), "low": float(v['low']), "close": float(v['close'])} for v in velas]
                datos.sort(key=lambda x: x['time'])
                alerta, lineas, bloques, tendencias = evaluar_puntos_operativos(datos)

                marcador_obj = None
                if ESTADO["en_curso"] and ESTADO.get("time_entrada"):
                    marcador_obj = {"time": ESTADO["time_entrada"], "tipo": ESTADO["tipo"]}

                prob_ia_dict = calcular_probabilidad_ia(SYMBOL, ESTADO)
                prob_ia_value = prob_ia_dict.get("prob") if isinstance(prob_ia_dict, dict) else prob_ia_dict

                await ws.send(json.dumps({
                    "action": "refresh",
                    "data": datos,
                    "lineas_horizontales": lineas,
                    "bloques_ordenes": bloques,
                    "tendencias": tendencias,
                    "alerta": alerta,
                    "sesgo": ESTADO["tipo"] if ESTADO["en_curso"] else "BUSCANDO TRAMPAS...",
                    "marcador": [marcador_obj] if marcador_obj else [],
                    "ia_probabilidad": prob_ia_value,
                    "model_accuracy": MODEL_ACCURACY,
                    "signal_history": SIGNAL_HISTORY,
                    "debug": {
                        "fractales_altos": FRACTALES["altos"],
                        "fractales_bajos": FRACTALES["bajos"],
                        "data_length": len(datos)
                    }
                }))
            else:
                print(f"⚠️ No se recibieron velas desde MT5 para timeframe {CLIENT_TF}. count={count} len={len(velas) if velas is not None else 0}")
            await asyncio.sleep(2)
    except asyncio.CancelledError:
        pass


async def manejar_cliente(websocket):
    global CLIENT_TF
    t_ticks = asyncio.create_task(enviar_ticks(websocket))
    t_refresh = asyncio.create_task(auto_refresh(websocket))
    try:
        async for msg in websocket:
            req = json.loads(msg)
            if req.get("action") == "get_history":
                CLIENT_TF = req.get("timeframe", "2m")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        t_ticks.cancel()
        t_refresh.cancel()


async def main():
    if not mt5.initialize() or not mt5.symbol_select(SYMBOL, True):
        return
    _load_model_stats()
    print(f"🚀 Bot activo | {SYMBOL} | Filtro: Mínimo {MINIMO_TP_PUNTOS} pts de TP")
    async with websockets.serve(manejar_cliente, "localhost", 8091):
        await asyncio.get_running_loop().create_future()


if __name__ == "__main__":
    # Cargar historial de señales previo antes de arrancar
    _load_signal_history_from_disk()
    asyncio.run(main())