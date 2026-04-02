import streamlit as st
import streamlit_authenticator as stauth
import yaml
from yaml.loader import SafeLoader
import ee
from google.oauth2 import service_account
import folium
from folium.plugins import Draw
from streamlit_folium import st_folium
import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.ticker import FormatStrFormatter
from shapely.geometry import mapping
from datetime import date
import tempfile, os, json
from io import BytesIO
from PIL import Image
import requests
import math
import altair as alt

# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config("Advanced Vegetation Monitoring", layout="wide")

# =============================
# LOGIN AUTHENTICATION
# =============================

with open("config.yaml") as file:
    config = yaml.load(file, Loader=SafeLoader)

authenticator = stauth.Authenticate(
    config["credentials"],
    config["cookie"]["name"],
    config["cookie"]["key"],
    config["cookie"]["expiry_days"]
)

# =============================
# CUSTOM LOGIN UI STYLE
# =============================

st.markdown("""
<style>

/* Hide default Streamlit header */
header {visibility: hidden;}
footer {visibility: hidden;}

/* Center login container */
.login-container {
    display:flex;
    flex-direction:column;
    align-items:center;
    justify-content:center;
    margin-top:80px;
}

/* Card style */
.login-card {
    background-color:#0b1220;
    padding:40px;
    border-radius:16px;
    box-shadow:0px 10px 30px rgba(0,0,0,0.5);
    width:400px;
}

/* Title styling */
.login-title {
    text-align:center;
    font-size:28px;
    font-weight:700;
    color:#00d084;
    margin-bottom:5px;
}

/* Subtitle */
.login-subtitle {
    text-align:center;
    font-size:14px;
    color:#9aa4b2;
    margin-bottom:25px;
}

</style>
""", unsafe_allow_html=True)


# Logo + Title
st.markdown("""
<div class="login-container">
    <div class="login-title"> AgroCast </div>
    <div class="login-subtitle">Advanced Vegetation Monitoring</div>
</div>
""", unsafe_allow_html=True)


# Render authenticator login form
authenticator.login(location="main")


authentication_status = st.session_state.get("authentication_status")
name = st.session_state.get("name")
username = st.session_state.get("username")

authentication_status = st.session_state.get("authentication_status")
name = st.session_state.get("name")
username = st.session_state.get("username")

if authentication_status:
    authenticator.logout(location="sidebar")
# ============================================================
# TOP TITLE
# ============================================================
    st.markdown(
        """
        <h2 style="
            margin-top:0px;
            margin-bottom:10px;
            color:#1f77b4;
            font-weight:700;
            text-align:left;
        ">
        Advanced Vegetation Monitoring 
        </h2>
        """,
        unsafe_allow_html=True
    )

    # ============================================================
    # EARTH ENGINE AUTH
    # ============================================================
    @st.cache_resource
    def init_ee():
        # Ensure you have your .streamlit/secrets.toml set up correctly
        if "ee" in st.secrets:
            info = dict(st.secrets["ee"])
            creds = service_account.Credentials.from_service_account_info(
                info,
                scopes=["https://www.googleapis.com/auth/earthengine"]
            )
            ee.Initialize(creds)
        else:
            st.error("Earth Engine secrets not found. Please configure .streamlit/secrets.toml")

    init_ee()

    # ============================================================
    # FOLIUM EE LAYER
    # ============================================================
    def add_ee_layer(self, image, vis, name):
        map_id = ee.Image(image).getMapId(vis)
        folium.TileLayer(
            tiles=map_id["tile_fetcher"].url_format,
            attr="Google Earth Engine",
            name=name,
            overlay=True,
            control=True
        ).add_to(self)

    folium.Map.add_ee_layer = add_ee_layer

    # ============================================================
    # HELPERS
    # ============================================================
    def read_geojson(upload):
        geojson = json.load(upload)
        gdf = gpd.GeoDataFrame.from_features(geojson["features"], crs="EPSG:4326")
        geom = gdf.geometry.unary_union
        return ee.Geometry(mapping(geom)), geom.bounds

    def ee_date(d):
        return ee.Date.fromYMD(d.year, d.month, d.day)

    def get_s2_collection(aoi, start, end, cloud):
        return (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(aoi)
            .filterDate(start, end)
            .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", cloud))
            .map(lambda i: i.divide(10000).clip(aoi).set('system:time_start', i.get('system:time_start')))
        )

    def calculate_index(image, index_name):
        if index_name == "NDVI":
            return image.normalizedDifference(["B8", "B4"]).rename("NDVI")
        elif index_name == "NDMI":
            return image.normalizedDifference(["B8", "B11"]).rename("NDMI")
        elif index_name == "EVI":
            evi = image.expression(
                '2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))', {
                    'NIR': image.select('B8'),
                    'RED': image.select('B4'),
                    'BLUE': image.select('B2')
                }).rename("EVI")
            return evi
        return image

    # ============================================================
    # SIDEBAR
    # ============================================================
    with st.sidebar:
        st.image("https://agrocastanalytics.com/home/agrocast-logo.png", width=160)
        st.caption("Powered by AgroCast Analytics")
        
        uploaded = st.file_uploader("Upload AOI (GeoJSON only)", type=["geojson"])
        
        index_option = st.selectbox("Select Index", ["NDVI", "NDMI", "EVI"])
        
        cloud = st.slider("Max Cloud Cover (%)", 0, 100, 20)
        start = st.date_input("Start Date", date(2024, 1, 1))
        end = st.date_input("End Date", date(2024, 1, 31))
        show_ts = st.checkbox(f"Show {index_option} Time Series", value=True)

    # ============================================================
    # VISUALIZATION PARAMETERS & THRESHOLDS
    # ============================================================
    if index_option == "NDVI":
        vis_min, vis_max = -0.2, 0.8
        vis_palette = ['#8b4513','#d2b48c','#ffff00','#9acd32','#006400']
        legend_labels = ['Bare Soil', 'Low Veg', 'Moderate', 'Healthy', 'Dense Veg']
        thresholds = [-1, 0.1, 0.3, 0.5, 0.7, 1.0] 
    elif index_option == "NDMI":
        vis_min, vis_max = -0.2, 0.6
        vis_palette = ['#ff0000', '#ff8c00', '#ffff00', '#00ffff', '#0000ff'] 
        legend_labels = ['Very Low Moisture', 'Low', 'Moderate', 'High', 'Very High']
        thresholds = [-1, -0.1, 0.1, 0.3, 0.5, 1.0]
    elif index_option == "EVI":
        vis_min, vis_max = 0, 1.0
        vis_palette = ['#ffffff', '#ce7e45', '#df923d', '#f1b555', '#fcd163', '#99b718', '#74a901', '#66a000', '#529400', '#3e8601', '#207401', '#056201', '#012e01', '#011d01', '#011301']
        legend_labels = ['Barren', 'Sparse', 'Low', 'Moderate', 'High']
        thresholds = [-1, 0.2, 0.4, 0.6, 0.8, 2.0]

    # ============================================================
    # SINGLE SATELLITE MAP (FOLIUM)
    # ============================================================
    m = folium.Map(
        location=[22.5, 88.3],
        zoom_start=6,
        tiles="Esri.WorldImagery",
        attr="ESRI"
    )

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        attr="ESRI Labels",
        name="Labels",
        overlay=True,
        control=False
    ).add_to(m)

    Draw(export=True).add_to(m)

    aoi = None
    aoi_bounds = None

    # ============================================================
    # GEOJSON UPLOAD
    # ============================================================
    if uploaded:
        aoi, aoi_bounds = read_geojson(uploaded)

    # ============================================================
    # MAP DISPLAY SETUP
    # ============================================================
    map_data = st_folium(m, height=420, use_container_width=True)

    if map_data and map_data.get("last_active_drawing"):
        aoi = ee.Geometry(map_data["last_active_drawing"]["geometry"])

    if not aoi:
        st.info("Upload GeoJSON or draw AOI to continue")
        st.stop()

    # ============================================================
    # AUTO-ZOOM TO AOI
    # ============================================================
    if aoi_bounds:
        minx, miny, maxx, maxy = aoi_bounds
        m.fit_bounds([[miny, minx], [maxy, maxx]])

    # ============================================================
    # PROCESSING
    # ============================================================
    # 1. Get Base Collection
    collection = get_s2_collection(aoi, ee_date(start), ee_date(end), cloud)

    # 2. Add Index Band
    def add_index_band(img):
        idx = calculate_index(img, index_option)
        return img.addBands(idx)

    collection_with_index = collection.map(add_index_band)

    # 3. Create Median Composite
    img_median = collection_with_index.median().clip(aoi)
    index_median = img_median.select(index_option)

    # ============================================================
    # FULL COVERAGE FILTERING LOGIC
    # ============================================================
    st.write("🔄 Checking image coverage (this may take a moment)...")

    # Calculate total AOI area in square meters
    aoi_area = aoi.area()

    # Function to check coverage for a specific date
    def check_coverage(date_str):
        d_start = ee.Date(date_str)
        d_end = d_start.advance(1, 'day')
        
        # Mosaic all tiles for this day
        daily_mosaic = collection_with_index.filterDate(d_start, d_end).mosaic().clip(aoi).select(index_option)
        
        # Calculate the area of valid pixels (pixels that are not null)
        # We use a mask where the index is not null
        valid_mask = daily_mosaic.mask().select(0)
        
        # Calculate area of valid pixels
        stats = valid_mask.multiply(ee.Image.pixelArea()).reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=aoi,
            scale=20,  # 20m scale for speed/accuracy trade-off
            maxPixels=1e13
        )
        
        valid_area = stats.get(index_option)
        
        # Return true if coverage is > 98%
        # We use ee.Algorithms.If to return 1 or 0
        return ee.Algorithms.If(
            ee.Number(valid_area).divide(aoi_area).gt(0.98), 
            1, 
            0
        )

    # Get list of unique dates first
    raw_dates_fc = collection.aggregate_array("system:time_start")
    raw_dates = raw_dates_fc.map(lambda t: ee.Date(t).format("YYYY-MM-dd")).distinct().getInfo()

    valid_dates = []

    # We perform the check. For very large date ranges, this loop might be slow.
    # We optimize by checking only the dates returned by the initial query.
    progress_bar = st.progress(0)
    for i, d in enumerate(raw_dates):
        # Update progress
        progress_bar.progress((i + 1) / len(raw_dates))
        
        # Check coverage server-side
        is_covered = check_coverage(d).getInfo()
        if is_covered == 1:
            valid_dates.append(d)

    progress_bar.empty()

    if not valid_dates:
        st.warning("No images found with 100% coverage for the selected period/cloud cover.")
    else:
        st.success(f"Found {len(valid_dates)} dates with full AOI coverage.")

    # ============================================================
    # MAP LAYERS (REFRESHED MAP)
    # ============================================================
    st.subheader(f"🗺️ {index_option} Analysis Map (Median Composite)")

    m_res = folium.Map(tiles="Esri.WorldImagery", attr="ESRI")
    if aoi_bounds:
        m_res.fit_bounds([[miny, minx], [maxy, maxx]])
    else:
        bounds = aoi.bounds().getInfo()["coordinates"][0]
        m_res.fit_bounds([
            [bounds[0][1], bounds[0][0]],
            [bounds[2][1], bounds[2][0]]
        ])

    m_res.add_ee_layer(
        img_median.select(["B4", "B3", "B2"]),
        {"min": 0, "max": 0.3},
        "True Color"
    )

    m_res.add_ee_layer(
        index_median,
        {"min": vis_min, "max": vis_max, "palette": vis_palette},
        index_option
    )

    legend_html = f'''
        <div style="position: fixed; 
        bottom: 50px; left: 50px; width: 150px; height: auto; 
        border:2px solid grey; z-index:9999; font-size:14px;
        background-color:white; opacity: 0.9;">
        &nbsp; <b>{index_option} Legend</b> <br>
        '''
    for i in range(len(legend_labels)):
        color = vis_palette[min(i, len(vis_palette)-1)]
        legend_html += f'&nbsp; <i style="background:{color};width:10px;height:10px;display:inline-block;"></i>&nbsp; {legend_labels[i]}<br>'
    legend_html += "</div>"
    m_res.get_root().html.add_child(folium.Element(legend_html))

    folium.LayerControl(collapsed=False).add_to(m_res)
    st_folium(m_res, height=420, use_container_width=True)

    # ============================================================
    # TIME-SERIES
    # ============================================================
    if show_ts and valid_dates:
        st.subheader(f" {index_option} Trend (Fully Covered Dates Only)")
        
        # We only compute time series for the VALID dates
        def get_valid_ts_point(d_str):
            d_start = ee.Date(d_str)
            d_end = d_start.advance(1, 'day')
            img = collection_with_index.filterDate(d_start, d_end).mosaic().clip(aoi).select(index_option)
            
            val = img.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=aoi,
                scale=20,
                maxPixels=1e13
            ).get(index_option)
            return {"date": d_str, "value": val}

        # Since we already filtered dates, we can just loop through valid_dates
        # This might be slower than map(), but it ensures we respect the coverage filter
        ts_data = []
        for d in valid_dates:
            pt = get_valid_ts_point(d)
            val = pt["value"].getInfo()
            if val is not None:
                ts_data.append({"date": d, "value": val})

        ts_df = pd.DataFrame(ts_data)
        if not ts_df.empty:
            ts_df["date"] = pd.to_datetime(ts_df["date"])
            ts_df = ts_df.sort_values("date")
            st.line_chart(ts_df.set_index("date")["value"], height=300, use_container_width=True)

    # ============================================================
    # STATISTICAL ANALYSIS SECTION
    # ============================================================
    st.subheader(f"📊 {index_option} Detailed Statistical Analysis")

    col_stat1, col_stat2 = st.columns([1, 1])

    with col_stat1:
        # ONLY VALID DATES ARE SHOWN HERE
        stat_date_options = ["Median Composite"] + valid_dates
        selected_stat_date = st.selectbox("Select Date for Analysis", stat_date_options)

    # Helper to get the correct image (Median or Mosaicked Date)
    def get_target_image(sel_date):
        if sel_date == "Median Composite":
            return index_median, f"Median {index_option}"
        else:
            d_start = ee.Date(sel_date)
            d_end = d_start.advance(1, 'day')
            # Mosaic ensures we use the full coverage image we validated earlier
            daily_col = collection_with_index.filterDate(d_start, d_end)
            return daily_col.mosaic().clip(aoi).select(index_option), f"{index_option} ({sel_date})"

    target_stat_img, stat_title = get_target_image(selected_stat_date)
    # 2. Basic Stats
    basic_stats = target_stat_img.reduceRegion(
            reducer=ee.Reducer.mean().combine(ee.Reducer.minMax(), "", True).combine(ee.Reducer.stdDev(), "", True),
            geometry=aoi,
            scale=20,
            maxPixels=1e13
        ).getInfo()

    if authentication_status:

        # Display Basic Stats
        st.markdown("#### 1. Descriptive Statistics")
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        col_m1.metric("Mean", f"{basic_stats.get(index_option+'_mean', 0):.3f}")
        col_m2.metric("Min", f"{basic_stats.get(index_option+'_min', 0):.3f}")
        col_m3.metric("Max", f"{basic_stats.get(index_option+'_max', 0):.3f}")
        col_m4.metric("Std Dev", f"{basic_stats.get(index_option+'_stdDev', 0):.3f}")

        # ============================================================
        # 2. Vegetation Class Coverage (No Histogram Version)
        # ============================================================
        st.markdown("#### 2. Vegetation Class Coverage")

        try:
            pixel_area = ee.Image.pixelArea().divide(10000)  # hectares
            
            class_areas = []
            
            for i in range(len(legend_labels)):
                lower = thresholds[i]
                upper = thresholds[i+1]
                
                class_mask = target_stat_img.gte(lower).And(target_stat_img.lt(upper))
                
                area = pixel_area.updateMask(class_mask).reduceRegion(
                    reducer=ee.Reducer.sum(),
                    geometry=aoi,
                    scale=20,
                    maxPixels=1e13
                ).getInfo()
                
                area_ha = area.get("area", 0) if area else 0
                
                class_areas.append({
                    "Class": legend_labels[i],
                    "Area (Ha)": round(area_ha, 2),
                    "Color": vis_palette[i]
                })

            class_df = pd.DataFrame(class_areas)
            st.dataframe(class_df.style.applymap(
                lambda x: f"background-color: {x}" if x in vis_palette else "",
                subset=["Color"]
            ), use_container_width=True)

        except Exception as e:
            st.error(f"Could not calculate class area stats: {e}")

        # ============================================================
        # PNG EXPORT
        # ============================================================
        st.subheader("🖼️ Publication-Ready Map Generator")

        col_sel1, col_sel2 = st.columns([1, 1])

        with col_sel1:
            # ONLY VALID DATES
            date_options = ["Median Composite"] + valid_dates
            selected_map_date = st.selectbox("Select Image Date for Map", date_options)

        with col_sel2:
            st.write("")
            st.info("Generates a high-res PNG. Only showing dates with >98% AOI coverage.")

        if st.button("Generate PNG Map"):
            
            target_image, map_title = get_target_image(selected_map_date)

            try:
                thumb = target_image.getThumbURL({
                    "min": vis_min,
                    "max": vis_max,
                    "palette": vis_palette,
                    "dimensions": 1000,
                    "region": aoi,
                    "format": "png"
                })

                image = Image.open(BytesIO(requests.get(thumb).content))
                
                bounds = aoi.bounds().getInfo()["coordinates"][0]
                lons = [c[0] for c in bounds]
                lats = [c[1] for c in bounds]

                min_lon, max_lon = min(lons), max(lons)
                min_lat, max_lat = min(lats), max(lats)

        # ---- ZOOM FACTOR (Change this value) ----
                # ---- WHITE GAP MARGIN (Creates outer space) ----
                margin_percent = 0.15   # Increase for bigger gap (0.10 small, 0.20 big)

                lon_margin = (max_lon - min_lon) * margin_percent
                lat_margin = (max_lat - min_lat) * margin_percent

                outer_min_lon = min_lon - lon_margin
                outer_max_lon = max_lon + lon_margin
                outer_min_lat = min_lat - lat_margin
                outer_max_lat = max_lat + lat_margin



                fig, ax = plt.subplots(figsize=(10, 10), dpi=150)
                
                ax.imshow(
                    image, 
                    extent=[min_lon, max_lon, min_lat, max_lat],
                    aspect='auto', 
                    interpolation='nearest'
                )
                # Expand axis to create white gap
                ax.set_xlim(outer_min_lon, outer_max_lon)
                ax.set_ylim(outer_min_lat, outer_max_lat)

                ax.set_title(map_title, fontsize=14, fontweight="bold")
                ax.set_xlabel("Longitude")
                ax.set_ylabel("Latitude")

                ax.tick_params(
                    top=True, bottom=True, left=True, right=True,
                    labeltop=True, labelbottom=True,
                    labelleft=True, labelright=True
                )

                ax.xaxis.set_major_formatter(FormatStrFormatter('%.2f'))
                ax.yaxis.set_major_formatter(FormatStrFormatter('%.2f'))
                ax.grid(True, linestyle="--", alpha=0.5)

                patches = [
                    mpatches.Patch(color=vis_palette[min(i, len(vis_palette)-1)], label=legend_labels[i])
                    for i in range(len(legend_labels))
                ]
                ax.legend(
                    handles=patches,
                    title=f"{index_option} Classes",
                    loc="lower right",
                    frameon=True,
                    facecolor="white"
                )

                buf = BytesIO()
                plt.savefig(buf, format="PNG", bbox_inches="tight")
                st.image(buf)
                st.download_button(f"Download {index_option} Map", buf.getvalue(), f"{index_option}_{selected_map_date}.png", "image/png")
            
            except Exception as e:
                st.error(f"Error generating map: {e}")
                # ============================================================
        # TIFF EXPORT (DATE-WISE LIKE PNG)
        # ============================================================
        st.subheader("⬇️ Export GeoTIFF")

        # Use SAME selected image as PNG
        export_image, export_title = get_target_image(selected_map_date)

        # Clean date text for filename
        if selected_map_date == "Median Composite":
            export_date_tag = "MEDIAN"
        else:
            export_date_tag = selected_map_date.replace("-", "")

        area_km2 = aoi.area().divide(1e6).getInfo()
        st.write(f"AOI Area: **{area_km2:.2f} km²**")

        # ------------------------------------------------------------
        # SMALL AOI → SINGLE FILE
        # ------------------------------------------------------------
        if area_km2 < 200:

            url = export_image.getDownloadURL({
                "scale": 10,
                "region": aoi,
                "crs": "EPSG:4326",
                "format": "GEO_TIFF",
                "filePerBand": False
            })

            filename = f"{index_option}_{export_date_tag}.tif"

            st.success("✅ Single-file export available")
            st.markdown(f"👉 [{filename}]({url})")

        # ------------------------------------------------------------
        # LARGE AOI → DATE-WISE TILE EXPORT
        # ------------------------------------------------------------
        else:
            st.warning("⚠️ Large AOI detected — exporting tiles")

            def export_tiled(image, region, date_tag, scale=10, max_tile_km2=100):

                bounds = region.bounds().getInfo()["coordinates"][0]
                minx, miny = bounds[0]
                maxx, maxy = bounds[2]

                total_area = region.area().divide(1e6).getInfo()
                grid_size = math.ceil(math.sqrt(total_area / max_tile_km2))

                dx = (maxx - minx) / grid_size
                dy = (maxy - miny) / grid_size

                urls = []

                for i in range(grid_size):
                    for j in range(grid_size):

                        tile = ee.Geometry.Rectangle([
                            minx + i * dx,
                            miny + j * dy,
                            minx + (i + 1) * dx,
                            miny + (j + 1) * dy
                        ])

                        try:
                            url = image.clip(tile).getDownloadURL({
                                "scale": scale,
                                "region": tile,
                                "crs": "EPSG:4326",
                                "format": "GEO_TIFF",
                                "filePerBand": False
                            })

                            filename = f"{index_option}_{date_tag}_Tile_{i+1}-{j+1}.tif"

                            urls.append((filename, url))

                        except Exception:
                            pass

                return urls

            tiles = export_tiled(export_image, aoi, export_date_tag)

            if tiles:
                st.success(f"✅ {len(tiles)} tiles generated")
                for fname, link in tiles:
                    st.markdown(f"🔹 [{fname}]({link})")

elif authentication_status == False:
    st.error("Incorrect username or password")

elif authentication_status == None:
    st.warning("Please enter login credentials")
