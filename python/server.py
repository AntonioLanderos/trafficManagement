from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging

from traffic_classic import TrafficModel, CarAgent, IntersectionAgent, RoadAgent

CONFIG = {
    "width": 30,
    "height": 30,
    "seed": 42,
    "seconds_per_tick": 3.0,
    "base_spawn_scale": 1.0,
    "signal_mode": "fixed",
    "light_cycle": 12,
    "min_green_time": 4,
    "max_green_time": 30,
    "switch_threshold": 2,
    "detection_range": 4,
}

MODEL = None
TICK = 0


def make_model():
    m = TrafficModel(
        width=int(CONFIG["width"]),
        height=int(CONFIG["height"]),
        seed=int(CONFIG["seed"]),
        base_spawn_scale=float(CONFIG["base_spawn_scale"]),
    )
    m.seconds_per_tick = float(CONFIG["seconds_per_tick"])
    m.signal_mode = str(CONFIG["signal_mode"])
    m.light_cycle = int(CONFIG["light_cycle"])
    m.min_green_time = int(CONFIG["min_green_time"])
    m.max_green_time = int(CONFIG["max_green_time"])
    m.switch_threshold = int(CONFIG["switch_threshold"])
    m.detection_range = int(CONFIG["detection_range"])
    return m


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
                "phase": int(a.phase),
            })

    return {
        "tick": int(tick),
        "width": int(model.width),
        "height": int(model.height),
        "cars": cars,
        "lights": lights,
        "count_cars": int(model.count_cars()),
        "avg_speed": round(model.avg_speed(), 2),
        "avg_wait": round(model.avg_wait(), 1),
        "avg_wait_seconds": round(model.avg_wait_seconds(), 2),
        "seconds_per_tick": float(model.seconds_per_tick),
        "signal_mode": str(getattr(model, "signal_mode", "fixed")),
        "light_cycle": int(getattr(model, "light_cycle", 12)),
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)
        self.wfile.write(b"{}")

    def do_GET(self):
        global TICK, MODEL

        if self.path.startswith("/map"):
            self._set_headers(200)
            payload = build_map(MODEL)
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        if self.path.startswith("/metrics"):
            self._set_headers(200)
            payload = {
                "tick": int(TICK),
                "count_cars": int(MODEL.count_cars()),
                "avg_speed": round(MODEL.avg_speed(), 2),
                "avg_wait": round(MODEL.avg_wait(), 1),
                "avg_wait_seconds": round(MODEL.avg_wait_seconds(), 2),
                "seconds_per_tick": float(MODEL.seconds_per_tick),
                "signal_mode": str(getattr(MODEL, "signal_mode", "fixed")),
                "light_cycle": int(getattr(MODEL, "light_cycle", 12)),
            }
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        self._set_headers(200)
        snap = build_snapshot(MODEL, TICK)
        self.wfile.write(json.dumps(snap).encode("utf-8"))

    def do_POST(self):
        global TICK, MODEL, CONFIG

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode("utf-8"))
            return

        if self.path.startswith("/config"):
            if not isinstance(data, dict):
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": "Body must be JSON object"}).encode("utf-8"))
                return

            for k in CONFIG.keys():
                if k in data:
                    CONFIG[k] = data[k]

            MODEL.seconds_per_tick = float(CONFIG["seconds_per_tick"])
            MODEL.base_spawn_scale = float(CONFIG["base_spawn_scale"])
            MODEL.signal_mode = str(CONFIG["signal_mode"])
            MODEL.light_cycle = int(CONFIG["light_cycle"])
            MODEL.min_green_time = int(CONFIG["min_green_time"])
            MODEL.max_green_time = int(CONFIG["max_green_time"])
            MODEL.switch_threshold = int(CONFIG["switch_threshold"])
            MODEL.detection_range = int(CONFIG["detection_range"])

            self._set_headers(200)
            self.wfile.write(json.dumps({"ok": True, "config": CONFIG, "snapshot": build_snapshot(MODEL, TICK)}).encode("utf-8"))
            return

        if self.path.startswith("/reset"):
            MODEL = make_model()
            TICK = 0
            self._set_headers(200)
            self.wfile.write(json.dumps({"ok": True, "config": CONFIG, "snapshot": build_snapshot(MODEL, TICK)}).encode("utf-8"))
            return

        MODEL.step()
        TICK += 1

        self._set_headers(200)
        snap = build_snapshot(MODEL, TICK)
        self.wfile.write(json.dumps(snap).encode("utf-8"))


def run(port=8585):
    global MODEL, TICK
    MODEL = make_model()
    TICK = 0

    logging.basicConfig(level=logging.INFO)
    server_address = ("", port)
    httpd = HTTPServer(server_address, UnityHandler)
    logging.info(f"Unity server running on http://localhost:{port}")
    logging.info("GET  /map -> returns static map (roads/intersections)")
    logging.info("POST /     -> advances 1 step and returns snapshot (cars/lights)")
    logging.info("POST /config -> update parameters")
    logging.info("POST /reset  -> restart simulation")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    logging.info("Server stopped.")


if __name__ == "__main__":
    run(8585)
