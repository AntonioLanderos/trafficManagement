# Traffic Manager project server side and model
this project contains the server that exposes the endpoints for the Traffic Manager Unity simulation, aswell as the model that handles the traffic management logic using Mesa.

## General Architecture

### Python (Server + MESA)
server.py: 
- Runs Model `TrafficModel`
- Exposes HTTP endoints to send map and snapshots for assets location

Endpoints:
- `GET /map` → returns static map (roads + intersections)
- `POST /` → advances one step in the model and returns snapshot of agents' locations (cars + traffic lights)
- `GET /` → returns a simple static snapshot for debugging

---

### Unity (Client)
- When initializing, does `GET /map` to build the map
- Then does `POST /` every certain time (`requestEverySeconds`) to update agents
- Cars and traffic lights are instantiated/destroyed dynamically

## Requirements
- Python 3.9+
- Mesa 2.4.0

## Setup
1. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```
2. Install dependencies mentioned before

**Make sure you have both files in the same folder**
- `server.py`
- `traffic_classic.py` (contains 'TrafficModel' and agent definitions)

3. Run the server:
   ```bash
   python server.py
   ```
4. The server will start on `http://localhost:8585`

5. Test base endpoint:
    ```bash
    curl http://localhost:8585/map
    ```
    5.1 And to advance a step:
    ```bash
    curl -X POST http://localhost:8585 -H "Content-Type: application/json" -d "{}"
    ```

## Unity Setup
1. Open the Scene
    Open the main project scene in Unity.

2. TrafficManager GameObject
    In the Hierarchy, there must be a GameObject named TrafficManager with the following script attached:
    TrafficManager.cs

    In the Inspector, assign the following fields:
    ### Server
    - Url: http://localhost:8585

### Prefabs
- Car Prefab: 3D green car prefab
- Traffic Light Prefab: traffic light prefab (just a cube for now)
- Road Tile Prefab: RoadTile.prefab
- Intersection Tile Prefab: IntersectionTile.prefab

### World Mapping (recommended)
- Cell Size: 2
- World Origin: (-30, 0, -30) (to center a 30x30 map)

### Update Rate
- Request Every Seconds: 0.1 (10 updates/second)

### Smoothing
- Smooth: true
- Lerp Speed: 12 - 25

## Putting it all together
1. Start the Python server:
   ```bash
   python server.py
   ```
2. send a request to advance a step to make sure it's working:
   ```bash
   curl -X POST http://localhost:8585 -H "Content-Type: application/json" -d "{}"
   ``` 

3. In Unity, press Play to start the simulation.

