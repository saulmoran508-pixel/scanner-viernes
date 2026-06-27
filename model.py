import json
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import accuracy_score, mean_absolute_error
import joblib

MODEL_STATS_FILE = "model_stats.json"

def entrenar_cerebro():
    print("🧠 Cargando datos enriquecidos de liquidez...")
    try:
        X = np.load("X_data.npy")
        y = np.load("y_data.npy")
    except Exception as e:
        print("❌ Error: Ejecuta dataset_builder.py primero para generar los datos.", e)
        return

    print(f"📊 Componentes cargados con éxito: Matriz X {X.shape} | Matriz Y {y.shape}")

    # Separar las 5 columnas calculadas por el nuevo dataset_builder
    y_exito = y[:, 0]        
    y_retroceso = y[:, 1]    
    y_velas = y[:, 2]
    y_tp = y[:, 3]           
    y_sl = y[:, 4]

    # Separar en datos de entrenamiento y prueba (80% / 20%)
    X_train, X_test, y_train_exito, y_test_exito = train_test_split(X, y_exito, test_size=0.2, random_state=42)
    _, _, y_train_ret, y_test_ret = train_test_split(X, y_retroceso, test_size=0.2, random_state=42)
    _, _, y_train_velas, y_test_velas = train_test_split(X, y_velas, test_size=0.2, random_state=42)
    _, _, y_train_tp, y_test_tp = train_test_split(X, y_tp, test_size=0.2, random_state=42)
    _, _, y_train_sl, y_test_sl = train_test_split(X, y_sl, test_size=0.2, random_state=42)

    # 1. CEREBRO CLASIFICADOR (Probabilidad de Éxito)
    print("🚀 Entrenando IA de Probabilidad (Random Forest Classifier)...")
    clf = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42, n_jobs=-1)
    clf.fit(X_train, y_train_exito)
    acc = accuracy_score(y_test_exito, clf.predict(X_test))
    accuracy_pct = acc * 100
    print(f"✅ Precisión del Clasificador: {accuracy_pct:.2f}%")
    joblib.dump(clf, "bot_model_class.pkl")
    with open(MODEL_STATS_FILE, 'w', encoding='utf-8') as f:
        json.dump({"accuracy_pct": accuracy_pct}, f, ensure_ascii=False, indent=2)

    # 2. CEREBRO DE RETROCESO (Drawdown Máximo)
    print("🚀 Entrenando IA de Drawdown (Random Forest Regressor)...")
    reg_ret = RandomForestRegressor(n_estimators=100, max_depth=10, random_state=42, n_jobs=-1)
    reg_ret.fit(X_train, y_train_ret)
    error_ret = mean_absolute_error(y_test_ret, reg_ret.predict(X_test))
    print(f"📉 Margen de error estimando retroceso: ±{error_ret:.2f} USD")
    joblib.dump(reg_ret, "bot_model_retroceso.pkl")

    # 3. CEREBRO DE TIEMPO (Velas a esperar antes de la reversión)
    print("🚀 Entrenando IA de Tiempo de Espera (Random Forest Regressor)...")
    reg_velas = RandomForestRegressor(n_estimators=100, max_depth=10, random_state=42, n_jobs=-1)
    reg_velas.fit(X_train, y_train_velas)
    error_velas = mean_absolute_error(y_test_velas, reg_velas.predict(X_test))
    print(f"⏳ Margen de error en tiempo de espera: ±{error_velas:.1f} velas")
    joblib.dump(reg_velas, "bot_model_velas.pkl")

    # 4. CEREBRO DE TAKE PROFIT SUGERIDO
    print("🚀 Entrenando IA de Objetivos TP (Random Forest Regressor)...")
    reg_tp = RandomForestRegressor(n_estimators=100, max_depth=10, random_state=42, n_jobs=-1)
    reg_tp.fit(X_train, y_train_tp)
    error_tp = mean_absolute_error(y_test_tp, reg_tp.predict(X_test))
    print(f"🎯 Margen de error al sugerir TP: ±{error_tp:.2f} USD")
    joblib.dump(reg_tp, "bot_model_tp.pkl")

    # 5. CEREBRO DE STOP LOSS SUGERIDO (¡EL QUE TE FALTABA!)
    print("🚀 Entrenando IA de Stop Loss Sugerido (Random Forest Regressor)...")
    reg_sl = RandomForestRegressor(n_estimators=100, max_depth=10, random_state=42, n_jobs=-1)
    reg_sl.fit(X_train, y_train_sl)
    error_sl = mean_absolute_error(y_test_sl, reg_sl.predict(X_test))
    print(f"🛑 Margen de error al sugerir SL: ±{error_sl:.2f} USD")
    joblib.dump(reg_sl, "bot_model_sl.pkl")

    print("\n🎉 ¡Los 5 cerebros de IA han sido entrenados y sincronizados correctamente!")

if __name__ == "__main__":
    entrenar_cerebro()