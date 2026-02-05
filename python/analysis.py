import matplotlib.pyplot as plt
import numpy as np
from traffic_classic import TrafficModel, CarAgent, IntersectionAgent, RoadAgent

def run_analysis(steps=500):
    print("Iniciando análisis de rendimiento...")
    cdmx_baseline = 60.0
    target_value = cdmx_baseline * 0.95  # Reducción del 5%
    
    # Ejecutar modo FIJO
    model_fixed = TrafficModel(base_spawn_scale=1.5) # Aumentamos spawn para ver tráfico real
    model_fixed.signal_mode = "fixed"
    for _ in range(steps):
        model_fixed.step()
    wait_fixed = model_fixed.avg_wait_seconds()

    # Ejecutar modo ADAPTATIVO
    model_adaptive = TrafficModel(base_spawn_scale=1.5)
    model_adaptive.signal_mode = "adaptive"
    for _ in range(steps):
        model_adaptive.step()
    wait_adaptive = model_adaptive.avg_wait_seconds()

    # Generar Gráficas
    labels = ['CDMX (Base)', 'Modo Fijo', 'Modo Adaptativo']
    values = [cdmx_baseline, wait_fixed, wait_adaptive]
    colors = ['#95a5a6', '#e74c3c', '#2ecc71']

    plt.figure(figsize=(10, 6))
    bars = plt.bar(labels, values, color=colors, alpha=0.8)
    
    plt.axhline(y=target_value, color='blue', linestyle='--', label='Objetivo (-5%)')
    
    plt.title('Comparativa de Tiempo de Espera Promedio', fontsize=14)
    plt.ylabel('Segundos de espera')
    plt.ylim(0, max(values) + 10)
    plt.legend()

    for bar in bars:
        yval = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2, yval + 1, f'{yval:.2f}s', ha='center', va='bottom', fontweight='bold')

    improvement = ((cdmx_baseline - wait_adaptive) / cdmx_baseline) * 100
    
    print(f"--- Resultados ---")
    print(f"Espera CDMX: {cdmx_baseline}s")
    print(f"Espera Adaptativo: {wait_adaptive:.2f}s")
    print(f"Mejora respecto a CDMX: {improvement:.2f}%")

    if wait_adaptive <= target_value:
        plt.text(1.5, 5, "¡OBJETIVO LOGRADO!", color='green', fontsize=12, fontweight='bold', bbox=dict(facecolor='white', alpha=0.8))
    else:
        plt.text(1.5, 5, "OBJETIVO NO ALCANZADO", color='red', fontsize=12, fontweight='bold')

    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    run_analysis()
