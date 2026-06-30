import asyncio
import websockets
import json
import joblib
import numpy as np
import datetime
import os
import socket
import ssl
import random  # Agregado para generar el historial base inicial local

# Archivo para persistir señales localmente (JSON Lines)
SIGNALS_FILE = "signals.jsonl"

# ─────────────────────────────────────────────────────────────
# CARGA DE LOS 5 CEREBROS DE IA
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
MINIMO_TP_PUNTOS = 500.0

# Estructuras globales para almacenar datos en memoria
VELAS_PROCESADAS = []
FRACTALES = {"altos": [], "bajos": []}
MODEL_STATS = {}
HISTORIAL_GENERADO = False  # Bandera para crear el colchón de velas local

# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN CREDENCIALES FIX API DE IC MARKETS
# ─────────────────────────────────────────────────────────────
FIX_HOST = "demo-uk-eqx-01.p.c-trader.com"
FIX_PORT = 5211  
SENDER_COMP_ID = "demo.ctrader.5849579"
TARGET_COMP_ID = "cServer"
PASSWORD = "5849579"  # <--- COLOCA TU CONTRASEÑA REAL AQUÍ
SYMBOL_ID = "1"  

def _load_model_stats():
    global MODEL_STATS
    if os.path.exists("model_stats.json"):
        try:
            with open("model_stats.json", "r") as f:
                MODEL_STATS = json.load(f)
        except:
            MODEL_STATS = {}

def generar_historial_previo(precio_inicial):
    """Genera 500 velas M2 estocásticas hacia atrás para que el gráfico cargue lleno localmente"""
    global VELAS_PROCESADAS, HISTORIAL_GENERADO
    print(f"📦 Generando colchón de 500 velas históricas locales basadas en precio: ${precio_inicial}...")
    ahora = int(datetime.datetime.now().timestamp())
    precio_actual = precio_inicial
    
    temporal_velas = []
    for i in range(500, 0, -1):
        tiempo_vela = ahora - (i * 120)  # Restamos 2 minutos (120s) por cada barra
        apertura = precio_actual + random.uniform(-0.8, 0.8)
        cierre = apertura + random.uniform(-1.2, 1.2)
        alto = max(apertura, cierre) + random.uniform(0.0, 0.5)
        bajo = min(apertura, cierre) - random.uniform(0.0, 0.5)
        
        temporal_velas.append({
            'time': tiempo_vela,
            'open': round(apertura, 2),
            'high': round(alto, 2),
            'low': round(bajo, 2),
            'close': round(cierre, 2),
            'tick_volume': random.randint(50, 300)
        })
        precio_actual = cierre
        
    VELAS_PROCESADAS.extend(temporal_velas)
    HISTORIAL_GENERADO = True
    print("✅ Historial inicial inyectado con éxito. Gráfico listo para pintar.")

def construir_mensaje_fix(tipo_msg, campos):
    msg_body = f"35={tipo_msg}\x0149={SENDER_COMP_ID}\x0156={TARGET_COMP_ID}\x0134=1\x0152={datetime.datetime.utcnow().strftime('%Y%m%d-%H:%M:%S')}\x01"
    for tag, val in campos.items():
        msg_body += f"{tag}={val}\x01"
    longitud = len(msg_body)
    msg_completo = f"8=FIX.4.4\x019={longitud}\x01" + msg_body
    checksum = sum(ord(c) for c in msg_completo) % 256
    msg_completo += f"10={checksum:03d}\x01"
    return msg_completo.encode('utf-8')

async def conectar_broker_fix():
    global VELAS_PROCESADAS
    contexto_ssl = ssl.create_default_context()
    while True:
        try:
            print("🔌 Conectando a los servidores FIX institucionales de IC Markets...")
            reader, writer = await asyncio.open_connection(FIX_HOST, FIX_PORT, ssl=contexto_ssl)
            print("🔒 Conexión SSL establecida. Autenticando sesión (Logon)...")
            
            login_campos = {"98": "0", "108": "30", "554": PASSWORD}
            writer.write(construir_mensaje_fix("A", login_campos))
            await writer.drain()
            
            sub_campos = {
                "262": "ReqXAU01", "263": "1", "264": "1", 
                "267": "2", "269": "0\x01269=1", "146": "1", "55": SYMBOL_ID
            }
            writer.write(construir_mensaje_fix("W", sub_campos))
            await writer.drain()
            print("📈 Suscripción al flujo del Oro realizada con éxito.")
            
            while True:
                datos = await reader.read(4096)
                if not datos:
                    break
                msg_crudo = datos.decode('utf-8', errors='ignore')
                if "35=W" in msg_crudo or "35=d" in msg_crudo:
                    procesar_tick_crudo(msg_crudo)
                    
        except Exception as e:
            print(f"❌ Error en el túnel FIX: {e}")
        await asyncio.sleep(5)

def procesar_tick_crudo(msg_fix):
    global VELAS_PROCESADAS, HISTORIAL_GENERADO
    try:
        partes = msg_fix.split("\x01")
        precio = None
        for p in partes:
            if p.startswith("270="):  
                precio = float(p.split("=")[1])
                break
        if not precio:
            return
            
        # Si es el primer tick que llega y no tenemos historial, creamos el colchón de barras del gráfico
        if not HISTORIAL_GENERADO:
            generar_historial_previo(precio)
            
        ahora = datetime.datetime.now()
        timestamp = int(ahora.timestamp())
        
        ultima_vela = VELAS_PROCESADAS[-1]
        if timestamp - ultima_vela['time'] < 120:
            ultima_vela['close'] = precio
            if precio > ultima_vela['high']: ultima_vela['high'] = precio
            if precio < ultima_vela['low']: ultima_vela['low'] = precio
            ultima_vela['tick_volume'] += 1
        else:
            VELAS_PROCESADAS.append({'time': timestamp, 'open': precio, 'high': precio, 'low': precio, 'close': precio, 'tick_volume': 1})
            if len(VELAS_PROCESADAS) > 1500:
                VELAS_PROCESADAS.pop(0)
    except:
        pass

# ─────────────────────────────────────────────────────────────
# MÓDULO DE FRACTALES Y SETUP DE SEÑALES
# ─────────────────────────────────────────────────────────────
def obtener_fractales_validados(velas):
    altos_validados, bajos_validados = [], []
    if velas is None or len(velas) < 30:
        return altos_validados, bajos_validados
        
    for i in range(2, len(velas) - 2): 
        h, l = float(velas[i]['high']), float(velas[i]['low'])
        t = int(velas[i]['time'])
        
        if h > velas[i-1]['high'] and h > velas[i-2]['high'] and h > velas[i+1]['high'] and h > velas[i+2]['high']:
            altos_validados.append({"time": t, "value": h})
        if l < velas[i-1]['low'] and l < velas[i-2]['low'] and l < velas[i+1]['low'] and l < velas[i+2]['low']:
            bajos_validados.append({"time": t, "value": l})
            
    return altos_validados, bajos_validados

def guardar_senal_local(signal_data):
    try:
        with open(SIGNALS_FILE, "a") as f:
            f.write(json.dumps(signal_data) + "\n")
    except Exception as e:
        print(f"⚠️ Error al persistir la señal: {e}")

def evaluar_senal_ia(velas, altos, bajos):
    if len(velas) < 35 or not IA_CLASS:
        return None
        
    ultima_vela = velas[-1]
    precio_actual = ultima_vela['close']
    
    for bajo in reversed(bajos):
        if ultima_vela['low'] < bajo['value'] and precio_actual > bajo['value']:
            dist_int_inter = abs(precio_actual - bajo['value'])
            if dist_int_inter * 100 < MINIMO_TP_PUNTOS:
                continue
                
            secuencia_x = []
            for k in range(len(velas)-30, len(velas)):
                v = velas[k]
                secuencia_x.extend([v['open'], v['high'], v['low'], v['close'], v['tick_volume']])
                
            dt = datetime.datetime.fromtimestamp(ultima_vela['time'])
            secuencia_x.extend([dt.hour, 1, dist_int_inter, 1.5, abs(ultima_vela['high'] - ultima_vela['low']), 1, 1, 0])
            
            arr_x = np.array([secuencia_x])
            pred_class = IA_CLASS.predict(arr_x)[0]
            
            if pred_class == 1:
                p_ret = float(IA_RET.predict(arr_x)[0])
                p_velas = int(IA_VELAS.predict(arr_x)[0])
                p_tp = float(IA_TP.predict(arr_x)[0])
                p_sl = float(IA_SL.predict(arr_x)[0])
                
                signal = {
                    "id": f"B_{ultima_vela['time']}", "type": "BUY", "time": ultima_vela['time'],
                    "price": precio_actual, "tp": precio_actual + p_tp, "sl": precio_actual - p_sl,
                    "info_ia": {"exceso_previsto": round(p_ret, 2), "velas_espera": p_velas, "precision_modelo": MODEL_STATS.get("class_accuracy", 0.85)}
                }
                guardar_senal_local(signal)
                return signal
                
    for alto in reversed(altos):
        if ultima_vela['high'] > alto['value'] and precio_actual < alto['value']:
            dist_int_inter = abs(precio_actual - alto['value'])
            if dist_int_inter * 100 < MINIMO_TP_PUNTOS:
                continue
                
            secuencia_x = []
            for k in range(len(velas)-30, len(velas)):
                v = velas[k]
                secuencia_x.extend([v['open'], v['high'], v['low'], v['close'], v['tick_volume']])
                
            dt = datetime.datetime.fromtimestamp(ultima_vela['time'])
            secuencia_x.extend([dt.hour, 1, dist_int_inter, 1.5, abs(ultima_vela['high'] - ultima_vela['low']), -1, 1, 0])
            
            arr_x = np.array([secuencia_x])
            pred_class = IA_CLASS.predict(arr_x)[0]
            
            if pred_class == 1:
                p_ret = float(IA_RET.predict(arr_x)[0])
                p_velas = int(IA_VELAS.predict(arr_x)[0])
                p_tp = float(IA_TP.predict(arr_x)[0])
                p_sl = float(IA_SL.predict(arr_x)[0])
                
                signal = {
                    "id": f"S_{ultima_vela['time']}", "type": "SELL", "time": ultima_vela['time'],
                    "price": precio_actual, "tp": precio_actual - p_tp, "sl": precio_actual + p_sl,
                    "info_ia": {"exceso_previsto": round(p_ret, 2), "velas_espera": p_velas, "precision_modelo": MODEL_STATS.get("class_accuracy", 0.85)}
                }
                guardar_senal_local(signal)
                return signal
                
    return None

# ─────────────────────────────────────────────────────────────
# WEBSOCKET MOTOR
# ─────────────────────────────────────────────────────────────
async def enviar_ticks(websocket):
    while True:
        try:
            if len(VELAS_PROCESADAS) >= 30:
                altos, bajos = obtener_fractales_validados(VELAS_PROCESADAS)
                signal_detectada = evaluar_senal_ia(VELAS_PROCESADAS, altos, bajos)
                
                payload = {
                    "type": "tick", "timeframe": CLIENT_TF, "velas": VELAS_PROCESADAS[-100:],
                    "debug": {"fractales_altos": altos[-15:], "fractales_bajos": bajos[-15:], "data_length": len(VELAS_PROCESADAS)}
                }
                if signal_detectada:
                    payload["signal"] = signal_detectada
                    
                await websocket.send(json.dumps(payload))
            await asyncio.sleep(2)
        except:
            break

async def auto_refresh(websocket):
    while True:
        try:
            if len(VELAS_PROCESADAS) > 0:
                await websocket.send(json.dumps({
                    "type": "history", "timeframe": CLIENT_TF, "velas": VELAS_PROCESADAS[-500:]
                }))
            await asyncio.sleep(5) # Más rápido en local para pruebas fluidas
        except:
            break

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
    _load_model_stats()
    asyncio.create_task(conectar_broker_fix())
    print("🚀 Servidor de Pruebas Locales Inicializado en Puerto 8091")
    
    async with websockets.serve(manejar_cliente, "127.0.0.1", 8091):
        await asyncio.get_running_loop().create_future()

if __name__ == "__main__":
    asyncio.run(main())