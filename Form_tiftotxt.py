# -*- coding: utf-8 -*-
"""
针对 QZGY.shp 范围，遍历 4 种分辨率下 3 种气候变量 12 个月的 tif，
按“从上到下、从左到右”的像元顺序，将范围内所有像元中心点及其12个月值输出为 txt。

输出格式：
经度 纬度 一月 二月 三月 四月 五月 六月 七月 八月 九月 十月 十一月 十二月
lon lat v01 v02 ... v12
"""

import os
import glob
import numpy as np
import rasterio
from rasterio.features import geometry_mask
import fiona
from shapely.geometry import shape, mapping
# from shapely.ops import transform as shp_transform
# from pyproj import Transformer


# =========================
# 1. 路径配置
# =========================
base_dir = r"F:\wyf\数据存储\组内数据\严艳梓老师合作论文相关\data"
shp_path = r"F:\wyf\数据存储\组内数据\严艳梓老师合作论文相关\data\fanwei\QZGY.shp"
result_dir = r"F:\wyf\数据存储\组内数据\严艳梓老师合作论文相关\data\result"

# 分辨率列表
resolutions = ["10m", "5m", "2.5m", "30s"]

# 变量列表：变量代码 -> 中文名
variables = {
    "prec": "降水",
    "tavg": "气温",
    "srad": "短波辐射"
}

# 12个月标题
month_headers = [
    "一月", "二月", "三月", "四月", "五月", "六月",
    "七月", "八月", "九月", "十月", "十一月", "十二月"
]


# =========================
# 2. 工具函数
# =========================
def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)


def read_shapefile_geometries(shp_file):
    """
    读取 shp 几何，并返回：
    - geometries: shapely geometry 列表
    - shp_crs: shp 的坐标系
    """
    geometries = []
    with fiona.open(shp_file, "r") as src:
        shp_crs = src.crs_wkt if src.crs_wkt else src.crs
        for feat in src:
            geom = shape(feat["geometry"])
            geometries.append(geom)
    return geometries, shp_crs

"""
def reproject_geometries(geometries, src_crs, dst_crs):
    
    将 shp 几何从 src_crs 转到 dst_crs
    
    if not src_crs or not dst_crs:
        return geometries

    src_str = src_crs if isinstance(src_crs, str) else str(src_crs)
    dst_str = dst_crs.to_string() if hasattr(dst_crs, "to_string") else str(dst_crs)

    if src_str == dst_str:
        return geometries

    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    reprojected = []
    for geom in geometries:
        new_geom = shp_transform(transformer.transform, geom)
        reprojected.append(new_geom)
    return reprojected
"""

def get_monthly_tif_list(folder, var_code, res_name):
    """
    获取某个分辨率、某个变量下的12个月 tif，按月份排序
    例如：
    wc2.1_30s_prec_01.tif
    wc2.1_30s_prec_02.tif
    ...
    """
    pattern = os.path.join(folder, f"wc2.1_{res_name}_{var_code}_*.tif")
    tif_list = glob.glob(pattern)

    def extract_month(fp):
        name = os.path.basename(fp)
        # 假设最后两位月份在 .tif 前
        # 例如 wc2.1_30s_prec_05.tif
        month_str = os.path.splitext(name)[0].split("_")[-1]
        return int(month_str)

    tif_list = sorted(tif_list, key=extract_month)

    if len(tif_list) != 12:
        raise ValueError(f"文件夹 {folder} 中变量 {var_code} 未找到完整12个月 tif，当前数量：{len(tif_list)}")

    return tif_list


def build_inside_pixel_index(ref_tif, shp_geometries, shp_crs):
    """
    基于参考 tif 和 shp，得到范围内所有像元的位置索引、中心点坐标
    顺序：从上到下、从左到右（即 row 升序，col 升序）

    前提：shp 与 tif 已确认同为 EPSG:4326，不做任何重投影
    返回：
    rows_sorted, cols_sorted, xs, ys, ref_profile
    """

    with rasterio.open(ref_tif) as src:
        transform = src.transform
        raster_crs = src.crs
        height = src.height
        width = src.width
        nodata = src.nodata
        band1 = src.read(1)

        print(f"    参考栅格 CRS: {raster_crs}")
        print(f"    shp CRS: {shp_crs}")

        # 直接使用原始 shp 几何
        shp_geojson = [mapping(g) for g in shp_geometries]

        # True 表示范围内（因为 invert=True）
        inside_mask = geometry_mask(
            shp_geojson,
            transform=transform,
            invert=True,
            out_shape=(height, width)
        )

        # 再叠加 nodata 掩膜
        if nodata is not None:
            valid_mask = inside_mask & (band1 != nodata)
        else:
            valid_mask = inside_mask

        rows, cols = np.where(valid_mask)

        # 排序：先行后列 = 从上到下，从左到右
        order = np.lexsort((cols, rows))
        rows_sorted = rows[order]
        cols_sorted = cols[order]

        # 计算像元中心点坐标（仍为 EPSG:4326 经纬度）
        xs, ys = rasterio.transform.xy(transform, rows_sorted, cols_sorted, offset="center")
        xs = np.array(xs)
        ys = np.array(ys)

        return rows_sorted, cols_sorted, xs, ys, src.profile


def extract_monthly_values(tif_list, rows, cols):
    """
    从12个月 tif 中，按同一批像元位置提取值
    返回 shape = [n_points, 12] 的数组
    """
    n_points = len(rows)
    n_months = len(tif_list)

    values = np.full((n_points, n_months), np.nan, dtype=np.float32)

    for i, tif in enumerate(tif_list):
        with rasterio.open(tif) as src:
            print(f"正在读取: {tif}")

            arr = src.read(1)
            nodata = src.nodata

            month_vals = arr[rows, cols].astype(np.float32)

            if nodata is not None:
                month_vals = np.where(month_vals == nodata, np.nan, month_vals)

            values[:, i] = month_vals

    return values


def write_txt(out_txt, xs, ys, values):
    """
    写出 txt
    列格式：
    经度 纬度 一月 二月 ... 十二月
    """
    header = "经度 纬度 " + " ".join(month_headers)

    with open(out_txt, "w", encoding="utf-8") as f:
        f.write(header + "\n")

        n_points = len(xs)
        for i in range(n_points):
            row_items = [f"{xs[i]:.8f}", f"{ys[i]:.8f}"]

            for v in values[i]:
                if np.isnan(v):
                    row_items.append("nan")
                else:
                    # 可根据需要调整保留位数
                    row_items.append(f"{v:.6f}")

            f.write(" ".join(row_items) + "\n")


# =========================
# 3. 主处理流程
# =========================
def process_one_variable(res_name, var_code, var_cn, shp_geometries, shp_crs):
    """
    处理一个分辨率下的一个变量
    """
    folder = os.path.join(base_dir, res_name, f"wc2.1_{res_name}_{var_code}")
    if not os.path.exists(folder):
        raise FileNotFoundError(f"文件夹不存在：{folder}")

    tif_list = get_monthly_tif_list(folder, var_code, res_name)

    # 用第一个月作为参考栅格建立像元索引
    ref_tif = tif_list[0]
    rows, cols, xs, ys, _ = build_inside_pixel_index(ref_tif, shp_geometries, shp_crs)

    # 提取12个月值
    values = extract_monthly_values(tif_list, rows, cols)

    # 输出 txt
    ensure_dir(result_dir)
    out_txt = os.path.join(result_dir, f"{var_cn}_{res_name}.txt")
    write_txt(out_txt, xs, ys, values)

    print(f"{var_cn}_{res_name} 完成，共 {len(xs)} 个像元")
    # print(f"完成：{out_txt}，共写入 {len(xs)} 个像元")


def main():
    ensure_dir(result_dir)

    print("读取 shp...")
    shp_geometries, shp_crs = read_shapefile_geometries(shp_path)

    for res_name in resolutions:
        print(f"\n开始处理分辨率：{res_name}")

        for var_code, var_cn in variables.items():
            print(f"  处理变量：{var_cn} ({var_code})")
            process_one_variable(res_name, var_code, var_cn, shp_geometries, shp_crs)

    print("\n全部处理完成。")


if __name__ == "__main__":
    main()