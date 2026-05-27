import glob
import json
import logging
import math
import os
import re
import struct
import sys
import traceback

import fiona
import uvicorn
from fastapi import FastAPI, HTTPException
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.strtree import STRtree


def get_resource_dir() -> str:
    """Return the directory that stores bundled read-only assets."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_runtime_dir() -> str:
    """Return the directory that stores runtime-writable files."""
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(__file__))

    preferred_dir = os.path.dirname(sys.executable)
    if os.access(preferred_dir, os.W_OK):
        return preferred_dir

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        fallback_dir = os.path.join(local_app_data, "flood-api")
    else:
        fallback_dir = os.path.join(os.path.expanduser("~"), ".flood-api")

    os.makedirs(fallback_dir, exist_ok=True)
    return fallback_dir


RESOURCE_DIR = get_resource_dir()
RUNTIME_DIR = get_runtime_dir()
GRID_OUTPUT_DIR = os.path.join(RESOURCE_DIR, "grid_outputs", "Depth ().TR-All.TIN_VT0.50m")

SHP_PATH = os.path.join(
    GRID_OUTPUT_DIR,
    "Depth ().TR-All.TIN_VT0.50m_Grid.shp",
)
DAT_DIR = GRID_OUTPUT_DIR

STATIONS_JSON_PATH = os.path.join(RUNTIME_DIR, "stations.json")
STATION_INFO_DIR = os.path.join(RUNTIME_DIR, "stationInfo")
os.makedirs(STATION_INFO_DIR, exist_ok=True)

STARTUP_LOG_PATH = os.path.join(RUNTIME_DIR, "api_server_startup.log")
ERROR_LOG_PATH = os.path.join(RUNTIME_DIR, "api_server_error.log")


def configure_logging() -> logging.Logger:
    """Configure logging for both console runs and packaged exe runs."""
    logger = logging.getLogger("flood_api")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(STARTUP_LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger


LOGGER = configure_logging()


def write_error_log(exc: Exception) -> None:
    """Persist a full traceback so exe startup failures are diagnosable."""
    with open(ERROR_LOG_PATH, "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
        f.write("\n")
        f.write(f"exception_type={type(exc).__name__}\n")
        f.write(f"resource_dir={RESOURCE_DIR}\n")
        f.write(f"runtime_dir={RUNTIME_DIR}\n")
        f.write(f"shp_path={SHP_PATH}\n")
        f.write(f"dat_dir={DAT_DIR}\n")


def validate_packaged_resources() -> None:
    """Fail fast with actionable messages when bundled assets are missing."""
    if not os.path.exists(SHP_PATH):
        raise RuntimeError(
            "找不到 SHP 文件。请确认打包时包含了 grid_outputs 目录，"
            "并且 exe 输出目录中存在对应的网格资源。"
            f" 当前路径: {SHP_PATH}"
        )

    if not os.path.exists(DAT_DIR):
        raise RuntimeError(
            "找不到 DAT 文件夹。请确认打包时包含了 grid_outputs 目录。"
            f" 当前路径: {DAT_DIR}"
        )

    dat_candidates = glob.glob(os.path.join(DAT_DIR, "*.dat"))
    if not dat_candidates:
        raise RuntimeError(
            "DAT 文件夹中没有找到任何 .dat 文件。请检查 --add-data 是否正确包含了时序数据文件。"
        )


def pause_before_exit() -> None:
    """Keep the console open long enough for users to read startup errors."""
    if getattr(sys, "frozen", False) and os.name == "nt":
        os.system("pause")

app = FastAPI(title="洪水水深时序查询 API (极速纯净版)", version="1.0")

# 预加载到内存中的全局对象
POLYGONS = []
GRID_IDS = []
SPATIAL_INDEX = None
TRANSFORMER = None
DAT_FILES = []


@app.on_event("startup")
async def load_data_on_startup():
    """在服务启动时，将数据预加载到内存中。"""
    global POLYGONS, GRID_IDS, SPATIAL_INDEX, TRANSFORMER, DAT_FILES

    LOGGER.info("正在启动服务，加载空间网格数据入内存...")
    LOGGER.info("RESOURCE_DIR=%s", RESOURCE_DIR)
    LOGGER.info("RUNTIME_DIR=%s", RUNTIME_DIR)
    LOGGER.info("SHP_PATH=%s", SHP_PATH)
    LOGGER.info("DAT_DIR=%s", DAT_DIR)

    validate_packaged_resources()

    polygons = []
    grid_ids = []
    transformer = None

    with fiona.open(SHP_PATH, "r") as src:
        shp_crs = src.crs
        if shp_crs:
            LOGGER.info("检测到 SHP 坐标系: %s，正在初始化坐标转换引擎...", shp_crs)
            transformer = Transformer.from_crs("EPSG:4326", shp_crs, always_xy=True)

        LOGGER.info("正在解析几何网格并构建空间索引...")
        for feat in src:
            geom = shape(feat["geometry"])
            grid_id = feat["properties"].get("ID", feat["properties"].get("id"))
            if grid_id is None:
                raise RuntimeError("Shapefile 属性表缺少唯一 ID 字段，需提供 'ID' 或 'id'。")

            polygons.append(geom)
            grid_ids.append(grid_id)

    dat_files = sorted(glob.glob(os.path.join(DAT_DIR, "*.dat")))

    POLYGONS = polygons
    GRID_IDS = grid_ids
    TRANSFORMER = transformer
    SPATIAL_INDEX = STRtree(POLYGONS)
    DAT_FILES = dat_files

    LOGGER.info("空间索引构建完成，共包含 %s 个网格。", len(POLYGONS))
    LOGGER.info("DAT 扫描完成，共找到 %s 个历史时刻文件。", len(DAT_FILES))
    LOGGER.info("服务启动完毕，请在浏览器访问 http://127.0.0.1:8000/docs 进行测试。")


@app.get("/api/get_depth_curve")
async def get_depth_curve(lon: float, lat: float):
    """
    根据经纬度查询历史水深曲线。
    :param lon: 经度 (如: 102.6)
    :param lat: 纬度 (如: 17.9)
    """
    if SPATIAL_INDEX is None or not DAT_FILES:
        raise HTTPException(status_code=500, detail="服务器数据未准备好。")

    query_x, query_y = lon, lat
    if TRANSFORMER:
        try:
            query_x, query_y = TRANSFORMER.transform(lon, lat)
            if not math.isfinite(query_x) or not math.isfinite(query_y):
                raise ValueError("坐标转换结果不是有效数值。")
        except Exception:
            return {"status": "error", "message": "坐标转换失败，请检查经纬度是否合理。"}

    query_point = Point(query_x, query_y)
    candidate_indices = SPATIAL_INDEX.query(query_point)

    matched_grid_id = None
    for idx in candidate_indices:
        if POLYGONS[idx].intersects(query_point):
            matched_grid_id = int(GRID_IDS[idx])
            break

    if matched_grid_id is None:
        return {
            "status": "warning",
            "message": f"坐标点 ({lon}, {lat}) 不在任何水深网格范围内！",
            "data": None,
        }

    LOGGER.info("查询坐标 (%s, %s) -> 命中网格 ID: %s", lon, lat, matched_grid_id)

    time_series_data = []
    record_size = 28
    byte_offset = (matched_grid_id - 1) * record_size

    for dat_file in DAT_FILES:
        filename = os.path.basename(dat_file)
        match = re.search(r"(\d{10})", filename)
        time_str = match.group(1) if match else filename

        try:
            with open(dat_file, "rb") as f:
                f.seek(byte_offset)
                chunk = f.read(record_size)

            if len(chunk) == record_size:
                parsed_id, h, u, v = struct.unpack("<iddd", chunk)
                if parsed_id != matched_grid_id:
                    continue

                time_series_data.append(
                    {
                        "time": time_str,
                        "depth_m": round(h, 3),
                        "u_m_s": round(u, 3),
                        "v_m_s": round(v, 3),
                    }
                )
        except Exception as exc:
            LOGGER.exception("读取文件 %s 失败: %s", filename, exc)

    return {
        "status": "success",
        "message": "查询成功",
        "point_info": {
            "input_lon": lon,
            "input_lat": lat,
            "matched_grid_id": matched_grid_id,
        },
        "data_length": len(time_series_data),
        "time_series": time_series_data,
    }


@app.get("/api/get_device_list")
async def get_device_list():
    """获取所有设备列表。"""
    device_list = [
        {"id": 1, "name": "设备A", "type": "传感器", "location": "地点1"},
        {"id": 2, "name": "设备B", "type": "执行器", "location": "地点2"},
        {"id": 3, "name": "设备C", "type": "传感器", "location": "地点3"},
    ]
    return {
        "status": "success",
        "message": "查询成功",
        "device_list": device_list,
    }


@app.get("/api/get_station_measurement")
async def get_station_measurement(station_id: str):
    """根据 stationId 查询对应的 measurement JSON 数据。"""
    file_path = os.path.join(STATION_INFO_DIR, f"{station_id}.json")
    if not os.path.exists(file_path):
        return {
            "status": "error",
            "message": f"未找到 stationId 为 {station_id} 的数据文件，可能尚未拉取或 ID 错误。",
            "data": None,
        }

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"status": "success", "message": "查询成功", "data": data}
    except Exception as exc:
        return {
            "status": "error",
            "message": f"读取 measurement 数据失败: {exc}",
            "data": None,
        }


@app.get("/api/get_stations")
async def get_stations():
    """查询最新的 stations JSON 数据。"""
    if not os.path.exists(STATIONS_JSON_PATH):
        return {
            "status": "error",
            "message": "stations 数据文件不存在，请等待定时任务拉取完成。",
            "data": None,
        }

    try:
        with open(STATIONS_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"status": "success", "message": "查询成功", "data": data}
    except Exception as exc:
        return {
            "status": "error",
            "message": f"读取 stations 数据失败: {exc}",
            "data": None,
        }


def run_server() -> None:
    """Start the API server and persist startup failures to disk."""
    try:
        # 打包成 exe 后不要启用 reload，避免多进程重复启动。
        uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
    except Exception as exc:
        write_error_log(exc)
        LOGGER.exception("服务启动失败，详细堆栈已写入 %s", ERROR_LOG_PATH)
        print(f"\n服务启动失败，详细错误已写入: {ERROR_LOG_PATH}")
        print("常见原因：资源目录未打包、缺少 GIS 动态库、端口 8000 被占用。")
        pause_before_exit()
        raise


if __name__ == "__main__":
    run_server()
