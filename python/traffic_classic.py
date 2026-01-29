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

# TODO: reducir scope del proyecto 
# TODO: mejorar agente Car para que utilice una implementacion de A* (revisar si vale la pena porque en la visualización no se ven los giros)
# TODO: reinforcement learning para semáforos inteligentes con base en tiempo de espera y densidad de tráfico

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple

from mesa import Agent, Model
from mesa.time import RandomActivation
from mesa.space import MultiGrid
from mesa.datacollection import DataCollector

from mesa.visualization.modules import CanvasGrid, ChartModule, TextElement
from mesa.visualization.ModularVisualization import ModularServer

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

        # posiciones (avenidas)
        self._define_avenues()
        self._build_roads_and_intersections()

        self.datacollector = DataCollector(
            model_reporters={
                "CarsActive": lambda m: m.count_cars(),
                "AvgSpeed": lambda m: m.avg_speed(),
                "AvgWait": lambda m: m.avg_wait(),
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
                            IntersectionAgent(self.next_id(), self, has_traffic_light=is_major, cycle=12, green_dirs=("E", "W")),
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

    # ---- Métricas

    def count_cars(self) -> int:
        return sum(1 for a in self.schedule.agents if isinstance(a, CarAgent))

    def avg_speed(self) -> float:
        speeds = [a.speed for a in self.schedule.agents if isinstance(a, CarAgent)]
        return sum(speeds) / len(speeds) if speeds else 0.0

    def avg_wait(self) -> float:
        waits = [a.wait_time for a in self.schedule.agents if isinstance(a, CarAgent)]
        return sum(waits) / len(waits) if waits else 0.0

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
        self.schedule.step()

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


# TODO: añadir unidades a las métricas del HUD
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


if __name__ == "__main__":
    run_server()

