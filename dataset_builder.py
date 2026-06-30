
import MetaTrader5 as mt5
import numpy as np
import datetime

# Parámetros
SYMBOL = "XAUUSD"
TIMEFRAME_BASE = mt5.TIMEFRAME_M2
SEQ_LEN = 30
MINIMO_TP_PUNTOS = 500.0  # Bajé el mínimo a 500 puntos ($5 usd) para que encuentre más setups reales
VELAS_FUTURO = 150

def inicializar():
    if not mt5.initialize():
        print("❌ Error al conectar con MT5")
        return False
    return True

# ====================================================================
# BLOQUE 1: EXTRACCIÓN Y VALIDACIÓN DE ROMPIMIENTOS
# ====================================================================
def obtener_fractales_validados(velas):
    altos_crudos, bajos_crudos = [], []
    altos_validados, bajos_validados = [], []
    
    if velas is None or len(velas) < 30:
        return altos_validados, bajos_validados
        
    for i in range(2, len(velas) - 21): 
        h, l = float(velas[i]['high']), float(velas[i]['low'])
        t = int(velas[i]['time'])
        
        es_alto, es_bajo = False, False
        
        if h > velas[i-1]['high'] and h > velas[i-2]['high'] and h > velas[i+1]['high'] and h > velas[i+2]['high']:
            es_alto = True
            altos_crudos.append(h)
            
        if l < velas[i-1]['low'] and l < velas[i-2]['low'] and l < velas[i+1]['low'] and l < velas[i+2]['low']:
            es_bajo = True
            bajos_crudos.append(l)
        
        velas_impulso = velas[i + 1 : i + 21] 
        
        if es_bajo and len(altos_crudos) > 0:
            alto_a_romper = altos_crudos[-1] 
            for v in velas_impulso:
                if float(v['high']) > alto_a_romper:
                    bajos_validados.append({'time': t, 'price': l})
                    break 
                    
        if es_alto and len(bajos_crudos) > 0:
            bajo_a_romper = bajos_crudos[-1] 
            for v in velas_impulso:
                if float(v['low']) < bajo_a_romper:
                    altos_validados.append({'time': t, 'price': h})
                    break 
                    
    return altos_validados, bajos_validados

def obtener_niveles_liquidez(simbolo, cantidad_velas=50000):
    liquidez = {
        "interna": {"altos": [], "bajos": []},
        "intermedia": {"altos": [], "bajos": []},
        "externa": {"altos": [], "bajos": []}
    }
    
    velas_m5 = mt5.copy_rates_from_pos(simbolo, mt5.TIMEFRAME_M5, 0, cantidad_velas)
    altos_int, bajos_int = obtener_fractales_validados(velas_m5)
    liquidez["interna"]["altos"].extend(altos_int)
    liquidez["interna"]["bajos"].extend(bajos_int)

    velas_m15 = mt5.copy_rates_from_pos(simbolo, mt5.TIMEFRAME_M15, 0, cantidad_velas // 3)
    velas_h1 = mt5.copy_rates_from_pos(simbolo, mt5.TIMEFRAME_H1, 0, cantidad_velas // 12)
    
    altos_m15, bajos_m15 = obtener_fractales_validados(velas_m15)
    altos_h1, bajos_h1 = obtener_fractales_validados(velas_h1)
    
    liquidez["intermedia"]["altos"].extend(altos_m15 + altos_h1)
    liquidez["intermedia"]["bajos"].extend(bajos_m15 + bajos_h1)

    velas_h4 = mt5.copy_rates_from_pos(simbolo, mt5.TIMEFRAME_H4, 0, cantidad_velas // 48)
    altos_h4, bajos_h4 = obtener_fractales_validados(velas_h4)
    
    liquidez["externa"]["altos"].extend(altos_h1 + altos_h4)
    liquidez["externa"]["bajos"].extend(bajos_h1 + bajos_h4)
    
    print(f"   ➤ Interna (M5): {len(liquidez['interna']['altos'])} altos, {len(liquidez['interna']['bajos'])} bajos")
    print(f"   ➤ Intermedia (M15/H1): {len(liquidez['intermedia']['altos'])} altos, {len(liquidez['intermedia']['bajos'])} bajos")
    print(f"   ➤ Externa (H1/H4): {len(liquidez['externa']['altos'])} altos, {len(liquidez['externa']['bajos'])} bajos")
    
    return liquidez

# ====================================================================
# BLOQUE 2: MOTOR DE SIMULACIÓN Y ETIQUETADO AVANZADO
# ====================================================================
def construir_dataset(cantidad_velas=60000):
    print("🔍 Escaneando y validando rompimientos en MT5...")
    liquidez = obtener_niveles_liquidez(SYMBOL, cantidad_velas)
    
    if len(liquidez['interna']['altos']) == 0:
        print("\n⚠️ ALERTA: MT5 devolvió 0 niveles de liquidez.")
        return np.array([]), np.array([])
    
    print(f"📥 Descargando {cantidad_velas} velas de la base (M2)...")
    historico = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME_BASE, 0, cantidad_velas)
    
    if historico is None or len(historico) < 1000:
        return np.array([]), np.array([])

    X, y = [], []
    exitosos, fallidos = 0, 0

    print("🤖 Simulando mercado vela a vela extrayendo contexto avanzado (retrocesos, tiempos, fuerza)...")

    for i in range(SEQ_LEN, len(historico) - VELAS_FUTURO):
        vela_actual = historico[i]
        t_actual = int(vela_actual['time'])
        o, h, l, c = vela_actual['open'], vela_actual['high'], vela_actual['low'], vela_actual['close']
        
        total_size = h - l
        if total_size <= 0: continue
        upper_wick, lower_wick = h - max(o, c), min(o, c) - l
        
        indicio_venta = (c < o) and (upper_wick >= (total_size * 0.35))
        indicio_compra = (c > o) and (lower_wick >= (total_size * 0.35))
        
        setup_detectado = None
        objetivo_tp = None
        direccion_num = 0 
        
        bajos_internos_pasados = [b['price'] for b in liquidez["interna"]["bajos"] if b['time'] < t_actual]
        altos_internos_pasados = [a['price'] for a in liquidez["interna"]["altos"] if a['time'] < t_actual]
        
        bajos_barridos = [b for b in bajos_internos_pasados if l <= b and c > b]
        altos_barridos = [a for a in altos_internos_pasados if h >= a and c < a]
        
        tamano_barrido = 0.0
        dist_int_inter = 0.0

        # GATILLO COMPRA
        if indicio_compra and bajos_barridos:
            setup_detectado = "COMPRA"
            direccion_num = 1
            tamano_barrido = min(bajos_barridos) - l 
            altos_intermedios_pasados = [a['price'] for a in liquidez["intermedia"]["altos"] if a['time'] < t_actual and a['price'] > c]
            objetivo_tp = min(altos_intermedios_pasados) if altos_intermedios_pasados else (c + MINIMO_TP_PUNTOS/100.0)
            dist_int_inter = objetivo_tp - c
            
        # GATILLO VENTA
        elif indicio_venta and altos_barridos:
            setup_detectado = "VENTA"
            direccion_num = -1
            tamano_barrido = h - max(altos_barridos) 
            bajos_intermedios_pasados = [b['price'] for b in liquidez["intermedia"]["bajos"] if b['time'] < t_actual and b['price'] < c]
            objetivo_tp = max(bajos_intermedios_pasados) if bajos_intermedios_pasados else (c - MINIMO_TP_PUNTOS/100.0)
            dist_int_inter = c - objetivo_tp

        if setup_detectado:
            dt = datetime.datetime.fromtimestamp(t_actual)
            hora = dt.hour
            sesion = 1 if 0 <= hora < 8 else (2 if 8 <= hora < 13 else 3) 
            fuerza_desplazamiento = abs(c - o) / total_size 
            
            # --- VIAJE AL FUTURO (APRENDIENDO EL PUNTO DE ENTRADA ÓPTIMO) ---
            resultado = 0 
            max_exceso = 0.0
            velas_para_exceso = 0
            velas_tardo_tp = VELAS_FUTURO
            sube_directo = 1 
            barrido_contrario = 0
            
            # 💡 AQUÍ ESTÁ LA MAGIA: Le damos $5 dólares de margen a la IA para que NO cierre la
            # operación y pueda aprender exactamente hasta dónde empujó el precio (exceso) antes de darse la vuelta.
            MARGEN_SL = 5.00 
            
            for j in range(1, VELAS_FUTURO + 1):
                v_fut = historico[i + j]
                
                if setup_detectado == "COMPRA":
                    retroceso_actual = c - v_fut['low']
                    if retroceso_actual > max_exceso: 
                        max_exceso = retroceso_actual
                        velas_para_exceso = j # Guarda cuántas velas tardó en dar el mejor punto de entrada
                        
                    if retroceso_actual > 2.0: 
                        sube_directo = 0 
                        
                    if v_fut['high'] >= objetivo_tp:
                        resultado = 1 
                        velas_tardo_tp = j
                        break
                    elif v_fut['low'] < (l - MARGEN_SL): # Solo fracasa si pasa los 5 dólares en contra
                        resultado = 0 
                        velas_tardo_tp = j
                        barrido_contrario = 1
                        break
                        
                elif setup_detectado == "VENTA":
                    retroceso_actual = v_fut['high'] - c
                    if retroceso_actual > max_exceso: 
                        max_exceso = retroceso_actual
                        velas_para_exceso = j # Guarda cuántas velas tardó en dar el mejor punto de entrada
                        
                    if retroceso_actual > 2.0: 
                        sube_directo = 0 
                        
                    if v_fut['low'] <= objetivo_tp:
                        resultado = 1 
                        velas_tardo_tp = j
                        break
                    elif v_fut['high'] > (h + MARGEN_SL): # Solo fracasa si pasa los 5 dólares en contra
                        resultado = 0 
                        velas_tardo_tp = j
                        barrido_contrario = 1
                        break
            
            if resultado == 1: exitosos += 1
            else: fallidos += 1
                
            secuencia_x = []
            for k in range(i - SEQ_LEN + 1, i + 1):
                v = historico[k]
                secuencia_x.extend([v['open'], v['high'], v['low'], v['close'], v['tick_volume']])
            
            secuencia_x.extend([
                hora, 
                sesion, 
                dist_int_inter, 
                fuerza_desplazamiento, 
                tamano_barrido, 
                direccion_num, 
                sube_directo,
                barrido_contrario
            ])
            
            X.append(secuencia_x)
            
            # MATRIZ Y (Lo que la red va a aprender ahora):
            # [Éxito, Puntos Exceso (Retroceso), Velas a Esperar, Distancia TP, SL Sugerido]
            # El SL Sugerido es el máximo exceso que alcanzó + 1 dólar de colchón protector
            sl_sugerido = max_exceso + 1.00 
            y.append([resultado, round(max_exceso, 2), velas_para_exceso, round(dist_int_inter, 2), round(sl_sugerido, 2)])

    print(f"\n📊 Total Setups de alta calidad encontrados: {len(X)}")
    print(f"🏆 Exitosos (Tocaron Intermedia): {exitosos} | 🛑 Fallidos (Rompieron Stop Loss de Emergencia): {fallidos}")
    return np.array(X), np.array(y)

if __name__ == "__main__":
    if inicializar():
        X_data, y_data = construir_dataset(60000) 
        if len(X_data) > 0:
            np.save("X_data.npy", X_data)
            np.save("y_data.npy", y_data)
            print("💾 Nuevos datos de 'Mejor Entrada' guardados en disco. ¡Ya puedes correr model.py!")
        mt5.shutdown()