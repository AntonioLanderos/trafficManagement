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

    [Header("UI")]
    public TMPro.TextMeshProUGUI metricsText;

    [Header("Lights (Directional)")]
    public bool directionalLights = true;

    // Qué tanto separar los indicadores EW/NS dentro de la intersección
    public float lightSeparation = 0.45f;

    // ---------------- Runtime state
    private bool mapBuilt = false;
    private Transform mapRoot;
    private Transform carsRoot;
    private Transform lightsRoot;

    private readonly Dictionary<int, GameObject> carGO = new Dictionary<int, GameObject>();
    private readonly Dictionary<int, Vector3> carTargetPos = new Dictionary<int, Vector3>();

    // Modo viejo (1 GO por celda light id)
    private readonly Dictionary<int, GameObject> lightGO = new Dictionary<int, GameObject>();

    // Modo nuevo: 2 indicadores por intersección (EW y NS)
    private readonly Dictionary<string, GameObject> lightEW = new Dictionary<string, GameObject>();
    private readonly Dictionary<string, GameObject> lightNS = new Dictionary<string, GameObject>();

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
        public int count_cars;
        public float avg_speed;
        public float avg_wait;
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

            //Debug.Log($"Metrics - Cars: {snap.count_cars} | AvgSpeed: {snap.avg_speed} | AvgWait: {snap.avg_wait}");

            UpdateMetricsUI(snap);
            UpdateCars(snap);

            if (directionalLights)
                UpdateLightsDirectional(snap);
            else
                UpdateLightsLegacy(snap);
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

        // Intersections primero
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
    void UpdateMetricsUI(SnapshotDTO snap)
    {
        if (metricsText == null) return;

        metricsText.text = $"Autos: {snap.count_cars}\n" +
                           $"Vel. Promedio: {snap.avg_speed:F2}\n" +
                           $"Espera Promedio: {snap.avg_wait:F1} ticks";
    }

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
                    go.transform.position = target;
                    carGO[c.id] = go;
                }

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

    void UpdateLightsLegacy(SnapshotDTO snap)
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

                Vector3 pos = GridToWorld(l.x, l.y) + new Vector3(0f, 0.5f, 0f);
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

                ApplyLightVisual(lightGO[l.id], l.phase);
            }
        }

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

    // ---------------- Lights: directional ----------------
    // Agrupa los 4 lights (2x2) en una sola intersección y crea 2 indicadores: EW y NS

    void UpdateLightsDirectional(SnapshotDTO snap)
    {
        // Si no hay prefab, usamos cubos automáticamente para testing
        HashSet<string> seenKeys = new HashSet<string>();

        if (snap.lights == null) return;

        // Guardar phase por intersección
        Dictionary<string, int> phaseByKey = new Dictionary<string, int>();
        Dictionary<string, Vector3> centerByKey = new Dictionary<string, Vector3>();

        foreach (var l in snap.lights)
        {
            string key = IntersectionKey2x2(l.x, l.y);
            seenKeys.Add(key);

            phaseByKey[key] = l.phase;

            Vector2Int baseCell = IntersectionBaseCell2x2(l.x, l.y);

            Vector3 center = GridToWorld(baseCell.x, baseCell.y);
            center += new Vector3(cellSize * 0.5f, 0f, cellSize * 0.5f);
            centerByKey[key] = center;
        }

        foreach (var kv in phaseByKey)
        {
            string key = kv.Key;
            int phase = kv.Value;

            Vector3 center = centerByKey[key];

            // Separación visual para distinguir dirección
            float sep = cellSize * lightSeparation;

            // EW a la derecha del centro, NS arriba del centro (en Z)
            // EW = sobre la horizontal (Z casi igual), NS = sobre la vertical (X casi igual)
            Vector3 posEW = center + new Vector3(0f, 0.5f, sep);
            Vector3 posNS = center + new Vector3(sep, 0.5f, 0f);


            // EW indicator
            if (!lightEW.ContainsKey(key) || lightEW[key] == null)
            {
                lightEW[key] = CreateLightGO($"LightEW_{key}");
                lightEW[key].transform.SetParent(lightsRoot);
            }
            lightEW[key].transform.position = posEW;

            // NS indicator
            if (!lightNS.ContainsKey(key) || lightNS[key] == null)
            {
                lightNS[key] = CreateLightGO($"LightNS_{key}");
                lightNS[key].transform.SetParent(lightsRoot);
            }
            lightNS[key].transform.position = posNS;

            bool ewGreen = (phase == 0);
            bool nsGreen = !ewGreen;

            SetLightColor(lightEW[key], ewGreen ? Color.green : Color.red);
            SetLightColor(lightNS[key], nsGreen ? Color.green : Color.red);
        }
    }

    GameObject CreateLightGO(string name)
    {
        GameObject go;

        if (trafficLightPrefab != null)
        {
            go = Instantiate(trafficLightPrefab);
        }
        else
        {
            go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.transform.localScale = new Vector3(0.6f, 0.6f, 0.6f);
        }

        go.name = name;
        return go;
    }

    void SetLightColor(GameObject go, Color c)
    {
        if (go == null) return;

        var mr = go.GetComponentInChildren<MeshRenderer>();
        if (mr != null && mr.material != null)
        {
            mr.material.color = c;
        }
    }

    string IntersectionKey2x2(int x, int y)
    {
        int bx = (x % 2 == 0) ? x : x - 1;
        int by = (y % 2 == 0) ? y : y - 1;
        return $"{bx}_{by}";
    }

    Vector2Int IntersectionBaseCell2x2(int x, int y)
    {
        int bx = (x % 2 == 0) ? x : x - 1;
        int by = (y % 2 == 0) ? y : y - 1;
        return new Vector2Int(bx, by);
    }

    // ---------------- Helpers ----------------

    Vector3 GridToWorld(int x, int y)
    {
        float wx = worldOrigin.x + x * cellSize;
        float wz = worldOrigin.z + y * cellSize;
        float wy = worldOrigin.y;
        return new Vector3(wx, wy, wz);
    }

    Quaternion DirToRotation(string dir)
    {
        // el carro mira hacia +Z por defecto
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

        Color c = (phase == 0) ? Color.green : Color.red;

        var mr = go.GetComponentInChildren<MeshRenderer>();
        if (mr != null && mr.material != null)
        {
            mr.material.color = c;
        }
    }

    Vector3 ApplyLaneOffset(Vector3 pos, string dir)
    {
        float off = cellSize * 0.25f;
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
