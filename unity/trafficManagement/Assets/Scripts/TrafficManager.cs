using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;

public class TrafficManager : MonoBehaviour
{
    [Header("Server")]
    public string url = "http://localhost:8585";

    [Header("Prefabs")]
    public GameObject carPrefab;
    public GameObject trafficLightPrefab;
    public GameObject roadTilePrefab;
    public GameObject intersectionTilePrefab;

    [Header("World mapping")]
    public float cellSize = 1f;
    public Vector3 worldOrigin = Vector3.zero;

    [Header("Update rate")]
    public float requestEverySeconds = 0.1f;

    [Header("Smoothing")]
    public bool smooth = true;
    public float lerpSpeed = 12f;

    // --- Runtime state
    private bool mapBuilt = false;
    private Transform mapRoot;
    private Transform carsRoot;
    private Transform lightsRoot;

    private readonly Dictionary<int, GameObject> carGO = new Dictionary<int, GameObject>();
    private readonly Dictionary<int, Vector3> carTargetPos = new Dictionary<int, Vector3>();

    private readonly Dictionary<int, GameObject> lightGO = new Dictionary<int, GameObject>();

    // ---------------- DTOs (JsonUtility compatible) ----------------

    [System.Serializable]
    public class CarDTO
    {
        public int id;
        public int x;
        public int y;
        public string dir;
        public float speed;
    }

    [System.Serializable]
    public class LightDTO
    {
        public int id;
        public int x;
        public int y;
        public int phase; // 0 o 1
    }

    [System.Serializable]
    public class SnapshotDTO
    {
        public int tick;
        public int width;
        public int height;
        public CarDTO[] cars;
        public LightDTO[] lights;
    }

    [System.Serializable]
    public class RoadCellDTO
    {
        public int x;
        public int y;
        public string dir;
        public string zone;
    }

    [System.Serializable]
    public class IntersectionCellDTO
    {
        public int x;
        public int y;
        public bool hasLight;
    }

    [System.Serializable]
    public class MapDTO
    {
        public int width;
        public int height;
        public RoadCellDTO[] roads;
        public IntersectionCellDTO[] intersections;
    }

    // ---------------- Unity lifecycle ----------------

    void Start()
    {
        mapRoot = new GameObject("MapRoot").transform;
        carsRoot = new GameObject("CarsRoot").transform;
        lightsRoot = new GameObject("LightsRoot").transform;

        StartCoroutine(InitThenLoop());
    }

    void Update()
    {
        if (!smooth) return;

        // Suavizado: mueve carros hacia su target
        foreach (var kv in carGO)
        {
            int id = kv.Key;
            GameObject go = kv.Value;
            if (go == null) continue;
            if (!carTargetPos.ContainsKey(id)) continue;

            Vector3 target = carTargetPos[id];
            go.transform.position = Vector3.Lerp(go.transform.position, target, Time.deltaTime * lerpSpeed);
        }
    }

    IEnumerator InitThenLoop()
    {
        yield return FetchAndBuildMap();
        StartCoroutine(Loop());
    }

    IEnumerator Loop()
    {
        while (true)
        {
            yield return FetchTickAndUpdate();
            yield return new WaitForSeconds(requestEverySeconds);
        }
    }

    // ---------------- Networking ----------------

    IEnumerator FetchAndBuildMap()
    {
        string mapUrl = url.TrimEnd('/') + "/map";

        using (UnityWebRequest req = UnityWebRequest.Get(mapUrl))
        {
            req.SetRequestHeader("Accept", "application/json");
            yield return req.SendWebRequest();

#if UNITY_2020_2_OR_NEWER
            if (req.result != UnityWebRequest.Result.Success)
#else
            if (req.isNetworkError || req.isHttpError)
#endif
            {
                Debug.LogError("Error fetching /map: " + req.error);
                Debug.LogError(req.downloadHandler.text);
                yield break;
            }

            MapDTO map = JsonUtility.FromJson<MapDTO>(req.downloadHandler.text);
            if (map == null)
            {
                Debug.LogError("Failed to parse /map response.");
                yield break;
            }

            BuildMap(map);
            mapBuilt = true;

            int roadsCount = (map.roads != null) ? map.roads.Length : 0;
            int interCount = (map.intersections != null) ? map.intersections.Length : 0;
            Debug.Log($"Map built ✅ Roads={roadsCount}, Intersections={interCount}");
        }
    }

    IEnumerator FetchTickAndUpdate()
    {
        if (!mapBuilt) yield break;

        // POST "/" con body "{}" para avanzar 1 tick
        string postUrl = url.TrimEnd('/');

        using (UnityWebRequest req = new UnityWebRequest(postUrl, "POST"))
        {
            byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes("{}");
            req.uploadHandler = new UploadHandlerRaw(bodyRaw);
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.SetRequestHeader("Accept", "application/json");

            yield return req.SendWebRequest();

#if UNITY_2020_2_OR_NEWER
            if (req.result != UnityWebRequest.Result.Success)
#else
            if (req.isNetworkError || req.isHttpError)
#endif
            {
                Debug.LogError("Tick POST error: " + req.error);
                Debug.LogError(req.downloadHandler.text);
                yield break;
            }

            SnapshotDTO snap = JsonUtility.FromJson<SnapshotDTO>(req.downloadHandler.text);
            if (snap == null)
            {
                Debug.LogError("Failed to parse snapshot JSON.");
                Debug.LogError(req.downloadHandler.text);
                yield break;
            }

            UpdateCars(snap);
            UpdateLights(snap);
        }
    }

    // ---------------- Map building ----------------

    void BuildMap(MapDTO map)
    {
        if (intersectionTilePrefab == null || roadTilePrefab == null)
        {
            Debug.LogError("Assign Road Tile Prefab and Intersection Tile Prefab in Inspector.");
            return;
        }

        // Intersections primero para que queden “encima” (puedes ajustar escala en prefab)
        if (map.intersections != null)
        {
            foreach (var c in map.intersections)
            {
                GameObject tile = Instantiate(intersectionTilePrefab, mapRoot);
                tile.transform.position = GridToWorld(c.x, c.y);
                tile.name = $"I_{c.x}_{c.y}";
            }
        }

        if (map.roads != null)
        {
            foreach (var c in map.roads)
            {
                GameObject tile = Instantiate(roadTilePrefab, mapRoot);
                tile.transform.position = GridToWorld(c.x, c.y);
                tile.name = $"R_{c.x}_{c.y}";
            }
        }
    }

    // ---------------- Entity updates ----------------

    void UpdateCars(SnapshotDTO snap)
    {
        if (carPrefab == null)
        {
            Debug.LogError("Car Prefab not assigned.");
            return;
        }

        HashSet<int> seen = new HashSet<int>();

        if (snap.cars != null)
        {
            foreach (var c in snap.cars)
            {
                seen.Add(c.id);

                Vector3 target = ApplyLaneOffset(GridToWorld(c.x, c.y), c.dir);
                carTargetPos[c.id] = target;

                if (!carGO.ContainsKey(c.id) || carGO[c.id] == null)
                {
                    GameObject go = Instantiate(carPrefab, carsRoot);
                    go.name = $"Car_{c.id}";
                    go.transform.position = target; // spawn at target
                    carGO[c.id] = go;
                }

                // Rotación por dirección (E,W,N,S)
                GameObject car = carGO[c.id];
                if (car != null)
                {
                    car.transform.rotation = DirToRotation(c.dir);
                    if (!smooth) car.transform.position = target;
                }
            }
        }

        // Remover carros que ya no existen
        List<int> toRemove = new List<int>();
        foreach (var kv in carGO)
        {
            int id = kv.Key;
            if (!seen.Contains(id))
            {
                if (kv.Value != null) Destroy(kv.Value);
                toRemove.Add(id);
            }
        }
        foreach (int id in toRemove)
        {
            carGO.Remove(id);
            carTargetPos.Remove(id);
        }
    }

    void UpdateLights(SnapshotDTO snap)
    {
        if (trafficLightPrefab == null)
        {
            Debug.LogError("Traffic Light Prefab not assigned.");
            return;
        }

        HashSet<int> seen = new HashSet<int>();

        if (snap.lights != null)
        {
            foreach (var l in snap.lights)
            {
                seen.Add(l.id);

                Vector3 pos = GridToWorld(l.x, l.y) + new Vector3(0f, 0.5f, 0f); // un poquito arriba
                if (!lightGO.ContainsKey(l.id) || lightGO[l.id] == null)
                {
                    GameObject go = Instantiate(trafficLightPrefab, lightsRoot);
                    go.name = $"Light_{l.id}";
                    go.transform.position = pos;
                    lightGO[l.id] = go;
                }
                else
                {
                    lightGO[l.id].transform.position = pos;
                }

                // Color según phase
                ApplyLightVisual(lightGO[l.id], l.phase);
            }
        }

        // Remover lights faltantes (si cambiara el mapa; normalmente no pasa)
        List<int> toRemove = new List<int>();
        foreach (var kv in lightGO)
        {
            int id = kv.Key;
            if (!seen.Contains(id))
            {
                if (kv.Value != null) Destroy(kv.Value);
                toRemove.Add(id);
            }
        }
        foreach (int id in toRemove)
        {
            lightGO.Remove(id);
        }
    }

    // ---------------- Helpers ----------------

    Vector3 GridToWorld(int x, int y)
    {
        // Mapea (x,y) del grid Mesa a Unity (X,Z)
        float wx = worldOrigin.x + x * cellSize;
        float wz = worldOrigin.z + y * cellSize;
        float wy = worldOrigin.y;
        return new Vector3(wx, wy, wz);
    }

    Quaternion DirToRotation(string dir)
    {
        // Ajusta si tu modelo apunta a otra dirección “forward”
        // Asumimos que el carro mira hacia +Z por defecto:
        // N = +Z, S = -Z, E = +X, W = -X
        switch (dir)
        {
            case "N": return Quaternion.Euler(0f, 0f, 0f);
            case "S": return Quaternion.Euler(0f, 180f, 0f);
            case "E": return Quaternion.Euler(0f, 90f, 0f);
            case "W": return Quaternion.Euler(0f, 270f, 0f);
            default: return Quaternion.identity;
        }
    }

    void ApplyLightVisual(GameObject go, int phase)
    {
        if (go == null) return;

        // 0 => verde, 1 => rojo
        Color c = (phase == 0) ? Color.green : Color.red;

        // Cambia el color del material si hay MeshRenderer
        var mr = go.GetComponentInChildren<MeshRenderer>();
        if (mr != null && mr.material != null)
        {
            mr.material.color = c;
        }
    }

    Vector3 ApplyLaneOffset(Vector3 pos, string dir)
    {
        float off = cellSize * 0.25f; // con cellSize=2 => 0.5
        switch (dir)
        {
            case "E": pos.z += off; break;
            case "W": pos.z -= off; break;
            case "N": pos.x -= off; break;
            case "S": pos.x += off; break;
        }
        return pos;
    }

}
