import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from traffic_classic import TrafficModel

plt.style.use('ggplot')

def run_batch_simulation(cycle_time, steps, warmup, iterations=3):
    """
    Corre el modelo 'iterations' veces para el MISMO ciclo y promedia los resultados.
    Retorna el promedio de AvgWait (serie de tiempo) y el promedio global (escalar).
    """
    all_series = []
    global_averages = []

    print(f"   > Simulando Ciclo {cycle_time} ({iterations} iteraciones)...")

    for i in range(iterations):
        model = TrafficModel(seed=42 + i) 
        model.signal_mode = "fixed"
        model.light_cycle = cycle_time

        for _ in range(steps + warmup):
            model.step()
        
        df = model.datacollector.get_model_vars_dataframe()["AvgWait"]
        
        data_trimmed = df.iloc[warmup:].values
        all_series.append(data_trimmed)
        global_averages.append(np.mean(data_trimmed))

    avg_series = np.mean(all_series, axis=0)
    avg_scalar = np.mean(global_averages)
    
    return avg_series, avg_scalar

def run_optimization_analysis(
    simulation_steps=800, 
    warmup_steps=300, 
    baseline_cycle=12, 
    test_cycles=[6, 9, 12, 15, 18, 24],
    target_wait_seconds=60.0,
    iterations_per_config=3
):
    total_steps = simulation_steps + warmup_steps
    
    print(f"--- ANÁLISIS DE TRÁFICO ROBUSTO (CDMX) ---")
    print(f"Meta: Reducir espera un 5% (Target: < {target_wait_seconds * 0.95:.2f}s)")
    
    # obtener baseline (Ciclo 12)
    print(f"\n1. Estableciendo Línea Base (Ciclo {baseline_cycle})")
    base_series_ticks, base_avg_ticks = run_batch_simulation(baseline_cycle, simulation_steps, warmup_steps, iterations_per_config)
    
    # calibración de tiempo (de ticks a segundos reales)
    # Factor = 60s / Promedio_Ticks_Baseline
    time_factor = target_wait_seconds / base_avg_ticks
    print(f"   -> Calibración: 1 Tick ≈ {time_factor:.2f} segundos reales.")

    # convertimos el baseline a segundos
    base_series_sec = base_series_ticks * time_factor
    base_avg_sec = base_avg_ticks * time_factor
    target_sec = base_avg_sec * 0.95 # Meta del 5%
    
    results_summary = []
    
    # guardamos datos para graficar líneas
    lines_data = {f"Base ({baseline_cycle})": base_series_sec}
    
    # correr experimientos
    
    for cycle in test_cycles:
        if cycle == baseline_cycle:
            # ya tenemos este dato, solo lo registramos
            avg_wait_sec = base_avg_sec
        else:
            series_ticks, avg_ticks = run_batch_simulation(cycle, simulation_steps, warmup_steps, iterations_per_config)
            # convertir a segundos usando EL MISMO factor del baseline
            series_sec = series_ticks * time_factor
            avg_wait_sec = avg_ticks * time_factor
            lines_data[f"Ciclo {cycle}"] = series_sec
        
        # calcular mejora
        pct_change = ((avg_wait_sec - base_avg_sec) / base_avg_sec) * 100
        cumple = avg_wait_sec <= target_sec
        
        results_summary.append({
            "Ciclo": cycle,
            "Espera Promedio (s)": avg_wait_sec,
            "Cambio %": pct_change,
            "Cumple Meta": cumple
        })

    # convertir resumen a DataFrame para fácil manejo
    df_res = pd.DataFrame(results_summary)
    
    # visualización
    fig = plt.figure(figsize=(14, 10))
    gs = fig.add_gridspec(2, 2)

    # gráfica 1 series de tiempo
    ax1 = fig.add_subplot(gs[0, :])
    
    x_axis = np.arange(len(base_series_sec))
    
    # graficamos Baseline
    ax1.plot(x_axis, base_series_sec, color='black', linewidth=2, label=f'Baseline ({baseline_cycle})', zorder=10)
    ax1.axhline(y=target_sec, color='green', linestyle='--', linewidth=2, label='Meta (-5%)')
    
    # graficamos los demás
    for name, data in lines_data.items():
        if "Base" in name: continue
        # usamos rolling mean para suavizar
        smooth_data = pd.Series(data).rolling(window=20).mean()
        ax1.plot(x_axis, smooth_data, alpha=0.7, label=name)
        
    ax1.set_title(f'Evolución del Tiempo de Espera (Promedio de {iterations_per_config} simulaciones)')
    ax1.set_ylabel('Segundos de Espera')
    ax1.set_xlabel('Steps (Tiempo Simulado)')
    ax1.legend(loc='upper right')
    ax1.grid(True, alpha=0.3)

    # gráfica 2 de barras
    ax2 = fig.add_subplot(gs[1, 0])
    
    colors = ['green' if x else 'red' for x in df_res["Cumple Meta"]]
    bars = ax2.bar(df_res["Ciclo"].astype(str), df_res["Espera Promedio (s)"], color=colors, alpha=0.7)
    
    # línea de referencia del baseline
    ax2.axhline(y=base_avg_sec, color='black', linestyle='-', linewidth=1, label='Actual')
    ax2.axhline(y=target_sec, color='green', linestyle='--', linewidth=1.5, label='Meta')
    
    ax2.set_title('Tiempo de Espera Promedio por Ciclo')
    ax2.set_ylabel('Segundos')
    ax2.set_xlabel('Configuración de Semáforo (Steps)')
    ax2.bar_label(bars, fmt='%.1f s', padding=3)

    # gráfica 3 porcentaje de mejora
    ax3 = fig.add_subplot(gs[1, 1])
    
    # invertimos el signo para que "bajar tiempo" sea positivo
    mejoras = -df_res["Cambio %"] 
    colors_mejora = ['green' if x > 5 else 'gray' for x in mejoras]
    
    bars3 = ax3.bar(df_res["Ciclo"].astype(str), mejoras, color=colors_mejora)
    ax3.axhline(y=5, color='green', linestyle='--', label='Obj. 5%')
    ax3.axhline(y=0, color='black', linewidth=1)
    
    ax3.set_title('% de Reducción de Tiempo (Mayor es mejor)')
    ax3.set_ylabel('% Mejora')
    ax3.set_xlabel('Ciclo')
    ax3.bar_label(bars3, fmt='%.1f%%', padding=3)

    plt.tight_layout()
    plt.show()

    # resultado final
    print("\n" + "="*40)
    print("RESUMEN DE RESULTADOS")
    print("="*40)
    best_config = df_res.loc[df_res["Espera Promedio (s)"].idxmin()]
    
    print(df_res[["Ciclo", "Espera Promedio (s)", "Cambio %", "Cumple Meta"]])
    print("-" * 40)
    print(f"Mejor Configuración: Ciclo {best_config['Ciclo']}")
    print(f"Tiempo Logrado: {best_config['Espera Promedio (s)']:.2f}s")
    print(f"Reducción Total: {best_config['Cambio %']:.2f}%")

if __name__ == "__main__":
    run_optimization_analysis()