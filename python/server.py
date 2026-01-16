# server.py
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging

# Importa modelo Mesa y clases de agentes
from traffic_classic import TrafficModel, CarAgent, IntersectionAgent, RoadAgent

# --- Estado global de simulación
MODEL = TrafficModel(width=30, height=30, seed=42, base_spawn_scale=1.0)
TICK = 0


def build_snapshot(model: TrafficModel, tick: int) -> dict:
    cars = []
    lights = []

    for a in model.schedule.agents:
        if isinstance(a, CarAgent) and a.pos is not None:
            x, y = a.pos
            cars.append({
                "id": int(a.unique_id),
                "x": int(x),
                "y": int(y),
                "dir": str(a.direction),
                "speed": float(a.speed),
            })

        if isinstance(a, IntersectionAgent) and a.pos is not None and a.has_traffic_light:
            x, y = a.pos
            lights.append({
                "id": int(a.unique_id),
                "x": int(x),
                "y": int(y),
                "phase": int(a.phase),  # 0 o 1
            })

    return {
        "tick": int(tick),
        "width": int(model.width),
        "height": int(model.height),
        "cars": cars,
        "lights": lights,
    }


def build_map(model: TrafficModel) -> dict:
    roads = []
    intersections = []

    for a in model.schedule.agents:
        if isinstance(a, RoadAgent) and a.pos is not None:
            x, y = a.pos
            roads.append({
                "x": int(x),
                "y": int(y),
                "dir": str(a.direction),
                "zone": str(a.zone_name),
            })

        if isinstance(a, IntersectionAgent) and a.pos is not None:
            x, y = a.pos
            intersections.append({
                "x": int(x),
                "y": int(y),
                "hasLight": bool(a.has_traffic_light),
            })

    return {
        "width": int(model.width),
        "height": int(model.height),
        "roads": roads,
        "intersections": intersections,
    }


class UnityHandler(BaseHTTPRequestHandler):
    def _set_headers(self, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        # CORS simple (por si pruebas desde navegador/herramientas)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)
        self.wfile.write(b"{}")

    def do_GET(self):
        global TICK

        if self.path.startswith("/map"):
            self._set_headers(200)
            payload = build_map(MODEL)
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        # Snapshot sin avanzar (útil para debug en navegador)
        self._set_headers(200)
        snap = build_snapshot(MODEL, TICK)
        self.wfile.write(json.dumps(snap).encode("utf-8"))

    def do_POST(self):
        global TICK

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"

        # Validar JSON (Unity manda "{}" o algo simple)
        try:
            _ = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode("utf-8"))
            return

        # Avanza 1 tick
        MODEL.step()
        TICK += 1

        # Regresa snapshot
        self._set_headers(200)
        snap = build_snapshot(MODEL, TICK)
        self.wfile.write(json.dumps(snap).encode("utf-8"))


def run(port=8585):
    logging.basicConfig(level=logging.INFO)
    server_address = ("", port)
    httpd = HTTPServer(server_address, UnityHandler)
    logging.info(f"Unity server running on http://localhost:{port}")
    logging.info("GET  /map -> returns static map (roads/intersections)")
    logging.info("POST /     -> advances 1 step and returns snapshot (cars/lights)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    logging.info("Server stopped.")


if __name__ == "__main__":
    run(8585)
# server.py
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging

# Importa tu modelo Mesa y clases de agentes
from traffic_classic import TrafficModel, CarAgent, IntersectionAgent, RoadAgent

# --- Estado global de simulación
MODEL = TrafficModel(width=30, height=30, seed=42, base_spawn_scale=1.0)
TICK = 0


def build_snapshot(model: TrafficModel, tick: int) -> dict:
    cars = []
    lights = []

    for a in model.schedule.agents:
        if isinstance(a, CarAgent) and a.pos is not None:
            x, y = a.pos
            cars.append({
                "id": int(a.unique_id),
                "x": int(x),
                "y": int(y),
                "dir": str(a.direction),
                "speed": float(a.speed),
            })

        if isinstance(a, IntersectionAgent) and a.pos is not None and a.has_traffic_light:
            x, y = a.pos
            lights.append({
                "id": int(a.unique_id),
                "x": int(x),
                "y": int(y),
                "phase": int(a.phase),  # 0 o 1
            })

    return {
        "tick": int(tick),
        "width": int(model.width),
        "height": int(model.height),
        "cars": cars,
        "lights": lights,
    }


def build_map(model: TrafficModel) -> dict:
    roads = []
    intersections = []

    for a in model.schedule.agents:
        if isinstance(a, RoadAgent) and a.pos is not None:
            x, y = a.pos
            roads.append({
                "x": int(x),
                "y": int(y),
                "dir": str(a.direction),
                "zone": str(a.zone_name),
            })

        if isinstance(a, IntersectionAgent) and a.pos is not None:
            x, y = a.pos
            intersections.append({
                "x": int(x),
                "y": int(y),
                "hasLight": bool(a.has_traffic_light),
            })

    return {
        "width": int(model.width),
        "height": int(model.height),
        "roads": roads,
        "intersections": intersections,
    }


class UnityHandler(BaseHTTPRequestHandler):
    def _set_headers(self, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        # CORS simple (por si pruebas desde navegador/herramientas)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)
        self.wfile.write(b"{}")

    def do_GET(self):
        global TICK

        if self.path.startswith("/map"):
            self._set_headers(200)
            payload = build_map(MODEL)
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        # Snapshot sin avanzar (útil para debug en navegador)
        self._set_headers(200)
        snap = build_snapshot(MODEL, TICK)
        self.wfile.write(json.dumps(snap).encode("utf-8"))

    def do_POST(self):
        global TICK

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"

        # Validar JSON (Unity manda "{}" o algo simple)
        try:
            _ = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode("utf-8"))
            return

        # Avanza 1 tick
        MODEL.step()
        TICK += 1

        # Regresa snapshot
        self._set_headers(200)
        snap = build_snapshot(MODEL, TICK)
        self.wfile.write(json.dumps(snap).encode("utf-8"))


def run(port=8585):
    logging.basicConfig(level=logging.INFO)
    server_address = ("", port)
    httpd = HTTPServer(server_address, UnityHandler)
    logging.info(f"Unity server running on http://localhost:{port}")
    logging.info("GET  /map -> returns static map (roads/intersections)")
    logging.info("POST /     -> advances 1 step and returns snapshot (cars/lights)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    logging.info("Server stopped.")


if __name__ == "__main__":
    run(8585)

