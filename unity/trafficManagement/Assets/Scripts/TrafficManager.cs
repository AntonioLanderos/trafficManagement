using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;
using TMPro;

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
    public TextMeshProUGUI metricsText;
    public Slider cycleSlider;
    public Slider spawnScaleSlider;
    public TMP_Dropdown modeDropdown;
    public Button applyConfigButton;
    public Button resetButton;
    public Button captureBaselineButton;

    [Header("Lights (Directional)")]
    public bool directionalLights = false;
    public float lightSeparation = 0.45f;

    private bool mapBuilt = false;
    private Transform mapRoot;
    private Transform carsRoot;
    private Transform lightsRoot;

    private readonly Dictionary<int, GameObject> carGO = new Dictionary<int, GameObject>();
    private readonly Dictionary<int, Vector3> carTargetPos = new Dictionary<int, Vector3>();

    private readonly Dictionary<int, GameObject> lightLegacyGO = new Dictionary<int, GameObject>();
    private readonly Dictionary<string, GameObject> lightEW = new Dictionary<string, GameObject>();
    private readonly Dictionary<string, GameObject> lightNS = new Dictionary<string, GameObject>();

    private float baselineAvgWaitSeconds = -1f;
    private float lastAvgWaitSeconds = 0f;

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
        public float avg_wait_seconds;
        public float seconds_per_tick;

        public string signal_mode;
        public int light_cycle;
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

    [System.Serializable]
    public class ConfigDTO
    {
        public string signal_mode;
        public int light_cycle;
        public float base_spawn_scale;
    }

    void Start()
    {
        mapRoot = new GameObject("MapRoot").transform;
        carsRoot = new GameObject("CarsRoot").transform;
        lightsRoot = new GameObject("LightsRoot").transform;

        if (applyConfigButton != null) applyConfigButton.onClick.AddListener(ApplyConfigFromUI);
        if (resetButton != null) resetButton.onClick.AddListener(ResetSimulation);
        if (captureBaselineButton != null) captureBaselineButton.onClick.AddListener(CaptureBaseline);

        StartCoroutine(InitThenLoop());
    }

    void Update()
    {
        if (!smooth) return;

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

            if (directionalLights)
                UpdateLightsDirectional(snap);
            else
                UpdateLightsLegacy(snap);

            lastAvgWaitSeconds = snap.avg_wait_seconds;
            UpdateMetricsText(snap);
        }
    }

    void BuildMap(MapDTO map)
    {
        if (intersectionTilePrefab == null || roadTilePrefab == null)
        {
            Debug.LogError("Assign Road Tile Prefab and Intersection Tile Prefab in Inspector.");
            return;
        }

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
                if (!lightLegacyGO.ContainsKey(l.id) || lightLegacyGO[l.id] == null)
                {
                    GameObject go = Instantiate(trafficLightPrefab, lightsRoot);
                    go.name = $"Light_{l.id}";
                    go.transform.position = pos;
                    lightLegacyGO[l.id] = go;
                }
                else
                {
                    lightLegacyGO[l.id].transform.position = pos;
                }

                ApplyLightVisual(lightLegacyGO[l.id], l.phase);
            }
        }

        List<int> toRemove = new List<int>();
        foreach (var kv in lightLegacyGO)
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
            lightLegacyGO.Remove(id);
        }
    }

    void UpdateLightsDirectional(SnapshotDTO snap)
    {
        if (trafficLightPrefab == null)
        {
            Debug.LogError("Traffic Light Prefab not assigned.");
            return;
        }

        HashSet<string> seenKeys = new HashSet<string>();

        if (snap.lights != null)
        {
            foreach (var l in snap.lights)
            {
                int gx = l.x - (l.x % 2);
                int gy = l.y - (l.y % 2);
                string key = gx + "_" + gy;

                if (seenKeys.Contains(key)) continue;
                seenKeys.Add(key);

                int phase = l.phase;
                Vector3 center = GridToWorld(gx, gy);

                Vector3 posEW = center + new Vector3(0f, 0.5f, lightSeparation);
                Vector3 posNS = center + new Vector3(lightSeparation, 0.5f, 0f);

                if (!lightEW.ContainsKey(key) || lightEW[key] == null)
                {
                    GameObject go = Instantiate(trafficLightPrefab, lightsRoot);
                    go.name = $"Light_EW_{key}";
                    lightEW[key] = go;
                }
                if (!lightNS.ContainsKey(key) || lightNS[key] == null)
                {
                    GameObject go = Instantiate(trafficLightPrefab, lightsRoot);
                    go.name = $"Light_NS_{key}";
                    lightNS[key] = go;
                }

                lightEW[key].transform.position = posEW;
                lightNS[key].transform.position = posNS;

                int phaseEW = (phase == 0) ? 0 : 1;
                int phaseNS = (phase == 0) ? 1 : 0;

                ApplyLightVisual(lightEW[key], phaseEW);
                ApplyLightVisual(lightNS[key], phaseNS);
            }
        }

        List<string> rm = new List<string>();
        foreach (var kv in lightEW)
        {
            if (!seenKeys.Contains(kv.Key))
            {
                if (kv.Value != null) Destroy(kv.Value);
                rm.Add(kv.Key);
            }
        }
        foreach (var k in rm) lightEW.Remove(k);

        rm.Clear();
        foreach (var kv in lightNS)
        {
            if (!seenKeys.Contains(kv.Key))
            {
                if (kv.Value != null) Destroy(kv.Value);
                rm.Add(kv.Key);
            }
        }
        foreach (var k in rm) lightNS.Remove(k);
    }

    void UpdateMetricsText(SnapshotDTO snap)
    {
        if (metricsText == null) return;

        string impStr = "N/A";
        if (baselineAvgWaitSeconds > 0f)
        {
            float improvement = (baselineAvgWaitSeconds - snap.avg_wait_seconds) / baselineAvgWaitSeconds * 100f;
            impStr = improvement.ToString("0.0") + "%";
        }

        metricsText.text =
            "Tick: " + snap.tick +
            "\nCars: " + snap.count_cars +
            "\nAvgWait (ticks): " + snap.avg_wait.ToString("0.0") +
            "\nAvgWait (sec): " + snap.avg_wait_seconds.ToString("0.00") +
            "\nBaseline (sec): " + (baselineAvgWaitSeconds > 0f ? baselineAvgWaitSeconds.ToString("0.00") : "N/A") +
            "\nImprovement: " + impStr +
            "\nMode: " + snap.signal_mode +
            "\nCycle: " + snap.light_cycle;
    }

    public void CaptureBaseline()
    {
        baselineAvgWaitSeconds = lastAvgWaitSeconds;
        Debug.Log("Baseline captured: " + baselineAvgWaitSeconds.ToString("0.00") + " sec");
    }

    public void ApplyConfigFromUI()
    {
        string mode = "fixed";
        if (modeDropdown != null)
        {
            mode = (modeDropdown.value == 0) ? "fixed" : "adaptive";
        }

        int cycle = (cycleSlider != null) ? Mathf.RoundToInt(cycleSlider.value) : 12;
        float spawn = (spawnScaleSlider != null) ? spawnScaleSlider.value : 1f;

        StartCoroutine(PostConfig(mode, cycle, spawn));
    }

    IEnumerator PostConfig(string mode, int cycle, float spawnScale)
    {
        string configUrl = url.TrimEnd('/') + "/config";
        ConfigDTO cfg = new ConfigDTO { signal_mode = mode, light_cycle = cycle, base_spawn_scale = spawnScale };
        string json = JsonUtility.ToJson(cfg);

        using (UnityWebRequest req = new UnityWebRequest(configUrl, "POST"))
        {
            byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes(json);
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
                Debug.LogError("Config POST error: " + req.error);
                Debug.LogError(req.downloadHandler.text);
            }
        }
    }

    public void ResetSimulation()
    {
        baselineAvgWaitSeconds = -1f;
        StartCoroutine(PostReset());
    }

    IEnumerator PostReset()
    {
        string resetUrl = url.TrimEnd('/') + "/reset";

        using (UnityWebRequest req = new UnityWebRequest(resetUrl, "POST"))
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
                Debug.LogError("Reset POST error: " + req.error);
                Debug.LogError(req.downloadHandler.text);
                yield break;
            }

            ClearRuntimeObjects();
            mapBuilt = false;

            yield return FetchAndBuildMap();
        }
    }

    void ClearRuntimeObjects()
    {
        foreach (var kv in carGO) if (kv.Value != null) Destroy(kv.Value);
        carGO.Clear();
        carTargetPos.Clear();

        foreach (var kv in lightLegacyGO) if (kv.Value != null) Destroy(kv.Value);
        lightLegacyGO.Clear();

        foreach (var kv in lightEW) if (kv.Value != null) Destroy(kv.Value);
        lightEW.Clear();

        foreach (var kv in lightNS) if (kv.Value != null) Destroy(kv.Value);
        lightNS.Clear();

        if (mapRoot != null) Destroy(mapRoot.gameObject);
        if (carsRoot != null) Destroy(carsRoot.gameObject);
        if (lightsRoot != null) Destroy(lightsRoot.gameObject);

        mapRoot = new GameObject("MapRoot").transform;
        carsRoot = new GameObject("CarsRoot").transform;
        lightsRoot = new GameObject("LightsRoot").transform;
    }

    Vector3 GridToWorld(int x, int y)
    {
        float wx = worldOrigin.x + x * cellSize;
        float wz = worldOrigin.z + y * cellSize;
        float wy = worldOrigin.y;
        return new Vector3(wx, wy, wz);
    }

    Quaternion DirToRotation(string dir)
    {
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
