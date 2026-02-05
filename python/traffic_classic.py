"""
traffic_classic.py  (Mesa 2.4 / Python 3.9 compatible)

Incluye:
- Zonas coloreadas (CENTRO/RESIDENCIAL/INDUSTRIAL/OTRA) en las celdas que son calle.  (aún falta mejorar la representación visual)
- 2 carriles por avenida (evita choques frontales).
- Regla anti-gridlock: no entrar a intersección si no puedes salir.
- Semáforos en cruces principales.
- Calles adicionales (2 horizontales + 2 verticales extra) para que existan calles dentro de otras zonas.
- Spawns en varias avenidas para que haya tráfico en varias áreas.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple

from mesa import Agent, Model
from mesa.time import RandomActivation
from mesa.space import MultiGrid
from mesa.datacollection import DataCollector

from mesa.visualization.modules import CanvasGrid, ChartModule, TextElement
from mesa.visualization.ModularVisualization import ModularServer

import numpy as np
import matplotlib.pyplot as plt

DIRECTIONS = {
    "E": (1, 0),
    "W": (-1, 0),
    "N": (0, 1),
    "S": (0, -1),
}

@dataclass(frozen=True)
class Zone:
    name: str
    x0: int
    y0: int
    x1: int
    y1: int
    base_spawn: float

    def contains(self, pos: Tuple[int, int]) -> bool:
        x, y = pos
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class RoadAgent(Agent):
    """Celda de calle con dirección de carril."""
    def __init__(self, unique_id, model, direction: str, zone_name: str):
        super().__init__(unique_id, model)
        self.direction = direction
        self.zone_name = zone_name

    def step(self):
        pass


class IntersectionAgent(Agent):
    """Intersección; puede tener semáforo."""
    def __init__(self, unique_id, model, has_traffic_light: bool, cycle: int = 12, green_dirs=("E", "W")):
        super().__init__(unique_id, model)
        self.has_traffic_light = has_traffic_light
        self.cycle = cycle
        self.green_dirs = tuple(green_dirs)
        self.t = 0
        self.phase = 0  # 0 => green_dirs, 1 => perpendicular

    def is_green_for(self, incoming_dir: str) -> bool:
        if not self.has_traffic_light:
            return True
        if self.phase == 0:
            return incoming_dir in self.green_dirs
        # perpendicular
        if self.green_dirs == ("E", "W"):
            return incoming_dir in ("N", "S")
        if self.green_dirs == ("N", "S"):
            return incoming_dir in ("E", "W")
        return True

    def step(self):
        if not self.has_traffic_light:
            return
        self.t += 1
        if self.t >= self.cycle:
            self.t = 0
            self.phase = 1 - self.phase


class CarAgent(Agent):
    """
    Auto:
    - direction fija (sin giros todavía)
    - speed/aceleración simplificadas
    - regla anti-gridlock en intersecciones
    """
    def __init__(self, unique_id, model, direction: str, vmax=1.0, accel=0.3):
        super().__init__(unique_id, model)
        self.direction = direction
        self.vmax = vmax
        self.accel = accel
        self.speed = 0.0
        self.wait_time = 0

    def _next_pos(self) -> Tuple[int, int]:
        dx, dy = DIRECTIONS[self.direction]
        x, y = self.pos
        return (x + dx, y + dy)

    def _pos_after(self, pos: Tuple[int, int]) -> Tuple[int, int]:
        dx, dy = DIRECTIONS[self.direction]
        x, y = pos
        return (x + dx, y + dy)

    def _cell_has_car(self, pos: Tuple[int, int]) -> bool:
        return any(isinstance(a, CarAgent) for a in self.model.grid.get_cell_list_contents([pos]))

    def _get_intersection(self, pos: Tuple[int, int]) -> Optional[IntersectionAgent]:
        for a in self.model.grid.get_cell_list_contents([pos]):
            if isinstance(a, IntersectionAgent):
                return a
        return None

    def _road_direction_ok(self, pos: Tuple[int, int]) -> bool:
        """
        Permite entrar a:
        - intersección siempre
        - road SOLO si road.direction == car.direction
        """
        cell = self.model.grid.get_cell_list_contents([pos])
        if any(isinstance(a, IntersectionAgent) for a in cell):
            return True

        roads = [a for a in cell if isinstance(a, RoadAgent)]
        if not roads:
            return False

        return any(r.direction == self.direction for r in roads)

    def step(self):
        self.speed = clamp(self.speed + self.accel, 0.0, self.vmax)

        nxt = self._next_pos()

        if self.model.grid.out_of_bounds(nxt):
            self.model.to_remove.append(self)
            return

        if not self._road_direction_ok(nxt):
            self.model.to_remove.append(self)
            return

        if self._cell_has_car(nxt):
            self.speed = 0.0
            self.wait_time += 1
            return

        inter = self._get_intersection(nxt)
        if inter is not None:
            if inter.has_traffic_light and not inter.is_green_for(self.direction):
                self.speed = 0.0
                self.wait_time += 1
                return

            # anti-gridlock: no entres si no puedes salir
            after = self._pos_after(nxt)
            if not self.model.grid.out_of_bounds(after):
                if (not self._road_direction_ok(after)) or self._cell_has_car(after):
                    self.speed = 0.0
                    self.wait_time += 1
                    return

        if self.speed > 0.5:
            self.model.grid.move_agent(self, nxt)
        else:
            self.wait_time += 1


class TrafficModel(Model):
    """
    Red vial:
    - 1 avenida central horizontal (2 carriles)
    - 1 avenida central vertical (2 carriles)
    - 2 avenidas horizontales extra (arriba / abajo) para tocar OTRA/INDUSTRIAL/RESIDENCIAL
    - 2 avenidas verticales extra (izq / der) para tocar más zonas
    - Intersecciones en cruces de avenidas; algunas con semáforo
    """
    def __init__(self, width=30, height=30, seed=42, base_spawn_scale=1.0):
        super().__init__()
        self.seconds_per_tick = 10.0  # conversion para metricas
        self.random.seed(seed)

        self.width = width
        self.height = height
        self.grid = MultiGrid(width, height, torus=False)
        self.schedule = RandomActivation(self)
        self.base_spawn_scale = base_spawn_scale

        self.minute_of_day = 7 * 60  # 07:00
        self.to_remove: List[CarAgent] = []
        self._uid = 0

        self.zones: List[Zone] = [
            Zone("CENTRO",      10, 10, 19, 19, base_spawn=0.12),
            Zone("RESIDENCIAL",  0,  0,  9,  9, base_spawn=0.06),
            Zone("INDUSTRIAL",  20,  0, 29,  9, base_spawn=0.07),
            Zone("OTRA",         0, 20,  9, 29, base_spawn=0.04),
        ]

        # modo de control para los semáforos
        # fixed -> ciclo fijo
        # adaptive -> ajustable
        self.signal_mode = "fixed"

        # vars globales para control de unity
        self.light_cycle = 12
        self.min_green_time = 6
        self.max_green_time = 14
        self.switch_threshold = 2 # diferencia de colas para cambiar
        self.detection_range = 8 # numero de celdas hacia atras para detectar autos

        # posiciones (avenidas)
        self._define_avenues()
        self._build_roads_and_intersections()

        self.datacollector = DataCollector(
            model_reporters={
                "CarsActive": lambda m: m.count_cars(),
                "AvgSpeed": lambda m: m.avg_speed(),
                "AvgWait": lambda m: m.avg_wait(),
                "AvgWaitSeconds": lambda m: m.avg_wait_seconds(),
                "PeakFactor": lambda m: m.peak_factor(),
                "DensityCentro": lambda m: m.zone_density("CENTRO"),
                "DensityRes": lambda m: m.zone_density("RESIDENCIAL"),
                "DensityInd": lambda m: m.zone_density("INDUSTRIAL"),
                "DensityOtra": lambda m: m.zone_density("OTRA"),
            }
        )

    def next_id(self) -> int:
        self._uid += 1
        return self._uid

    def _zone_name(self, pos: Tuple[int, int]) -> str:
        for z in self.zones:
            if z.contains(pos):
                return z.name
        return "FUERA"

    def _place(self, agent: Agent, pos: Tuple[int, int]):
        self.grid.place_agent(agent, pos)
        self.schedule.add(agent)

    def _define_avenues(self):
        """
        Define líneas de avenidas (con carriles):
        Horizontal:
          - cada avenida usa dos filas
        Vertical:
          - cada avenida usa dos columnas
        """
        cx = self.width // 2
        cy = self.height // 2

        # avenida central
        self.h_avenues = [cy]                 # fila base (E)
        self.v_avenues = [cx]                 # col base (N)

        # avenidas extra
        self.h_avenues += [6, self.height - 7]      # horizontales adicionales
        self.v_avenues += [6, self.width - 7]       # verticales adicionales

        # evitar duplicados y fuera de rango (necesitan y>=1 por carril W)
        self.h_avenues = sorted({y for y in self.h_avenues if 1 <= y < self.height})
        self.v_avenues = sorted({x for x in self.v_avenues if 0 <= x < self.width - 1}) 

    def _add_horizontal_avenue(self, yE: int):
        """Crea dos carriles."""
        yW = yE - 1
        if not (0 <= yW < self.height and 0 <= yE < self.height):
            return

        for x in range(self.width):
            self._place(RoadAgent(self.next_id(), self, "E", self._zone_name((x, yE))), (x, yE))
            self._place(RoadAgent(self.next_id(), self, "W", self._zone_name((x, yW))), (x, yW))

    def _add_vertical_avenue(self, xN: int):
        """Crea dos carriles: N en xN y S en xN+1."""
        xS = xN + 1
        if not (0 <= xN < self.width and 0 <= xS < self.width):
            return

        for y in range(self.height):
            self._place(RoadAgent(self.next_id(), self, "N", self._zone_name((xN, y))), (xN, y))
            self._place(RoadAgent(self.next_id(), self, "S", self._zone_name((xS, y))), (xS, y))

    def _build_roads_and_intersections(self):
        # calles
        for yE in self.h_avenues:
            self._add_horizontal_avenue(yE)

        for xN in self.v_avenues:
            self._add_vertical_avenue(xN)

        # intersecciones: cada cruce de avenida horizontal con vertical genera 2x2 celdas
        # (por los carriles dobles).
        for yE in self.h_avenues:
            yW = yE - 1
            for xN in self.v_avenues:
                xS = xN + 1
                cells = [(xN, yE), (xS, yE), (xN, yW), (xS, yW)]

                # Semáforo solo en cruces "importantes":
                # - el cruce central
                # - y el cruce cerca de industrial
                is_central = (yE == (self.height // 2) and xN == (self.width // 2))
                is_major = is_central or (yE == (self.height - 7) and xN == (self.width - 7))

                for pos in cells:
                    if 0 <= pos[0] < self.width and 0 <= pos[1] < self.height:
                        self._place(
                            IntersectionAgent(self.next_id(), self, has_traffic_light=is_major, cycle=self.light_cycle, green_dirs=("E", "W")),
                            pos,
                        )

    # ---- Tráfico / spawns

    def peak_factor(self) -> float:
        t = self.minute_of_day
        if 7 * 60 <= t <= 9 * 60 + 30:
            return 2.2
        if 17 * 60 <= t <= 19 * 60 + 30:
            return 2.4
        return 1.0

    def _spawn_points(self):
        """
        Spawns en bordes para varias avenidas.
        Para horizontal:
          - carril E: entra por (0, yE)
          - carril W: entra por (width-1, yW)
        Para vertical:
          - carril N: entra por (xN, 0)
          - carril S: entra por (xS, height-1)
        """
        pts = []

        for yE in self.h_avenues:
            yW = yE - 1
            pts.append(((0, yE), "E"))
            pts.append(((self.width - 1, yW), "W"))

        for xN in self.v_avenues:
            xS = xN + 1
            pts.append(((xN, 0), "N"))
            pts.append(((xS, self.height - 1), "S"))

        # quitar duplicados
        seen = set()
        out = []
        for p in pts:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    def _try_spawn_cars(self):
        pf = self.peak_factor()

        for (pos, dirn) in self._spawn_points():
            if any(isinstance(a, CarAgent) for a in self.grid.get_cell_list_contents([pos])):
                continue

            zname = self._zone_name(pos)
            base = 0.03  # default
            for z in self.zones:
                if z.name == zname:
                    base = z.base_spawn
                    break

            # industrial sube un poco más en horario tarde
            if zname == "INDUSTRIAL" and (17 * 60 <= self.minute_of_day <= 20 * 60):
                base *= 1.4

            p = base * pf * self.base_spawn_scale
            if self.random.random() < p:
                car = CarAgent(self.next_id(), self, direction=dirn, vmax=1.0, accel=0.3)
                self.grid.place_agent(car, pos)
                self.schedule.add(car)

    def _intersection_groups(self):
        """
        Agrupa los 4 IntersectionAgent de cada cruce real (2x2) usando la geometria
        de carriles definida por h_avenues y v_avenues (NO paridad x%2/y%2).
        Key = (xN, yE) donde:
        - carril N esta en xN, carril S en xN+1
        - carril E esta en yE, carril W en yE-1
        """
        groups = {}

        for a in self.schedule.agents:
            if not (isinstance(a, IntersectionAgent) and a.pos is not None and a.has_traffic_light):
                continue

            x, y = a.pos

            # hallar xN tal que x pertenece a {xN, xN+1}
            xN = None
            if x in self.v_avenues:
                xN = x
            elif (x - 1) in self.v_avenues:
                xN = x - 1
            else:
                continue

            # hallar yE tal que y pertenece a {yE, yE-1}
            yE = None
            if y in self.h_avenues:
                yE = y
            elif (y + 1) in self.h_avenues:
                yE = y + 1
            else:
                continue

            key = (xN, yE)
            groups.setdefault(key, []).append(a)

        return groups


    def _count_queue_for_group(self, key):
        """
        Cuenta demanda horizontal vs vertical alrededor del cruce real.
        key=(xN,yE) con:
        xS=xN+1, yW=yE-1
        """
        xN, yE = key
        xS = xN + 1
        yW = yE - 1
        L = self.detection_range

        def cars_in_cells(cells):
            cnt = 0
            for (cx, cy) in cells:
                if self.grid.out_of_bounds((cx, cy)):
                    continue
                for a in self.grid.get_cell_list_contents([(cx, cy)]):
                    if isinstance(a, CarAgent):
                        cnt += 1
            return cnt

        # Entra E por (xN,yE) viniendo desde la izquierda en yE (carril E)
        # Entra W por (xS,yW) viniendo desde la derecha en yW (carril W)
        horiz_cells = []
        horiz_cells += [(xN - i, yE) for i in range(1, L + 1)]  # approach E
        horiz_cells += [(xS + i, yW) for i in range(1, L + 1)]  # approach W

        # Entra N por (xN,yE) viniendo desde abajo en xN (carril N)
        # Entra S por (xS,yW) viniendo desde arriba en xS (carril S)
        vert_cells = []
        vert_cells += [(xN, yE - i) for i in range(1, L + 1)]  # approach N
        vert_cells += [(xS, yW + i) for i in range(1, L + 1)]  # approach S

        q_h = cars_in_cells(horiz_cells)
        q_v = cars_in_cells(vert_cells)
        return q_h, q_v

    def _update_lights_fixed(self):
        groups = self._intersection_groups()
        for key, agents in groups.items():
            ref = agents[0]
            ref.cycle = self.light_cycle
            ref.t += 1
            if ref.t >= ref.cycle:
                ref.t = 0
                ref.phase = 1 - ref.phase
            for b in agents[1:]:
                b.phase = ref.phase
                b.t = ref.t
                b.cycle = ref.cycle




    def _update_lights_actuated(self):
        """
        Adaptive:
        - Base fixed: alterna cada light_cycle
        - Gap-out: si ya cumplio min_green_time, y el verde no tiene demanda,
        pero el rojo si, entonces cambia antes.
        """
        groups = self._intersection_groups()

        for key, agents in groups.items():
            ref = agents[0]
            q_h, q_v = self._count_queue_for_group(key)

            # phase 0 => horizontal verde (E/W)
            # phase 1 => vertical verde (N/S)
            current = ref.phase
            ref.cycle = self.light_cycle
            ref.t += 1

            cur_demand = q_h if current == 0 else q_v
            oth_demand = q_v if current == 0 else q_h

            # --- 1) Gap-out (cambio anticipado seguro) ---
            if ref.t >= self.min_green_time:
                if cur_demand == 0 and oth_demand > 0:
                    ref.phase = 1 - ref.phase
                    ref.t = 0

            # --- 2) Fixed fallback (si no hubo gap-out) ---
            if ref.t >= ref.cycle:
                ref.t = 0
                ref.phase = 1 - ref.phase

            # Sync 2x2
            for b in agents:
                b.phase = ref.phase
                b.t = ref.t
                b.cycle = ref.cycle

    # ---- Métricas

    def count_cars(self) -> int:
        return sum(1 for a in self.schedule.agents if isinstance(a, CarAgent))

    def avg_speed(self) -> float:
        speeds = [a.speed for a in self.schedule.agents if isinstance(a, CarAgent)]
        return sum(speeds) / len(speeds) if speeds else 0.0

    def avg_wait(self) -> float:
        waits = [a.wait_time for a in self.schedule.agents if isinstance(a, CarAgent)]
        return sum(waits) / len(waits) if waits else 0.0

    def avg_wait_seconds(self) -> float:
        return self.avg_wait() * float(self.seconds_per_tick)

    def avg_speed_cells_per_tick(self) -> float:
        return self.avg_speed()

    def zone_density(self, zone_name: str) -> int:
        zone = next((z for z in self.zones if z.name == zone_name), None)
        if not zone:
            return 0
        return sum(
            1 for a in self.schedule.agents
            if isinstance(a, CarAgent) and a.pos and zone.contains(a.pos)
        )

    def step(self):
        self._try_spawn_cars()
        if self.signal_mode == "adaptive":
            self._update_lights_actuated()
        else:
            self._update_lights_fixed()

        cars = [a for a in list(self.schedule.agents) if isinstance(a, CarAgent)]
        for car in cars:
            car.step()

        if self.to_remove:
            for car in self.to_remove:
                if car in self.schedule.agents:
                    self.grid.remove_agent(car)
                    self.schedule.remove(car)
            self.to_remove.clear()

        self.datacollector.collect(self)
        self.minute_of_day = (self.minute_of_day + 1) % (24 * 60)


# ---- Visualization

def agent_portrayal(agent):
    if isinstance(agent, RoadAgent):
        zone = agent.zone_name
        col = "#E0E0E0"
        if zone == "CENTRO": col = "#F2D7D5"
        elif zone == "RESIDENCIAL": col = "#D5F5E3"
        elif zone == "INDUSTRIAL": col = "#FCF3CF"
        elif zone == "OTRA": col = "#D6EAF8"
        return {"Shape": "rect", "Color": col, "Filled": True, "Layer": 0, "w": 1.0, "h": 1.0}

    if isinstance(agent, IntersectionAgent):
        col = "#BDBDBD" if not agent.has_traffic_light else ("#A9CCE3" if agent.phase == 0 else "#F5B7B1")
        return {"Shape": "rect", "Color": col, "Filled": True, "Layer": 1, "w": 1.0, "h": 1.0}

    if isinstance(agent, CarAgent):
        s = clamp(agent.speed, 0.0, 1.0)
        col = "#E74C3C" if s < 0.2 else ("#F39C12" if s < 0.6 else "#2ECC71")
        return {"Shape": "circle", "Color": col, "Filled": True, "Layer": 2, "r": 0.45}

    return {}


class HUD(TextElement):
    def render(self, model: TrafficModel):
        h = model.minute_of_day // 60
        m = model.minute_of_day % 60
        return (
            f"Hora: {h:02d}:{m:02d} | "
            f"Autos: {model.count_cars()} | "
            f"VelProm: {model.avg_speed():.2f} | "
            f"EsperaProm: {model.avg_wait():.1f} | "
            f"FactorPico: {model.peak_factor():.1f} | "
            f"Centro:{model.zone_density('CENTRO')} "
            f"Res:{model.zone_density('RESIDENCIAL')} "
            f"Ind:{model.zone_density('INDUSTRIAL')} "
            f"Otra:{model.zone_density('OTRA')}"
        )


def run_server():
    width, height = 30, 30
    grid = CanvasGrid(agent_portrayal, width, height, 650, 650)

    chart = ChartModule(
        [
            {"Label": "CarsActive"},
            {"Label": "AvgSpeed"},
            {"Label": "AvgWait"},
            {"Label": "AvgWaitSeconds"},
            {"Label": "DensityCentro"},
            {"Label": "DensityRes"},
            {"Label": "DensityInd"},
            {"Label": "DensityOtra"},
        ],
        data_collector_name="datacollector",
    )

    hud = HUD()

    server = ModularServer(
        TrafficModel,
        [hud, grid, chart],
        "Traffic Multiagent Prototype",
        {"width": width, "height": height, "seed": 42, "base_spawn_scale": 1.0},
    )
    server.port = 8521
    server.launch()

# ---- Matplotlib evaluation plots (fixed vs adaptive) ----
def run_once_for_dataframe(
    signal_mode: str,
    steps: int = 600,
    seed: int = 42,
    base_spawn_scale: float = 1.0,
    light_cycle: int = 12,
    min_green_time: int = 4,
    max_green_time: int = 30,
    switch_threshold: int = 2,
    detection_range: int = 4,
):
    """
    Corre una simulación headless y regresa el DataFrame del DataCollector.
    """
    model = TrafficModel(width=30, height=30, seed=seed, base_spawn_scale=base_spawn_scale)

    # Configuración del modo semáforos
    model.signal_mode = signal_mode

    model.light_cycle = light_cycle
    model.min_green_time = min_green_time
    model.max_green_time = max_green_time
    model.switch_threshold = switch_threshold
    model.detection_range = detection_range

    for _ in range(steps):
        model.step()

    df = model.datacollector.get_model_vars_dataframe().copy()
    return df


def evaluate_kpi(avg_wait_seconds_mean: float, baseline_seconds: float = 60.0, target_reduction: float = 0.05):
    """
    KPI: lograr al menos -5% vs baseline 60s.
    """
    target_seconds = baseline_seconds * (1.0 - target_reduction)
    improvement_pct = (baseline_seconds - avg_wait_seconds_mean) / baseline_seconds  # 0.05 = 5%
    meets = avg_wait_seconds_mean <= target_seconds
    return {
        "baseline_seconds": baseline_seconds,
        "target_seconds": target_seconds,
        "mean_seconds": avg_wait_seconds_mean,
        "improvement_pct": improvement_pct,
        "meets_target": meets,
    }


def plot_wait_time_comparison(
    steps: int = 600,
    warmup: int = 100,
    runs: int = 5,
    baseline_seconds: float = 60.0,
    target_reduction: float = 0.05,
):
    """
    1) Serie de tiempo fixed vs adaptive (una corrida)
    2) Barras: promedio (post-warmup) y std en varias corridas
    """


    target_seconds = baseline_seconds * (1.0 - target_reduction)

    # Time-series de una corrida (misma seed para comparar)
    df_fixed = run_once_for_dataframe("fixed", steps=steps, seed=42)
    df_adap = run_once_for_dataframe("adaptive", steps=steps, seed=42)

    # recortar warmup
    s_fixed = df_fixed["AvgWaitSeconds"].iloc[warmup:].reset_index(drop=True)
    s_adap = df_adap["AvgWaitSeconds"].iloc[warmup:].reset_index(drop=True)

    # moving average simple para ver tendencia
    win = 20
    ma_fixed = s_fixed.rolling(win, min_periods=1).mean()
    ma_adap = s_adap.rolling(win, min_periods=1).mean()

    plt.figure()
    plt.plot(s_fixed.values, linewidth=1, alpha=0.35, label="fixed (raw)")
    plt.plot(s_adap.values, linewidth=1, alpha=0.35, label="adaptive (raw)")
    plt.plot(ma_fixed.values, linewidth=2, label=f"fixed (MA{win})")
    plt.plot(ma_adap.values, linewidth=2, label=f"adaptive (MA{win})")
    plt.axhline(baseline_seconds, linewidth=2, linestyle="--", label="baseline CDMX = 60s")
    plt.axhline(target_seconds, linewidth=2, linestyle=":", label=f"target = {target_seconds:.1f}s (-5%)")
    plt.title("AvgWaitSeconds (post-warmup) — fixed vs adaptive")
    plt.xlabel(f"Step (desde warmup={warmup})")
    plt.ylabel("AvgWaitSeconds (s)")
    plt.legend()
    plt.tight_layout()

    # Barras con varias corridas (semillas diferentes)
    fixed_means = []
    adap_means = []

    for i in range(runs):
        seed = 100 + i
        dfx = run_once_for_dataframe("fixed", steps=steps, seed=seed)
        dfa = run_once_for_dataframe("adaptive", steps=steps, seed=seed)

        fixed_means.append(float(dfx["AvgWaitSeconds"].iloc[warmup:].mean()))
        adap_means.append(float(dfa["AvgWaitSeconds"].iloc[warmup:].mean()))

    fixed_means = np.array(fixed_means, dtype=float)
    adap_means = np.array(adap_means, dtype=float)

    means = np.array([fixed_means.mean(), adap_means.mean()])
    stds = np.array([fixed_means.std(ddof=1) if runs > 1 else 0.0,
                     adap_means.std(ddof=1) if runs > 1 else 0.0])

    labels = ["fixed", "adaptive"]

    plt.figure()
    plt.bar(labels, means, yerr=stds, capsize=6)
    plt.axhline(baseline_seconds, linewidth=2, linestyle="--", label="baseline CDMX = 60s")
    plt.axhline(target_seconds, linewidth=2, linestyle=":", label=f"target = {target_seconds:.1f}s (-5%)")
    plt.title(f"AvgWaitSeconds mean±std (post-warmup) — {runs} runs")
    plt.ylabel("AvgWaitSeconds (s)")
    plt.legend()
    plt.tight_layout()

    # KPI summary (con el promedio del modo adaptive)
    kpi = evaluate_kpi(means[1], baseline_seconds=baseline_seconds, target_reduction=target_reduction)
    print("\n=== KPI (Adaptive) ===")
    print(f"Baseline: {kpi['baseline_seconds']:.1f}s")
    print(f"Target (-5%): {kpi['target_seconds']:.1f}s")
    print(f"Mean adaptive: {kpi['mean_seconds']:.2f}s")
    print(f"Improvement: {kpi['improvement_pct']*100:.2f}%")
    print(f"Meets target? {'YES' if kpi['meets_target'] else 'NO'}")

    plt.show()


# ejemplo de uso:
# python traffic_classic.py --mode plots --steps 600 --warmup 100 --runs 5
# o para correr la simulacion sola en el servidor:
# python traffic_classic.py --mode server
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["server", "plots"], default="server")
    parser.add_argument("--steps", type=int, default=600)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()

    if args.mode == "plots":
        plot_wait_time_comparison(steps=args.steps, warmup=args.warmup, runs=args.runs)
    else:
        run_server()

if __name__ == "__main__":
    run_server()

